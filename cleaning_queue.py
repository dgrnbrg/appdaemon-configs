import math
import pytz
import numpy as np
import pandas as pd
import hassapi as hass
import adbase as ad
import datetime
import influx
from collections import defaultdict
from pprint import pprint


def parse_conditional_expr(cause):
    """
    Copied from lights.py
    """
    present_state = 'on'
    absent_state = 'off'
    entity = cause
    if '==' in cause:
        xs = [x.strip() for x in cause.split('==')]
        #print(f"parsing a state override light trigger {xs}")
        entity = xs[0]
        present_state = xs[1]
        absent_state = None
    elif '!=' in cause:
        xs = [x.strip() for x in cause.split('!=')]
        #print(f"parsing a negative state override light trigger")
        entity = xs[0]
        present_state = None
        absent_state = xs[1]
    elif ' not in ' in cause:
        xs = [x.strip() for x in cause.split(' not in ')]
        entity = xs[0]
        present_state = None
        absent_state = [x.strip() for x in xs[1].strip('[]').split(',')]
    elif ' in ' in cause:
        xs = [x.strip() for x in cause.split(' in ')]
        entity = xs[0]
        present_state = [x.strip() for x in xs[1].strip('[]').split(',')]
        absent_state = None
    return present_state, absent_state, entity


class CleaningManager(hass.Hass):
    def initialize(self):
        self.debug_enabled = self.args.get('debug', False)
        self.ready_service_args = None
        self.vacuum = self.args['vacuum']
        self.vacuum_map = self.args['vacuum_map']
        self.pending_actions = [] # list of maps that describe pending cleaning actions
        self.areas = self.args['areas']
        self.home_area = None
        for area,cfg in self.areas.items():
            if cfg.get('home'):
                if self.home_area is not None:
                    raise ValueError(f"Should only be one home area")
                self.home_area = area
        self.sensor_listen_tokens = []
        for src_area, cfgs in self.args['pathways'].items():
            if self.debug_enabled:
                self.log(f"processing pathway from {src_area}, cfg={cfgs}")
            for dest_cfg in cfgs:
                if isinstance(dest_cfg, str):
                    dest_area = dest_cfg
                    dest_cfg = {'area': dest_area, 'always_open': True}
                else:
                    dest_area = dest_cfg['area']
                    del dest_cfg['area']
                    dest_cfg['always_open'] = False
                    dest_cfg['before_coord'] = tuple(dest_cfg['before_coord'])
                    dest_cfg['after_coord'] = tuple(dest_cfg['after_coord'])
                if self.debug_enabled:
                    self.log(f"processing pathway from {src_area} to {dest_area} with cfg={dest_cfg}")
                src_conns = self.areas[src_area].get('connections', {})
                if dest_area in src_conns:
                    raise ValueError(f"{dest_area} already in {list(src_conns.keys())}")
                src_conns[dest_area] = dest_cfg.copy()
                self.areas[src_area]['connections'] = src_conns
                dest_conns = self.areas[dest_area].get('connections', {})
                if src_area in dest_conns:
                    raise ValueError(f"{dest_area} already in {list(src_conns.keys())}")
                if 'before_coord' in dest_cfg:
                    tmp = dest_cfg['before_coord']
                    dest_cfg['before_coord'] = dest_cfg['after_coord']
                    dest_cfg['after_coord'] = tmp
                dest_conns[src_area] = dest_cfg
                self.areas[dest_area]['connections'] = dest_conns
        # compute "openings_from_home" for all areas. This is needed to be practical about cleaning past sometimes-opened doors
        self.areas[self.home_area]['openings_from_home'] = 0
        seen = set()
        worklist = [self.home_area]
        openings = set()
        while worklist:
            cur = worklist.pop(0)
            seen.add(cur)
            cur_cfg = self.areas[cur]
            conns = cur_cfg.get('connections', {})
            for dest,conn in conns.items():
                if dest not in seen:
                    if conn['always_open']:
                        self.areas[dest]['openings_from_home'] = cur_cfg['openings_from_home']
                    else:
                        self.areas[dest]['openings_from_home'] = cur_cfg['openings_from_home'] + 1
                    if 'opening' in conn:
                        openings.add(conn['opening'])
                    worklist.append(dest)
        if self.debug_enabled:
            pprint(self.areas)
        # try to reschedule as soon as a door is opened for a bit
        for opening in openings:
            self.listen_state(self.schedule_on_state_change, opening, duration=15)
        # validate we have full reachability for all areas
        areas_not_connected = []
        for area, cfg in self.areas.items():
            if 'openings_from_home' not in cfg:
                areas_not_connected.append(area)
        if areas_not_connected:
            raise ValueError(f"The following areas don't have pathways configured correctly: {areas_not_connected}")
        # Start looking to clean
        runtime = datetime.time(0, 0, 0)
        self.listen_event(self.clean_event_cb, "cleaner.clean_area")
        self.run_minutely(self.next_job, runtime)

    def clean_area(self, area, custom_args):
        action = {}
        action['area'] = area
        action['args'] = custom_args
        if self.debug_enabled:
            self.log(f"Enqueuing clean area: {action}")
        self.pending_actions.append(action)

    def clean_event_cb(self, event_name, data, kwargs):
        self.clean_area(data['area'], data.get('args', {}))
        # Immediately try scheduling
        self.next_job({})

    def get_directly_connected_set(self, area, include_currently_open=False, min_openings_from_home=0):
        connected = set()
        seen = set()
        connected.add(area)
        seen.add(area)
        worklist = [area]
        while worklist:
            cur = worklist.pop(0)
            for dst_area, cfg in self.areas[cur].get('connections', {}).items():
                now_open = False
                if include_currently_open and 'opening' in cfg:
                    now_open = self.get_state(cfg['opening']) == 'on'
                if self.areas[dst_area]['openings_from_home'] >= min_openings_from_home and cfg['always_open'] or now_open:
                    connected.add(dst_area)
                    if dst_area not in seen:
                        worklist.append(dst_area)
                seen.add(dst_area)
        return connected

    def is_zone(self, area):
        return 'zone' in self.areas[area]

    def vacuum_close_to(self, coord, distance = 150):
        cur_pos = self.get_state(self.vacuum_map, attribute='vacuum_position')
        tx,ty = coord
        cx,cy = (cur_pos['x'], cur_pos['y'])
        dist = math.sqrt((tx-cx)**2 + (ty-cy)**2)
        if self.debug_enabled:
            self.log(f"close to check: current={(cx,cy)} target={(tx,ty)} dist={dist}")
        return dist < distance

    def find_path_between(self, start, end):
        predecessors = {}
        seen = set()
        seen.add(start)
        worklist = [start]
        while worklist and end not in seen:
            cur = worklist.pop(0)
            for dst_area, cfg in self.areas[cur].get('connections', {}).items():
                now_open = False
                if dst_area not in predecessors:
                    predecessors[dst_area] = cur
                if dst_area not in seen:
                    worklist.append(dst_area)
                seen.add(dst_area)
        path = [end]
        while start not in path:
            path.append(predecessors[path[-1]])
        if self.debug_enabled:
            self.log(f"computed path from {start} to {end}: {path[::-1]}")
        return path[::-1]

    def schedule_on_state_change(self, entity, attribute, old, new, kwargs):
        self.next_job({})

    def next_job(self, kwargs):
        if self.ready_service_args:
            self.log(f"not trying to schedule because we've already got a job running")
            return
        if self.get_state(self.vacuum) not in ['docked', 'idle']:
            self.log(f"not trying to schedule because we're doing something now")
            return
        if not self.pending_actions:
            self.log(f"not trying to schedule b/c no pending actions")
            return
        self.log(f"Starting to compute next job")
        min_openings_from_home = 0
        # First, check if we're right by something's before_coord
        for area, cfg in self.areas.items():
            for dst_area,dst_cfg in cfg.get('connections',{}).items():
                # If we are, we should try to clean rooms at least that many openings from home
                if 'before_coord' in dst_cfg and self.vacuum_close_to(dst_cfg['before_coord']):
                    min_openings_from_home = max(min_openings_from_home, self.areas[dst_area]['openings_from_home'])
        # We'll prefer cleaning that won't require asking for help to open a door
        current_area_id = self.get_state(self.vacuum_map, attribute='vacuum_room')
        current_area = self.home_area # default to home area if we don't localize
        for area, cfg in self.areas.items():
            if 'id' in cfg and current_area_id == cfg['id']:
                current_area = area
        self.log(f"Vacuum in {current_area} after localizing, looking for areas at least {min_openings_from_home} openings from home away")
        areas_accessible_from_current = self.get_directly_connected_set(current_area, include_currently_open=True, min_openings_from_home=min_openings_from_home)
        preferred_pending_actions = [a for a in self.pending_actions if a['area'] in areas_accessible_from_current]
        if self.debug_enabled:
            self.log(f"first attempt for preferred pending: {preferred_pending_actions} based on accessible area list: {areas_accessible_from_current}")
        if not preferred_pending_actions: # nothing is readily accessible
            coords_and_votes = defaultdict(lambda: 0)
            coords_to_transition = {}
            # here we find the before_coord for a pending area that is directly accessible. Then just head there.
            pending_areas = [x['area'] for x in self.pending_actions]
            for pending_area in pending_areas:
                area_path = self.find_path_between(current_area, pending_area)
                for i,area in enumerate(area_path[:-1]):
                    cfg = self.areas[area]
                    next_area = area_path[i+1]
                    conn = cfg['connections'][next_area]
                    if 'opening' in conn:
                        if self.get_state(conn['opening']) != 'on': # not currently open
                            coords_and_votes[conn['before_coord']] += 1
                            coords_to_transition[conn['before_coord']] = (area, next_area)
            self.log(f"coords_and_votes = {coords_and_votes}; coords_to_transition = {coords_to_transition}")
            pprint(coords_and_votes)
            target_coords = max(coords_and_votes, key=lambda k: coords_and_votes[k])
            #self.log(f"self.call_service('roborock/vacuum_goto', x_coord={target_coords[0]}, y_coord={target_coords[1]})")
            area, next_area = coords_to_transition[target_coords]
            if self.vacuum_close_to(target_coords):
                self.log(f"vacuum already close to {target_coords} (to go from {area} to {next_area}), so holding tight")
            else:
                self.call_service('roborock/vacuum_goto', x_coord=target_coords[0], y_coord=target_coords[1], entity_id= self.vacuum)
                self.log(f"sending vacuum to the opening between {area} and {next_area} to wait")
            return
        init_action = preferred_pending_actions.pop(0)
        next_actions = [init_action]
        merge_candidate_areas = self.get_directly_connected_set(init_action['area'], include_currently_open=True, min_openings_from_home=min_openings_from_home)
        for action in self.pending_actions:
            if (action['area'] in merge_candidate_areas # is freely connected, for no prompting/assistance
                and action['args'] == init_action['args'] # same configuration
                and self.is_zone(action['area']) == self.is_zone(init_action['area']) # ensure cleanable in the same API call (zones or rooms)
                and action not in next_actions): # we don't handle duplicates b/c it's easier
                next_actions.append(action)
        for action in next_actions:
            self.pending_actions.remove(action)
        self.log(f"Next actions:")
        pprint(next_actions)
        if self.is_zone(init_action['area']):
            self.do_room_cleaning([a['area'] for a in next_actions], target_key='zone', service='roborock/vacuum_clean_zone', service_arg='zone', **init_action['args'])
        else: # rooms
            self.do_room_cleaning([a['area'] for a in next_actions], target_key='id', service='roborock/vacuum_clean_segment', service_arg='segments', **init_action['args'])

    def stop_sensor_listening(self):
        for token in self.sensor_listen_tokens:
            self.cancel_listen_state(token)
        self.sensor_listen_tokens = []
        self.sensor_states = {}

    def sensor_state_changed(self, entity, attribute, old, new, kwargs):
        if new in ['unknown', 'unavailable']:
            new = 'off'
        self.sensor_states[entity] = new
        self.clean_if_ready()

    def clean_if_ready(self):
        waiting_for_sensors = []
        all_off = True
        for k,v in self.sensor_states.items():
            if v != 'off':
                all_off = False
                waiting_for_sensors.append(k)
        if all_off:
            # everything is unoccupied
            self.log(f"starting clean: {self.ready_service_args}")
            self.call_service(**self.ready_service_args)
            self.stop_sensor_listening()
            # now that the cleaning started, we need to wait until it's done, then clear the ready_service_args
            self.vacuum_listen_token = self.listen_state(self.vacuum_state_changed, self.vacuum, attribute='status')
        else:
            self.log(f"Waiting for sensors: {waiting_for_sensors}")

    def vacuum_state_changed(self, entity, attribute, old, new, kwargs):
        # statuses seen: 'washing_the_map', 'idle', 'segment_cleaning', 'going_to_wash_the_mop', 'washing_the_mop', 'zoned_cleaning'
        if self.debug_enabled:
            self.log(f"observed state change for {entity} from {old} to {new}")
        # TODO looks like we want a 3 state track of 'returning_home' -> 'emptying_the_bin' -> 'charging'
        if old == 'emptying_the_bin' and new == 'charging':
            if self.debug_enabled:
                self.log(f"Finished cleaning and returning to dock, job is done")
            self.ready_service_args = None
            self.cancel_listen_state(self.vacuum_listen_token)

    def do_room_cleaning(self, rooms, repeats=None, target_key='id', service='roborock/vacuum_clean_segment', service_arg='segments'):
        presence_sensors = [] # wait for rooms to seem empty
        ids = [] # things to clean
        for room in rooms:
            cfg = self.areas[room]
            presence = cfg.get('presence', [])
            if not isinstance(presence, list):
                presence = [presence]
            presence_sensors.extend(presence)
            ids.append(cfg[target_key])
        self.stop_sensor_listening()
        # build the service call to do the work
        self.ready_service_args = {
            'service': service,
            service_arg: ids,
            'entity_id': self.vacuum,
        }
        if repeats is not None:
            self.ready_service_args['repeats'] = repeats
        self.log(f'Waiting for {set(presence_sensors)} to be all off to start the cleaning job {self.ready_service_args}')
        # start listening for sensors to be ready
        for sensor in set(presence_sensors):
            self.sensor_states[sensor] = self.get_state(sensor)
            token = self.listen_state(self.sensor_state_changed, sensor)
            self.sensor_listen_tokens.append(token)
        self.clean_if_ready()
