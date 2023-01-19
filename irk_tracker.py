import hassapi as hass
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
        self.valid_rooms = set()
        self.device_in_room = defaultdict(lambda: 'unknown')
        self.active_device_by_person = defaultdict(lambda: 'unknown')
        self.expiry_timers = {}
        self.tracking_window = timedelta(minutes=int(self.args.get('tracking_window_minutes', 3)))
        self.min_superplurality = self.args.get('tracking_min_superplurality',1.0)
        self.rssi_adjustments = self.args.get('rssi_adjustments',{})
        self.ping_halflife_seconds = self.args.get('ping_halflife_seconds', 60)
        self.log(f'rssi_adjustments = {self.rssi_adjustments}')
        self.away_tracker_state = {}
        self.away_tracker_pending_arrivals = {}
        for room in self.room_aliases.values():
            if isinstance(room, str):
                self.valid_rooms.add(room)
            # TODO include rooms from other alias types
        self.identities = {x['device_name']: x for x in self.args['identities']}
        self.ciphers = {}
        people = set(x['person'] for x in self.args['identities'])
        for identity, data in self.identities.items():
            self.ciphers[identity] = AES.new(bytearray.fromhex(data['irk']), AES.MODE_ECB)
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
        for cfg in self.args['away_trackers']:
            #self.log(f"configuring away tracker {cfg}")
            person = cfg['person']
            tracker = cfg['tracker']
            self.away_tracker_state[person] = 'home' if self.get_state(tracker) == 'home' else 'away'
            self.listen_state(self.away_tracker_cb, tracker, person=person)
        for person in people:
            person_ent = self.get_entity(f'device_tracker.{person}_irk')
            init_state = self.away_tracker_state.get(person, 'unknown')
            person_ent.set_state(state=init_state, attributes={'from_device': 'init'})
        self.listen_event(self.ble_tracker_cb, "esphome.ble_tracking_beacon", addr=lambda addr: self.known_addr_cache.get(addr,None) != 'none')
        self.recording_df = None
        self.listen_event(self.start_recording, "irk_tracker.start_recording")
        self.listen_event(self.stop_recording, "irk_tracker.stop_recording")
        self.recent_observations = defaultdict(lambda: [])

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
    def away_tracker_cb(self, entity, attr, old, new, kwargs):
        person = kwargs['person']
        self.log(f"running away tracker cb for person = {person} state = {new} entity = {entity}")
        if new != 'home':
            if person in self.away_tracker_pending_arrivals:
                # cancel a pending arrival if we got a new not here event
                self.cancel_timer(self.away_tracker_pending_arrivals[person])
                del self.away_tracker_pending_arrivals[person]
                self.log(f"canceled pending arrival timer")
            self.away_tracker_state[person] = 'away'
            person_ent = self.get_entity(f'device_tracker.{person}_irk')
            person_ent.set_state(state='away', attributes={'from_device': entity})
        else:
            self.log(f"arrived home, acknowledging in {self.args['away_tracker_arrival_delay_secs']}")
            cb_token = self.run_in(self.arrived_home, person=person, delay=self.args['away_tracker_arrival_delay_secs'])
            self.away_tracker_pending_arrivals[person] = cb_token

    @ad.app_lock
    def arrived_home(self, kwargs):
        self.log(f"registering that {kwargs['person']} arrived home (after delay)")
        self.away_tracker_state[kwargs['person']] = 'home'

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
            in_room = self.resolve_room(weighted_votes, device)
        device_person = self.identities[device]['person']
        if device in self.device_in_room and in_room != 'unknown':
            old_room = self.device_in_room[device]
            if old_room != in_room or self.get_state(f'device_tracker.{device_person}_irk') == 'away' or force_update:
                # publish that the person is in in_room, if we think they're actually here
                if self.away_tracker_state[device_person] == 'home':
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
        if active_device_unknown and self.away_tracker_state[device_person] == 'home':
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
        def resolve_inner(source, i):
            alias = self.room_aliases[source]
            if isinstance(alias, str): # direct mapping
                return alias
            elif 'secondary_clarifiers' in alias:
                if i != 0 and i < (len(weighted_votes) + 1):
                    try:
                        _,_,next_source = weighted_votes[i-1]
                    except:
                        self.log(f"for {device} resolving inner for {source} at {i}, next will have index {i-1} and weighted_votes len={len(weighted_votes)}, weighted_votes={weighted_votes}")
                        raise
                    #self.log(f"for {device} resolving inner for {source} at {i}, next is {next_source} at {i-1}")
                    next_room = resolve_inner(next_source, i+1)
                    if next_room in alias['secondary_clarifiers']:
                        return next_room
                    else:
                        return None
                if len(weighted_votes) >= 2: # i == 0
                    _,_,next_source = weighted_votes[1]
                    #self.log(f"for {device} resolving inner for {source} at 0, next is {next_source} at 1")
                    next_room = resolve_inner(next_source, 1)
                    #self.log(f"  next room = {next_room}")
                    if next_room in alias['secondary_clarifiers']:
                        return next_room
                    else:
                        return None
                else: # no data for 2ndary classifier b/c it's the last one
                    return None
            else:
                raise ValueError(f'invalid alias config: {alias}')
        for i, (rssi, count, source) in enumerate(weighted_votes):
            room = resolve_inner(source, i)
            rooms.append(room)
        if len(rooms) == 1:
            resolved_room = rooms[0]
        elif len(rooms) >= 2:
            if weighted_votes[0][0] < weighted_votes[1][0] * self.min_superplurality or rooms[0] == rooms[1]:
                resolved_room = rooms[0]
            else: # there was no superplurality
                resolved_room = 'unknown'
        if resolved_room is None:
            resolved_room = 'unknown'
        return resolved_room

    def prune_old_obs(self, obs):
        while obs and (obs[0][0] + self.tracking_window).timestamp() < datetime.now().timestamp():
            obs.pop(0)
