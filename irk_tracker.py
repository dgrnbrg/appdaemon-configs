import hassapi as hass
import pandas as pd
from Crypto.Cipher import AES
import dateutil.parser as du
import os
from glob import glob
from sklearn.neighbors import KNeighborsClassifier


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

    def fit_model(self, event_name, data, kwargs):
        data = pd.concat(pd.read_csv(x) for x in glob(f"{self.data_loc}examples*.csv"))
        # TODO define base_station_names, a list of locations. Then, pivot data
        #self.knn_columns = base_station_names
        self.knn = KNeighborsClassifier(n_neighbors=7)
        #self.knn.fit(data[self.knn_columns], data["tag"])

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
                if self.knn:
                    #self.knn.predict(
                    # TODO must grab the latest values that are new enough from other sources to match the model's schema
                    pass
                #self.log(f"found a match for {name} with rssi {data['rssi']} from {source} at {time} (mac: {data['addr']})")
