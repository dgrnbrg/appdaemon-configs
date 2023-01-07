import hassapi as hass
from collections import defaultdict
import pandas as pd
from Crypto.Cipher import AES
import dateutil.parser as du
from datetime import timedelta, datetime, time
import os
from glob import glob
from sklearn.neighbors import KNeighborsClassifier
from sklearn import svm
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
        for room in self.room_aliases.values():
            if isinstance(room, str):
                self.valid_rooms.add(room)
            # TODO include rooms from other alias types
        self.identities = {x['device_name']: x for x in self.args['identities']}
        self.ciphers = {}
        for identity, data in self.identities.items():
            self.ciphers[identity] = AES.new(bytearray.fromhex(data['irk']), AES.MODE_ECB)
            device_ent = self.get_entity(f'device_tracker.{identity.replace(" ", "_")}_irk')
            if device_ent.exists():
                self.device_in_room[identity] = device_ent.get_state(attribute='room')
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
        self.listen_event(self.ble_tracker_cb, "esphome.ble_tracking_beacon")
        self.recording_df = None
        self.listen_event(self.start_recording, "irk_tracker.start_recording")
        self.listen_event(self.stop_recording, "irk_tracker.stop_recording")
        self.listen_event(self.fit_model, "irk_tracker.fit_model")
        self.recent_observations = defaultdict(lambda: [])
        #self.run_minutely(self.inference, time(0,0,0))
        self.run_hourly(self.clear_known_addr_cache, time(0,0,0))

    def fit_model(self, event_name, data, kwargs):
        dfs = []
        for x in glob(f'{self.data_loc}examples*.csv'):
            df = pd.read_csv(x, parse_dates=[0])
            dfs.append(df)
        df = pd.concat(dfs)
        df = df.sort_values(by=['time']).query("device not in ['aysylu phone', 'aysylu watch'] and source not in ['basement_pi']")
        base_station_names = list(df['source'].unique())
        rolling_rssi = df.set_index('time').groupby(['device', 'source']).rolling("3min", min_periods=10)['rssi'].mean().reset_index()
        rolling_labeled = pd.merge(df.drop('rssi', axis=1), rolling_rssi, on=['device', 'source', 'time']).dropna()
        with_station = rolling_labeled
        for station in base_station_names:
            specific_station = rolling_labeled.query(f"source == '{station}'").copy()
            specific_station = specific_station.drop('source',axis=1).rename(columns={'rssi': station})
            with_station = pd.merge_asof(with_station, specific_station,
                                         left_on='time',
                                         right_on='time',
                                         direction='backward',
                                         allow_exact_matches=False,
                                         tolerance=pd.Timedelta(seconds=5),
                                         by=['device', 'tag'])
        for station in base_station_names:
            with_station[station] = np.where(with_station['source'] == station, with_station['rssi'], with_station[station])
        with_station = with_station.drop(['source', 'rssi'], axis=1).dropna()#.fillna(-100)
        self.knn_columns = base_station_names
        self.knn = KNeighborsClassifier(n_neighbors=7)
        self.knn.fit(with_station[base_station_names].to_numpy(), with_station['tag'].to_numpy())
        clf1 = svm.SVC()
        clf1.fit(with_station[['living_room_blinds', 'bedroom_blinds']].to_numpy(),
                 np.where(with_station['tag'] == "upstairs-both-20221027", "upstairs", "other"))
        clf2 = svm.SVC()
        clf2.fit(with_station[['basement_beacon', 'bedroom_blinds']].to_numpy(), np.where(with_station['tag'] == "basement-david-20221027", "basement", "main-floor"))
        def predict(a):
            m = {k: v for k,v in zip(base_station_names, a)}
            r = clf1.predict([[m[x] for x in ['living_room_blinds', 'bedroom_blinds']]])[0]
            if r == 'other':
                r = clf2.predict([[m[x] for x in ['basement_beacon', 'bedroom_blinds']]])[0]        
            return r
        self.svm = predict

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

    def ble_tracker_cb(self, event_name, data, kwargs):
        #self.log(f'event: {event_name} : {data}')
        #time = du.parse(data['metadata']['time_fired'])
        # TODO this should actually do the parsing above with the timezone awareness
        matched_device = 'none'
        if data['addr'] in self.known_addr_cache:
            matched_device = self.known_addr_cache[data['addr']]
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

    def device_expiry(self, kwargs):
        device = kwargs['expiring_device']
        del self.expiry_timers[device]
        self.tracking_resolve(device)

    def tracking_resolve(self, device):
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
            if old_room != in_room:
                # publish that the person is in in_room
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
        if active_device_unknown:
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

    def clear_known_addr_cache(self, kwargs):
        self.known_addr_cache = {}

    def inference(self, kwargs):
        for (source, name) in self.recent_observations:
            if self.knn:
                means = defaultdict(lambda: 0)
                num_detected = 0
                for (source, otherdevice), obs in self.recent_observations.items():
                    if otherdevice == name:
                        self.prune_old_obs(obs)
                        if len(obs) >= 10:
                            for (_, rssi) in obs:
                                means[source] += float(rssi)
                            means[source] /= len(obs)
                            num_detected += 1
                        else:
                            #self.log(f"not predicting for {name} b/c {source} only has {len(obs)} obs for {name}")
                            means[source] = -100.0
                device_entity = self.get_entity(f"sensor.ble_tracker_{name.replace(' ', '_')}")
                if num_detected != 0:
                    if num_detected != len(means):
                        #self.log(f"only detected {num_detected} stations ({[x for x,v in means.items() if v != -100.0]})")
                        pass
                    input_arg = [means[source] for source in self.knn_columns]
                    room = self.knn.predict([input_arg])[0]
                    svm_room = self.svm(input_arg)
                    m = {k: v for k,v in zip(self.knn_columns, input_arg)}
                    if not room.startswith(svm_room):
                        #self.log(f"Localized {name} to knn:{room} svm:{svm_room} {m}")
                        device_entity.set_state(state=f"{svm_room} and {room}")
                    else:
                        device_entity.set_state(state=svm_room)
                else:
                    device_entity.set_state(state='insufficient')
                    vis = {k:v for k,v in means.items()}
                    #self.log(f"Couldn't localize {name}; only obs from {vis}")
