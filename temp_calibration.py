import numpy as np
import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

import warnings
from influxdb_client.client.warnings import MissingPivotFunction
#warnings.simplefilter("ignore", MissingPivotFunction)


client = InfluxDBClient(url="http://192.168.0.112:8086", token='appdaemon-dev:opennow', org='-')
query_api = client.query_api()

def get_sensor_data(entity_id, start='-7d', field="value"):
    q = f'''
    from(bucket: "homeassistant/autogen")
      |> range(start: {start})
      |> filter(fn: (r) => r.entity_id == "{entity_id}" and r._field == "{field}")
      |> pivot(rowKey:["_time"], columnKey: ["entity_id"], valueColumn: "_value")
      |> drop(columns: ["_measurement", "domain", "_start", "_stop"])
    '''
    df = query_api.query_data_frame(q).drop(['result','table','_field'], axis=1)
    if field != 'value':
        return df.rename(columns={entity_id: field})
    else:
        return df

def compute_heat_index(temp_farenheit, relative_humidity):
    # relative humidity should be 0-100
    if temp_farenheit < 80: # seems like the formula below says it doesn't apply below 80ºF
        return temp_farenheit
    t = temp_farenheit
    rh = relative_humidity
    # https://www.weather.gov/media/epz/wxcalc/heatIndex.pdf
    return -42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 6.83783e-3 * t * t - 5.481717e-2 * rh * rh + 1.22874e-3 * t * t * rh + 8.5282e-4 * t * rh * rh - 1.99e-6 * t * t * rh * rh

remote_sensor = 'bedroom'
remote_sensor = 'main_floor'
remote_sensor = 'aysylu_office'

humidity = get_sensor_data(f"{remote_sensor}_sensor_bme280_humidity") # what we want to calibrate to
remote_temp = get_sensor_data(f"{remote_sensor}_sensor_bme280_temperature") # what we want to calibrate to
temp = get_sensor_data("thermostat_2", field="current_temperature") # thermostat's opinion
target_temp= get_sensor_data("thermostat_2", field="temperature") # thermostat's setting
mode = get_sensor_data("thermostat_2", field="hvac_action_str") # fan, heating, or cooling
hi = f"heat index of 80ºF at 90%rh = {compute_heat_index(80, 90)}"


# First, join all data together with the fill forward or something
# Add columns for feels like for the temperatures
## To find offset
# Filter for rows where the thermostat is not in "off" or "fan" mode
# Filter for rows where the thermostat thinks it hit its target
# Group by heat & cool modes to get the heat & cool fixed offsets
## To find speed of action (º/min or something)
# Using shift to find changes, filter for rows when the mode or target changed AND mode is heating or cooling
# Also filter for rows when the target temp was successfully hit
# Join those 2 frames together the initiated change & arrival time pairing
# Group by mode & compute averages (maybe confirm distributions are reasonable)

#print(temp)
#print(humidity)
base_times = pd.concat([x['_time'] for x in [temp, humidity, remote_temp, target_temp, mode]])
base_times = base_times.sort_values().drop_duplicates()
df = pd.merge_asof(base_times, temp, on='_time')
df = pd.merge_asof(df, target_temp, on='_time')
df = pd.merge_asof(df, humidity, on='_time')
df = pd.merge_asof(df, mode, on='_time')
df = pd.merge_asof(df, remote_temp, on='_time')
offset_df = df.query("hvac_action_str in ('heating', 'cooling') and current_temperature == temperature")
offset_df._time= offset_df._time.dt.tz_convert('America/New_York')
offset_df['delta'] = offset_df[f'{remote_sensor}_sensor_bme280_temperature'] - offset_df['current_temperature']
print(offset_df)
print(offset_df[['hvac_action_str', 'delta']].groupby('hvac_action_str').describe())


df = df.set_index('_time')
change_df = df[['hvac_action_str', 'temperature']].shift(1).rename(columns={'hvac_action_str': 'action_before', 'temperature': 'target_before'})
change_events_df = pd.merge(df, change_df, left_index=True, right_index=True).dropna(subset=['hvac_action_str', 'action_before']).query("(hvac_action_str != action_before and hvac_action_str not in ['fan', 'off']) or temperature != target_before and hvac_action_str not in ['fan', 'off']")
print(change_events_df)

# Then, we'll have hot & cold offsets and hot & cold speeds
# These values may need to be additionally bucketed/broken down by time of day, difference between indoor/outdoor temp, or weather
