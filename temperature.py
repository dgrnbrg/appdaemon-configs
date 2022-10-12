import datetime
import numpy as np
import pandas as pd
import hassapi as hass
import datetime
import influx


def trim_entity(domain, s):
    if s.startswith(domain + "."):
        return s[len(domain) + 1:]
    else:
        return s

def get_sensor_data(entity_id, start='-7d', field="value"):
    q = f'''
    from(bucket: "homeassistant/autogen")
      |> range(start: {start})
      |> filter(fn: (r) => r.entity_id == "{entity_id}" and r._field == "{field}")
      |> pivot(rowKey:["_time"], columnKey: ["entity_id"], valueColumn: "_value")
      |> drop(columns: ["_measurement", "domain", "_start", "_stop"])
    '''
    df = influx.query_api.query_data_frame(q).drop(['result','table','_field'], axis=1)
    if field != 'value':
        return df.rename(columns={entity_id: field})
    else:
        return df

class OffsetCalibration(hass.Hass):
    def initialize(self):
        self.thermostat_ent = trim_entity('climate', self.args["climate_entity"])
        self.remote_temp_ent = [trim_entity('sensor', x) for x in self.args["temperature_entity"]]
        runtime = datetime.time(0, 0, 0)
        self.run_in(self.compute_offsets, 0)
        self.run_hourly(self.compute_offsets, runtime)

    def compute_offsets(self, kwargs):
        for remote_temp_ent in self.remote_temp_ent:
            self.log(f"computing offset for sensor: {remote_temp_ent}")
            offset_entity = self.get_entity(f"sensor.offset_calibrated_{self.thermostat_ent}_{remote_temp_ent}")
            try:
                remote_temp = get_sensor_data(remote_temp_ent)
                temp = get_sensor_data(self.thermostat_ent, field="current_temperature")
                target_temp = get_sensor_data(self.thermostat_ent, field="temperature")
                mode = get_sensor_data(self.thermostat_ent, field="hvac_action_str")
                base_times = pd.concat([x['_time'] for x in [temp, remote_temp, target_temp, mode]])
                base_times = base_times.sort_values().drop_duplicates()
                df = pd.merge_asof(base_times, temp, on='_time')
                df = pd.merge_asof(df, target_temp, on='_time')
                df = pd.merge_asof(df, mode, on='_time')
                df = pd.merge_asof(df, remote_temp, on='_time')
                offset_df = df.query("hvac_action_str in ('heating', 'cooling') and current_temperature == temperature").copy()
                offset_df._time= offset_df._time.dt.tz_convert('America/New_York')
                offset_df['delta'] = offset_df[remote_temp_ent] - offset_df['current_temperature']
                result = offset_df[['hvac_action_str', 'delta']].groupby('hvac_action_str').describe()
                self.log(result)
                offset_entity.set_state(state='on', attributes = {
                    'cooling_offset': result.loc['cooling'].loc[('delta','mean')],
                    'heating_offset': result.loc['heating'].loc[('delta','mean')],
                    'cooling_stddev': result.loc['cooling'].loc[('delta','std')],
                    'heating_stddev': result.loc['heating'].loc[('delta','std')]
                })
            except:
                offset_entity.state_state(state='error')

class ConvergenceSpeedCalibration(hass.Hass):
    def initialize(self):
        self.thermostat_ent = trim_entity('climate', self.args["climate_entity"])
        runtime = datetime.time(0, 0, 0)
        self.run_in(self.compute_offsets, 0)
        self.run_hourly(self.compute_offsets, runtime)

    def compute_offsets(self, kwargs):
        # TODO copy this into the implementation speed measurer
        temp = get_sensor_data(self.thermostat_ent, field="current_temperature")
        target_temp = get_sensor_data(self.thermostat_ent, field="temperature")
        mode = get_sensor_data(self.thermostat_ent, field="hvac_action_str")
        base_times = pd.concat([x['_time'] for x in [temp, target_temp, mode]])
        base_times = base_times.sort_values().drop_duplicates()
        df = pd.merge_asof(base_times, temp, on='_time')
        df = pd.merge_asof(df, target_temp, on='_time')
        df = pd.merge_asof(df, mode, on='_time')
        df = df.set_index('_time')
        change_df = df[['hvac_action_str', 'temperature']].shift(1).rename(columns={'hvac_action_str': 'action_before', 'temperature': 'target_before'})
        change_events_df = pd.merge(df, change_df, left_index=True, right_index=True).dropna(subset=['hvac_action_str', 'action_before']).query("(hvac_action_str != action_before or temperature != target_before) and hvac_action_str not in ['fan', 'off'] and current_temperature != temperature")

        stable_df = df.query('current_temperature == temperature').drop(['current_temperature', 'temperature'],axis=1)
        stable_df['stable_time'] = stable_df.index
        change_events_df['temp_delta'] = change_events_df['temperature'] - change_events_df['current_temperature']
        change_events_df = pd.merge_asof(change_events_df, stable_df, left_index=True, right_index=True, by='hvac_action_str', direction='forward').dropna(subset=['stable_time'])
        change_events_df['time_delta'] = (change_events_df['stable_time'] - change_events_df.index).dt.total_seconds()
        change_events_df['adapt_rate_degrees_per_hr'] = change_events_df['temp_delta'] / (change_events_df['time_delta'] / 3600.0)
        self.log(change_events_df)
