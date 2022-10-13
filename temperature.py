import datetime
import numpy as np
import pandas as pd
import hassapi as hass
import datetime
import influx


def get_sensor_data(entity_id, column, start='-7d'):
    id_parts = entity_id.split('.')
    if len(id_parts) == 2:
        id_parts.append("value") # default for state field, otherwise attr
    q = f'''
    from(bucket: "homeassistant/autogen")
      |> range(start: {start})
      |> filter(fn: (r) => r.entity_id == "{id_parts[1]}" and r._field == "{id_parts[2]}" and r.domain == "{id_parts[0]}")
      |> pivot(rowKey:["_time"], columnKey: ["entity_id"], valueColumn: "_value")
      |> drop(columns: ["_measurement", "domain", "_start", "_stop"])
    '''
    df = influx.query_api.query_data_frame(q).drop(['result','table','_field'], axis=1)
    return df.rename(columns={id_parts[1]: column})

class OffsetCalibration(hass.Hass):
    def initialize(self):
        self.thermostat_ent = self.args["climate_entity"]
        self.remote_temp_ent = self.args["temperature_entity"]
        runtime = datetime.time(0, 0, 0)
        self.run_in(self.compute_offsets, 0)
        self.run_hourly(self.compute_offsets, runtime)

    def compute_offsets(self, kwargs):
        for remote_temp_ent in self.remote_temp_ent:
            self.log(f"computing offset for sensor: {remote_temp_ent} from {self.thermostat_ent}")
            thermostat_ent_parts = self.thermostat_ent.split('.')
            remote_temp_ent_parts = remote_temp_ent.split('.')
            offset_entity = self.get_entity(f"sensor.offset_calibrated_{thermostat_ent_parts[1]}_{remote_temp_ent_parts[1]}")
            try:
                remote_temp = get_sensor_data(remote_temp_ent, column='remote_temp')
                temp = get_sensor_data(self.thermostat_ent + ".current_temperature", column='current_temp')
                target_temp = get_sensor_data(self.thermostat_ent + ".temperature", column='target_temp')
                mode = get_sensor_data(self.thermostat_ent + ".hvac_action_str", column='hvac_action')
                base_times = pd.concat([x['_time'] for x in [temp, remote_temp, target_temp, mode]])
                base_times = base_times.sort_values().drop_duplicates()
                df = pd.merge_asof(base_times, temp, on='_time')
                df = pd.merge_asof(df, target_temp, on='_time')
                df = pd.merge_asof(df, mode, on='_time')
                df = pd.merge_asof(df, remote_temp, on='_time')
                offset_df = df.query("hvac_action in ('heating', 'cooling') and current_temp == target_temp").copy()
                offset_df._time= offset_df._time.dt.tz_convert('America/New_York')
                offset_df['delta'] = offset_df['remote_temp'] - offset_df['current_temp']
                result = offset_df[['hvac_action', 'delta']].groupby('hvac_action').describe()
                self.log(result)
                offset_entity.set_state(state='on', attributes = {
                    'cooling_offset': result.loc['cooling'].loc[('delta','mean')],
                    'heating_offset': result.loc['heating'].loc[('delta','mean')],
                    'cooling_stddev': result.loc['cooling'].loc[('delta','std')],
                    'heating_stddev': result.loc['heating'].loc[('delta','std')]
                })
            except Exception as e:
                self.error(e)
                offset_entity.set_state(state='error')

class ConvergenceSpeedCalibration(hass.Hass):
    def initialize(self):
        self.thermostat_ent = self.args["climate_entity"]
        runtime = datetime.time(0, 0, 0)
        self.run_in(self.compute_offsets, 0)
        self.run_hourly(self.compute_offsets, runtime)

    def compute_offsets(self, kwargs):
        temp = get_sensor_data(self.thermostat_ent + ".current_temperature", column="current_temp")
        target_temp = get_sensor_data(self.thermostat_ent + ".temperature", column="target_temp")
        mode = get_sensor_data(self.thermostat_ent + ".hvac_action_str", column="hvac_action")
        base_times = pd.concat([x['_time'] for x in [temp, target_temp, mode]])
        base_times = base_times.sort_values().drop_duplicates()
        df = pd.merge_asof(base_times, temp, on='_time')
        df = pd.merge_asof(df, target_temp, on='_time')
        df = pd.merge_asof(df, mode, on='_time')
        df = df.set_index('_time')
        change_df = df[['hvac_action', 'target_temp']].shift(1).rename(columns={'hvac_action': 'action_before', 'target_temp': 'target_before'})
        change_events_df = pd.merge(df, change_df, left_index=True, right_index=True).dropna(subset=['hvac_action', 'action_before']).query("(hvac_action != action_before or target_temp != target_before) and hvac_action not in ['fan', 'off'] and current_temp != target_temp")

        stable_df = df.query('current_temp == target_temp').drop(['current_temp', 'target_temp'],axis=1)
        stable_df['stable_time'] = stable_df.index
        change_events_df['temp_delta'] = change_events_df['target_temp'] - change_events_df['current_temp']
        change_events_df = pd.merge_asof(change_events_df, stable_df, left_index=True, right_index=True, by='hvac_action', direction='forward').dropna(subset=['stable_time'])
        change_events_df['time_delta'] = (change_events_df['stable_time'] - change_events_df.index).dt.total_seconds()
        change_events_df['adapt_rate_degrees_per_hr'] = change_events_df['temp_delta'] / (change_events_df['time_delta'] / 3600.0)
        self.log(change_events_df)

class ClimateGoal(hass.Hass):
    def initialize(self):
        self.people_trackers = self.args["people_trackers"]
        self.presence_ent = self.args["presence_ent"]

    def apply_climate_goal(self, kwargs):
        min_to_reach_tolerable = 30 # TODO base on real numbers & configuration
        trackers = [self.get_state(x, attributes='all') for x in self.people_trackers]
        target_state = 'safe'
        for tracker in trackers:
            if tracker['state'] == 'home':
                target_state = 'tolerable'
                break
            if tracker['travel_time_min'] <= min_to_reach_tolerable:
                target_state = 'tolerable'
                break
        presence = self.get_state(self.presence_ent)
        if presence == 'on':
            target_state = 'agreeable'
            # TODO incorporatee room-level tracking info here for preference impl
        # TODO incorporate task overrides here
        # TODO compute the actual target temperature goal (maybe it's actually a high/low range? or maybe it's a state w/ attributes for preference details)
        # TODO publish goal for the room

class ClimateImplementor(hass.Hass):
    def initialize(self):
        self.climate_ent = self.args['climate_entity']
        self.climate_goal = self.args['climate_goal']
        self.outside_temp = self.args['outside_temp']
        self.temperature_ent = self.args['temperature_entity']

    def calculate_target_temp(self):
        climate_goal = self.get_state(self.climate_goal)
        current_temp = self.get_state(self.temperature_ent) # TODO this should be task-linked
        outside_temp = self.get_state(self.outside_temp)

        if climate_goal == 'safe':
            t_high = 80 #self.get_state('input_number.temp_safe_high')
            t_low = 60 #self.get_state('input_number.temp_safe_low')
        if climate_goal == 'tolerable':
            t_high = 74
            t_low = 67
        if climate_goal == 'agreeable':
            t_high = 72
            t_low = 69
        # todo add logic to incorporate preferences here

        mode = 'fan'
        target_temp = None
        if current_temp > t_high:
            if outside_temp > t_high: # make 73 the min outdoors temp for ac
                # A/C mode
                mode = 'cool'
                target_temp = t_high
            else:
                pass # notify to open a window
        elif current_temp < t_low:
            if outside_temp < t_low: # make 63 the max outdoors temp for heat
                mode = 'heat'
                target_temp = t_low
            else:
                pass # notify to open a window
        else:
            pass # we're within comfort settings

        if mode != 'fan':
            # apply calibration
            cal = self.get_state(f'sensor.offset_calibrated_{self.climate_ent}_{self.temperature_ent}')
            if mode == 'heat':
                offset = cal['heating_offset']
            else:
                offset = cal['cooling_offset']
            offset = np.clip(offset, -4, 4)
            target_temp += offset
            self.call_service('climate/set_temperature', entity_id = self.climate_ent, temperature = target_temp, hvac_mode = mode)
        else:
            self.call_service('climate/set_hvac_mode', entity_id = self.climate_ent, hvac_mode = 'fan_only')
