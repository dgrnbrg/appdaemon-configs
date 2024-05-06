import hassapi as hass
import base64
import adbase as ad
from collections import defaultdict
import pandas as pd
from Crypto.Cipher import AES
import dateutil.parser as du
from datetime import timedelta, datetime, time
import os
from glob import glob
from collections import defaultdict
import numpy as np


tracker_log_loc = '/config/appdaemon/tracker_logs/'
tracker_log_rows_per_flush = 100


class IrkTracker(hass.Hass):
    def initialize(self):
        self.known_addr_cache = {}
        self.room_aliases = self.args.get('room_aliases', {})
        self.room_presence = self.args.get('room_presence', {})
        for room, alias in self.room_aliases.items():
            if 'secondary_clarifiers' in alias:
                clarifiers = {}
                for c in alias['secondary_clarifiers']:
                    if isinstance(c, str):
                        clarifiers[c] = c
                    else: # support resolving to an alternative room (e.g. when between floors)
                        k,v = list(c.items())[0]
                        clarifiers[k] = v
                alias['secondary_clarifiers'] = clarifiers
        self.log(f"processed room aliases: {self.room_aliases}")
        self.valid_rooms = set()
        self.device_in_room = defaultdict(lambda: 'unknown')
        self.active_device_by_person = defaultdict(lambda: 'unknown')
        self.expiry_timers = {}
        self.tracking_window = timedelta(minutes=int(self.args.get('tracking_window_minutes', 3)))
        self.min_superplurality = self.args.get('tracking_min_superplurality',1.0)
        self.rssi_adjustments = self.args.get('rssi_adjustments',{})
        self.ping_halflife_seconds = self.args.get('ping_halflife_seconds', 60)
        self.log(f'rssi_adjustments = {self.rssi_adjustments}')
        self.away_tracker_pending_arrivals = {}
        for room in self.room_aliases.values():
            if isinstance(room, str):
                self.valid_rooms.add(room)
            elif 'default' in room:
                self.valid_rooms.add(room['default'])
            elif 'secondary_clarifiers' in room:
                for r in room['secondary_clarifiers'].values():
                    self.valid_rooms.add(r)
        self.identities = {x['device_name']: x for x in self.args['identities']}
        self.ciphers = {}
        self.people = set(x['person'] for x in self.args['identities'])
        irk_prefilters = [] # will be a list of base64 encoded IRKs
        for identity, data in self.identities.items():
            byte_form = bytearray.fromhex(data['irk'])
            irk_prefilters.append(base64.b64encode(byte_form).decode("ascii"))
            self.ciphers[identity] = AES.new(byte_form, AES.MODE_ECB)
            device_clean_name = identity.replace(" ", "_")
            device_ent = self.get_entity(f'device_tracker.{device_clean_name}_irk')
            if device_ent.exists():
                self.device_in_room[identity] = device_ent.get_state(attribute='room')
            person = data['person']
            force_primary_button = f"button.irk_tracker_make_primary_{person}_{device_clean_name}"
            self.get_entity(force_primary_button).set_state(state='unknown', attributes={'friendly_name': f"Set {identity} as the primary device for {person}", 'person': person, 'device': identity})
        def filter_make_primary_entity(x):
            entity = x.get('entity_id')
            if isinstance(entity, str):
                return entity.startswith('button.irk_tracker_make_primary_')
            return False
        self.listen_event(self.make_primary_cb, "call_service", domain="button", service="press", service_data=filter_make_primary_entity)
        if 'data_loc' in self.args:
            self.data_loc = f"/config/appdaemon/{self.args['data_loc']}"
        else:
            self.data_loc = tracker_log_loc
        if 'rows_per_flush' in self.args:
            self.rows_per_flush = int(self.args['rows_per_flush'])
        else:
            self.rows_per_flush = tracker_log_rows_per_flush
        all_examples = glob(f"{self.data_loc}examples*.csv")
        if len(all_examples) > 0:
            self.log(f"found {len(all_examples)} preexisting examples, fitting model...")
            #self.fit_model(None, {}, {})
            self.log("model fit")
        else:
            self.knn = None
        self.fused_trackers = {}
        for cfg in self.args['away_trackers']:
            #self.log(f"configuring fused trackers {cfg}")
            person = cfg['person']
            fused_tracker = cfg['home_focused_tracker']
            self.fused_trackers[person] = fused_tracker
        for person in self.people:
            person_ent = self.get_entity(f'device_tracker.{person}_irk')
            if person in self.fused_trackers:
                init_state = self.get_state(self.fused_trackers[person])
            else:
                init_state = 'unknown'
            person_ent.set_state(state=init_state, attributes={'from_device': 'init'})
            fused_override_select = f"select.irk_tracker_fused_override_{person}"
            self.get_entity(fused_override_select).set_state(state='unknown', attributes={'friendly_name': f"Override {person} home/away", 'person': person, 'options': ['home', 'away', 'just_arrived', 'just_left']})
        for cfg in self.args['away_trackers']:
            #self.log(f"initializing fused tracker {cfg}")
            person = cfg['person']
            tracker = cfg['tracker']
            init_fused_state = 'home' if self.get_state(tracker) == 'home' else 'away'
            self.set_person_fused_tracker_state(person, init_fused_state, 'init')
            self.listen_state(self.away_tracker_cb, tracker, person=person)
        def filter_override_fused(x):
            entity = x.get('entity_id')
            if isinstance(entity, str):
                return entity.startswith('select.irk_tracker_fused_override_')
            return False
        self.listen_event(self.override_fused_cb, "call_service", domain="select", service="select_option", service_data=filter_override_fused)
        for pullout_sensor in self.args.get('pullout_sensors', []):
            entity = pullout_sensor['entity']
            from_state = pullout_sensor['from']
            to_state = pullout_sensor['to']
            nearest_beacons = pullout_sensor['nearest_beacons']
            self.listen_state(self.pullout_sensor_cb, entity, cfg=pullout_sensor)#, old=from_state, new=to_state, nearest_beacons=nearest_beacons)
        self.listen_event(self.ble_tracker_cb, "esphome.ble_tracking_beacon", addr=lambda addr: self.known_addr_cache.get(addr,None) != 'none')
        self.recording_df = None
        self.listen_event(self.start_recording, "irk_tracker.start_recording")
        self.listen_event(self.stop_recording, "irk_tracker.stop_recording")
        self.recent_observations = defaultdict(lambda: [])
        self.get_entity('sensor.irk_prefilter').set_state(state=':'.join(irk_prefilters) + ':') # TODO the filtering code on the esp requires a trailing colon
        self.init_time = datetime.now()

    def flush_recording(self):
        df = pd.DataFrame(self.recording_df)
        df['tag'] = self.recording_tag
        save_path = self.data_loc + f"examples-{self.recording_tag}-{self.recording_index}.csv"
        self.log(f'recording data to {save_path}')
        df.to_csv(save_path, index=False, header=True)
        self.recording_index += 1
        self.recording_df = {'time':[], 'device':[], 'source':[], 'rssi':[]}

    def start_recording(self, event_name, data, kwargs):
        input_irk_tag_ent = self.get_entity(self.args['training_input_text'])
        tag = input_irk_tag_ent.get_state('state')
        os.makedirs(self.data_loc, exist_ok = True)
        # delete all files in dir startig with self.data_loc+"examples-"+tag for clean train
        for f in glob(f"{self.data_loc}examples-{tag}*.csv"):
            os.unlink(f)
        self.log(f"started recording with tag {tag}")
        self.recording_df = {'time':[], 'device':[], 'source':[], 'rssi':[]}
        self.recording_index = 0
        self.recording_tag = tag

    def stop_recording(self, event_name, data, kwargs):
        if self.recording_df is not None:
            self.flush_recording()
            self.log(f"stopped recording for {self.recording_tag}")
            self.recording_df = None

    @ad.app_lock
    def pullout_sensor_cb(self, entity, attr, old, new, kwargs):
        #self.log(f"pullout: {entity} went from {old} to {new}")
        f = kwargs['cfg']['from']
        t = kwargs['cfg']['to']
        if old != f or new != t:
            self.log(f"didn't match pullout: {old} != {f} or {new} != {t}")
            return
        within_top = kwargs['cfg'].get('within_top', 1)
        nearest_beacons = kwargs['cfg']['nearest_beacons']
        for person in self.people:
            active_device = self.active_device_by_person[person]
            device_ent = self.get_entity(f'device_tracker.{active_device.replace(" ", "_")}_irk')
            weighted_votes = device_ent.get_state(attribute='weighted_votes')
            if weighted_votes:
                self.log(f"pullout for {person} (looking at top {within_top}): weighted votes are {weighted_votes}")
                # lowest rssi * superplurality
                rssi_limit = weighted_votes[0][0] * self.min_superplurality
                for i in range(within_top):
                    if weighted_votes[i][2] in nearest_beacons and weighted_votes[i][0] <= rssi_limit:
                        self.set_person_fused_tracker_state(person, 'just_left', device_ent)
                        # Now, this could be an incorrect operation. So, if time passes and GPS doesn't pick up, treat as at home again
                        self.schedule_arrival_after_delay(person)
            else:
                self.log(f"pullout for {person} has no weighted votes")

    def schedule_arrival_after_delay(self, person):
        cb_token = self.run_in(self.arrived_home, person=person, delay=self.args['away_tracker_arrival_delay_secs'])
        self.away_tracker_pending_arrivals[person] = cb_token

    def set_person_fused_tracker_state(self, person, state, from_device='bug'):
        fused_tracker = self.get_entity(self.fused_trackers[person])
        fused_tracker.set_state(state=state)
        if state in ['away', 'just_left']:
            person_ent = self.get_entity(f'device_tracker.{person}_irk')
            person_ent.set_state(state='away', attributes={'from_device': from_device})
        fused_override_select = f"select.irk_tracker_fused_override_{person}"
        self.get_entity(fused_override_select).set_state(state=state)

    @ad.app_lock
    def away_tracker_cb(self, entity, attr, old, new, kwargs):
        person = kwargs['person']
        #self.log(f"running away tracker cb for person = {person} state = {new} entity = {entity}")
        if new != 'home':
            if person in self.away_tracker_pending_arrivals:
                # cancel a pending arrival if we got a new not here event
                self.cancel_timer(self.away_tracker_pending_arrivals[person])
                del self.away_tracker_pending_arrivals[person]
                self.log(f"canceled pending arrival timer for {person} because they are {new}")
            self.set_person_fused_tracker_state(person, 'away', entity)
        else:
            cur_state = self.get_state(self.fused_trackers[person])
            self.log(f"{person} arrived home, current fused state = {cur_state}")
            if cur_state == 'home':
                self.log(f"doing nothing because we are already aware they're home")
            else:
                self.log(f"acknowledging in {self.args['away_tracker_arrival_delay_secs']}")
                self.schedule_arrival_after_delay(person)

    @ad.app_lock
    def arrived_home(self, kwargs):
        person = kwargs['person']
        self.log(f"registering that {person} arrived home (after delay)")
        self.set_person_fused_tracker_state(person, 'home', 'arrived_after_delay')

    @ad.app_lock
    def override_fused_cb(self, event_name, data, kwargs):
        entity = data['service_data']['entity_id']
        option = data['service_data']['option']
        attrs = self.get_state(entity, attribute='all')
        person = attrs['attributes']['person']
        self.log(f'overriding fused for {person} to {option}')
        if person in self.away_tracker_pending_arrivals:
            self.cancel_timer(self.away_tracker_pending_arrivals[person])
            del self.away_tracker_pending_arrivals[person]
            self.log(f"canceled pending arrival timer for {person} because of override")
        self.set_person_fused_tracker_state(person, option, 'fused_override')


    @ad.app_lock
    def make_primary_cb(self, event_name, data, kwargs):
        entity = data['service_data']['entity_id']
        button_attrs = self.get_state(entity, attribute='all')
        person = button_attrs['attributes']['person']
        device = button_attrs['attributes']['device']
        self.active_device_by_person[person] = device
        self.log(f"Manually overriding primary device for {person} to be {device}")
        self.tracking_resolve(device, force_update=True)

    @ad.app_lock
    def ble_tracker_cb(self, event_name, data, kwargs):
        #self.log(f'event: {event_name} : {data}')
        #time = du.parse(data['metadata']['time_fired'])
        # TODO this should actually do the parsing above with the timezone awareness
        matched_device = 'none'
        if data['addr'] in self.known_addr_cache:
            matched_device = self.known_addr_cache[data['addr']]
            #if matched_device == 'none':
            #    self.log(f"fast skipping {data['addr']}")
        else:
            addr = bytes.fromhex(data['addr'].replace(":",""))
            pt = bytearray(b'\0' * 16)
            pt[15] = addr[2]
            pt[14] = addr[1]
            pt[13] = addr[0]
            for name, cipher in self.ciphers.items():
                msg = cipher.encrypt(bytes(pt))
                if msg[15] == addr[5] and msg[14] == addr[4] and msg[13] == addr[3]:
                    matched_device = name
                    break
            # This also caches that a device is unknown
            self.known_addr_cache[data['addr']] = matched_device
        if matched_device == 'none':
            return
        #self.log(f"found a match {matched_device}")
        time = datetime.now()
        source = data['source']
        rssi = int(data['rssi'])
        if self.recording_df is not None:
            self.recording_df['time'].append(time)
            self.recording_df['device'].append(matched_device)
            self.recording_df['source'].append(source)
            self.recording_df['rssi'].append(rssi)
            if len(self.recording_df['time']) > self.rows_per_flush:
                self.flush_recording()
        # handle publishing update for appropriate entity
        obs = self.recent_observations[(source,matched_device)]
        #if source in self.rssi_adjustments:
        #    self.log(f"Adjusting rssi for {source} from {rssi} to {rssi + self.rssi_adjustments.get(source,0)}")
        # If this is the first observation for this device across all sources, this means that the owner just arrived home, so we should update the fused tracker
        # Unless this is within 30 seconds of the initialization of the component, in which case ignore it
        if len(obs) == 0 and (time - self.init_time).total_seconds() > 30:
            # now, we need to check all the sources
            other_source_had_obs = False
            for other_obs in (o for (s,d),o in self.recent_observations.items() if d == matched_device and s != source):
                if len(other_obs) != 0:
                    other_source_had_obs = True
                    break
            if not other_source_had_obs:
                device_person = self.identities[matched_device]['person']
                self.set_person_fused_tracker_state(device_person, 'just_arrived', f'saw {matched_device}')
                self.log(f"{device_person} just arrived due to first observation from {matched_device}")
                self.schedule_arrival_after_delay(device_person)
        obs.append((time, rssi + self.rssi_adjustments.get(source,0)))
        self.tracking_resolve(matched_device)
        if matched_device in self.expiry_timers:
            self.cancel_timer(self.expiry_timers[matched_device])
        self.expiry_timers[matched_device] = self.run_in(self.device_expiry, delay=self.tracking_window.total_seconds(), expiring_device = matched_device)
        #self.log(f"found a match for {matched_device} with rssi {data['rssi']} from {source} at {time} (mac: {data['addr']})")

    @ad.app_lock
    def device_expiry(self, kwargs):
        device = kwargs['expiring_device']
        del self.expiry_timers[device]
        self.tracking_resolve(device)

    def tracking_resolve(self, device, force_update=False):
        room_votes = defaultdict(lambda: [])
        total_votes = 0
        for (source, d), obs in self.recent_observations.items():
            if device != d:
                # not resolving this device atm
                continue
            self.prune_old_obs(obs)
            if source not in self.room_aliases:
                # tracker doesn't belong to a room
                continue
            room = source#self.room_aliases[source]
            room_votes[room].extend(obs)
            total_votes += len(obs)
        weighted_votes = []
        if total_votes < 3:
            # not enough info
            in_room = 'unknown'
        else:
            now = datetime.now()
            for room, obs in room_votes.items():
                if len(obs) == 0:
                    continue # nothing to do
                count = numerator = denominator = 0
                for time, rssi in obs:
                    #weight = 1.0
                    weight = 0.5**((now-time).total_seconds()/self.ping_halflife_seconds)
                    numerator += -rssi * weight
                    denominator += weight
                    count += 1
                orig_source = room
                # at this point, resolve to a room
                weighted_votes.append((numerator / denominator, count, orig_source))
            weighted_votes.sort(key=lambda x: x[0], reverse=False)
            in_room = self.resolve_room2(weighted_votes, device)
        #self.log(f"total_votes={total_votes} in_room={in_room}")
        device_person = self.identities[device]['person']
        # they're always here if they don't have a fused tracker, or they're in a "home" state
        person_is_home = device_person not in self.fused_trackers or self.get_state(self.fused_trackers[device_person]) in ['home']#, 'just_arrived']
        if device in self.device_in_room and in_room != 'unknown':
            old_room = self.device_in_room[device]
            # here we filter to ensure the in_room is occupied, or else we can ignore this update
            in_room_is_eligible = True
            if in_room in self.room_presence:
                in_room_is_eligible = False
                for sensor in self.room_presence[in_room]:
                    if self.get_state(sensor) == 'on':
                        in_room_is_eligible = True
                        break
            #self.log(f"device={device}, in_room={in_room}, eligble={in_room_is_eligible}")
            # This statement is saying that:
            # If the room is occupied, and it's different than the last place we had them, we can update
            # If the room is occupied, and we think they are away, if they're home now, we'll resume updating their room tracker
            # Or if it's a forced update, just go for it
            if (in_room_is_eligible and old_room != in_room or self.get_state(f'device_tracker.{device_person}_irk') == 'away') or force_update:
                # publish that the person is in in_room, if we think they're actually here
                if person_is_home:
                    person_ent = self.get_entity(f'device_tracker.{device_person}_irk')
                    person_ent.set_state(state=in_room, attributes={'from_device': device})
                # mark that this is the "active" device for that person
                self.active_device_by_person[device_person] = device
        # publish the device-level stat
        self.device_in_room[device] = in_room
        device_ent = self.get_entity(f'device_tracker.{device.replace(" ", "_")}_irk')
        device_ent.set_state(state=in_room, attributes={'weighted_votes': weighted_votes})
        # finally, only if now every device is unknown
        active_device = self.active_device_by_person[device_person]
        active_device_unknown = self.device_in_room[active_device] == 'unknown'
        if active_device_unknown and person_is_home:
            # publish that the person isn't detected
            person_ent = self.get_entity(f'device_tracker.{device_person}_irk')
            person_ent.set_state(state='unknown', attributes={'from_device': f'{active_device} unknown'})
        #self.log(f"checking if every device is unknown for {device_person}: {self.device_in_room}")
        #every_device_unknown = True
        #for identity, data in self.identities.items():
        #    if data['person'] != device_person:
        #        continue
        #    x = self.device_in_room[data['device_name']]
        #    if x is not None and x != 'unknown':
        #        self.log(f"all devices not unknown due to {data}")
        #        every_device_unknown = False
        #        break
        #if every_device_unknown:
        #    # publish that the person isn't detected
        #    person_ent = self.get_entity(f'device_tracker.{device_person}_irk')
        #    person_ent.set_state(state='unknown', attributes={'from_device': 'all'})

    def resolve_room(self, weighted_votes, device):
        rooms = []
        def resolve_inner(source, i, recur=True):
            alias = self.room_aliases[source]
            if isinstance(alias, str): # direct mapping
                return alias
            elif 'secondary_clarifiers' in alias:
                if len(weighted_votes) == 1: # we haven't observed anything else, so default if we have one
                    return alias.get('default')
                if i == 0:
                    next_index = 1
                else:
                    next_index = i-1 # preceding
                #self.log(f"for {device} resolving inner for {source} at 0, next is {next_source} at {next_index}")
                _,_,next_source = weighted_votes[next_index]
                clarifiers = alias['secondary_clarifiers']
                if next_source in clarifiers: # handle case resolving by device
                    return clarifiers[next_source]
                try:
                    if recur:
                        next_room = resolve_inner(next_source, next_index, recur=i != 0)
                    else:
                        next_room = next_source
                except:
                    self.log(f"for {device} resolving inner for {source} at {i}, next is {next_source} will have index {next_index} and weighted_votes len={len(weighted_votes)}, weighted_votes={weighted_votes}")
                    raise
                if next_room in clarifiers: # handle case resolving by room
                    return clarifiers[next_room]
                if 'default' in alias:
                    return alias['default']
                # no data for 2ndary classifier b/c it's the last one
                return None
            else:
                raise ValueError(f'invalid alias config: {alias}')
        for i, (rssi, count, source) in enumerate(weighted_votes):
            room = resolve_inner(source, i)
            rooms.append(room)
        if len(rooms) == 1:
            resolved_room = rooms[0]
        elif len(rooms) >= 2:
            # TODO this should consider the secondary clarifiers as well
            if weighted_votes[0][0] < weighted_votes[1][0] * self.min_superplurality or rooms[0] == rooms[1]:
                resolved_room = rooms[0]
            else: # there was no superplurality
                resolved_room = 'unknown'
        if resolved_room is None:
            resolved_room = 'unknown'
        return resolved_room

    def resolve_room2(self, weighted_votes, device):
        rooms = []
        def resolve_inner(i, recur=True):
            rssi, count, source = weighted_votes[i]
            alias = self.room_aliases[source]
            if isinstance(alias, str): # direct mapping
                return (rssi, count, alias)
            elif 'secondary_clarifiers' in alias:
                clarifiers = alias['secondary_clarifiers']
                # Look over the candidate clarifiers, which is everything with a higher signal strength & the next one
                clarifying_sources = []
                for j, (c_rssi, c_count, c_source) in enumerate(weighted_votes[0:min(i+2, len(weighted_votes))]):
                    if j != i and c_rssi < rssi * self.min_superplurality: # we can't self-clarify, of course, and we should only clarify if they're similar in strength (TODO is this true)
                        clarifying_sources.append((j, (c_rssi, c_count, c_source)))
                # map each clarifying source to rssi + room
                resolved_clarifying_sources = []
                for j, (c_rssi, c_count, c_source) in clarifying_sources:
                    if c_source in clarifiers: # it's a source -> room mapping
                        resolved_clarifying_sources.append((c_rssi, c_count, clarifiers[c_source]))
                    elif recur: # we can try to resolve once
                        x = resolve_inner(j, recur=False)
                        if x is not None:
                            (c_rssi_resolve, c_count_resolved, c_source_resolved) = x
                            if c_source_resolved in clarifiers: # we can now resolve the room
                                resolved_clarifying_sources.append((c_rssi_resolve, c_count_resolved, clarifiers[c_source_resolved]))
                    else:
                        pass # we can't resolve devices to rooms on the 2nd recurrance
                # we have a list of rssi,count,room in resolved_clarifying_sources that all matched 2ndary clarifiers
                # if all rooms are the same, just return that
                if len(set(room for (_,_,room) in resolved_clarifying_sources)) == 1:
                    return resolved_clarifying_sources[0]
                # The first clarifier that's superplural to others that don't match wins
                for c_rssi, c_count, c_room in resolved_clarifying_sources:
                    nonmatching_min_rssi = min(rssi for (rssi, _, room) in resolved_clarifying_sources if room != c_room)
                    if c_rssi < nonmatching_min_rssi * self.min_superplurality:
                        # winner
                        return (c_rssi, c_count, c_room)
                if 'default' in alias:
                    return (rssi, count, alias['default'])
            else:
                raise ValueError(f'invalid alias config: {alias}')
        for i, (rssi, count, source) in enumerate(weighted_votes):
            room = resolve_inner(i)
            if room is not None:
                rooms.append(room)
        resolved_room = 'unknown'
        if len(rooms) == 1:
            (_,_,resolved_room) = rooms[0]
        elif len(rooms) >= 2:
            if rooms[0][0] < rooms[1][0] * self.min_superplurality or rooms[0][2] == rooms[1][2]:
                resolved_room = rooms[0][2]
            else: # there was no superplurality
                resolved_room = 'unknown'
        return resolved_room

    def prune_old_obs(self, obs):
        while obs and (obs[0][0] + self.tracking_window).timestamp() < datetime.now().timestamp():
            obs.pop(0)
