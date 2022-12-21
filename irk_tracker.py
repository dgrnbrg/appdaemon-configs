import hassapi as hass
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


identities = {
        'david phone': b'\x0C\x95\x82\xB2\xC7\xD3\xBE\x4B\x8B\xE2\xE2\xC3\x3F\xFE\xFA\x8F',
        'david watch': b'\x57\xd8\x99\xb3\x9a\x9b\xca\x38\x4e\x89\xab\x74\x1b\x56\xd3\xbd',
        'aysylu phone': b'\x67\x8D\xDF\x7B\x3B\xDB\x19\x51\x06\xA6\x7E\x5B\xC1\x2E\x55\x6F',
        'aysylu watch': b'\x00\x33\x35\x4f\xb6\x1c\x4f\x44\xf2\x38\x9c\x68\x29\x88\x02\xe4'
}

ciphers = {k: AES.new(v, AES.MODE_ECB) for k,v in identities.items()}

tracker_log_loc = '/config/appdaemon/tracker_logs/'
tracker_log_rows_per_flush = 100


class IrkTracker(hass.Hass):
    def initialize(self):
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
            self.fit_model(None, {}, {})
            self.log("model fit")
        else:
            self.knn = None
        self.listen_event(self.ble_tracker_cb, "esphome.ble_tracking_beacon")
        self.recording_df = None
        self.listen_event(self.start_recording, "irk_tracker.start_recording")
        self.listen_event(self.stop_recording, "irk_tracker.stop_recording")
        self.listen_event(self.fit_model, "irk_tracker.fit_model")
        self.recent_observations = defaultdict(lambda: [])
        self.run_minutely(self.inference, time(0,0,0))

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
        addr = bytes.fromhex(data['addr'].replace(":",""))
        time = du.parse(data['metadata']['time_fired'])
        source = data['source']
        if source in ['basement_pi']:
            return
        rssi = data['rssi']
        pt = bytearray(b'\0' * 16)
        pt[15] = addr[2]
        pt[14] = addr[1]
        pt[13] = addr[0]
        for name, cipher in ciphers.items():
            msg = cipher.encrypt(bytes(pt))
            if msg[15] == addr[5] and msg[14] == addr[4] and msg[13] == addr[3]:
                if self.recording_df is not None:
                    self.recording_df['time'].append(time)
                    self.recording_df['device'].append(name)
                    self.recording_df['source'].append(source)
                    self.recording_df['rssi'].append(rssi)
                    if len(self.recording_df['time']) > self.rows_per_flush:
                        self.flush_recording()
                # handle publishing update for appropriate entity
                obs = self.recent_observations[(source,name)]
                obs.append((time, rssi))
                self.prune_old_obs(obs)
                #self.log(f"found a match for {name} with rssi {data['rssi']} from {source} at {time} (mac: {data['addr']})")

    def prune_old_obs(self, obs):
        while obs and (obs[0][0] + timedelta(minutes=3)).timestamp() < datetime.now().timestamp():
            obs.pop(0)

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
