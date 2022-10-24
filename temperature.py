import datetime
import math
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

# TODO maybe this should be a linear model instead. Weather, outdoor temp, indoor temp, heating/cool mode. maybe cloudiness, indoor humidity?
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
                mode = get_sensor_data(self.thermostat_ent + ".state", column='hvac_mode')
                base_times = pd.concat([x['_time'] for x in [temp, remote_temp, target_temp, mode]])
                base_times = base_times.sort_values().drop_duplicates()
                df = pd.merge_asof(base_times, temp, on='_time')
                df = pd.merge_asof(df, target_temp, on='_time')
                df = pd.merge_asof(df, mode, on='_time')
                df = pd.merge_asof(df, remote_temp, on='_time')
                offset_df = df.query("hvac_mode in ('heat', 'cool') and current_temp == target_temp").copy()
                offset_df._time= offset_df._time.dt.tz_convert('America/New_York')
                offset_df['delta'] = offset_df['remote_temp'] - offset_df['current_temp']
                result = offset_df[['hvac_mode', 'delta']].groupby('hvac_mode').describe()
                self.log(result)
                attrs = {
                    'heating_offset': 0,
                    'heating_stddev': 0,
                    'cooling_offset': 0,
                    'cooling_stddev': 0,
                }
                if 'heat' in result.index:
                    attrs = {
                        **attrs,
                        'heating_offset': result.loc['heat'].loc[('delta','mean')],
                        'heating_stddev': result.loc['heat'].loc[('delta','std')],
                    }
                if 'cool' in result.index:
                    attrs = {
                        **attrs,
                        'cooling_offset': result.loc['cool'].loc[('delta','mean')],
                        'cooling_stddev': result.loc['cool'].loc[('delta','std')],
                    }
                offset_entity.set_state(state='on', attributes = attrs)
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
        mode = get_sensor_data(self.thermostat_ent + ".state", column="hvac_mode")
        base_times = pd.concat([x['_time'] for x in [temp, target_temp, mode]])
        base_times = base_times.sort_values().drop_duplicates()
        df = pd.merge_asof(base_times, temp, on='_time')
        df = pd.merge_asof(df, target_temp, on='_time')
        df = pd.merge_asof(df, mode, on='_time')
        df['_time'] = df['_time'].dt.tz_convert('America/New_York')
        df = df.set_index('_time')
        change_df = df[['hvac_mode', 'target_temp']].shift(1).rename(columns={'hvac_mode': 'mode_before', 'target_temp': 'target_before'})
        change_events_df = pd.merge(df, change_df, left_index=True, right_index=True).dropna(subset=['hvac_mode', 'mode_before'])
        change_events_df = change_events_df.query("(hvac_mode != mode_before or target_temp != target_before) and hvac_mode not in ['fan', 'off'] and current_temp != target_temp")

        stable_df = df.query('current_temp == target_temp').drop(['current_temp'],axis=1).rename(columns={'target_temp': 'stable_temp'})
        stable_df['stable_time'] = stable_df.index
        change_events_df['temp_delta'] = change_events_df['target_temp'] - change_events_df['current_temp']
        change_events_df = pd.merge_asof(change_events_df, stable_df, left_index=True, right_index=True, by='hvac_mode', direction='forward').dropna(subset=['stable_time'])
        change_events_df = change_events_df.query("(temp_delta > 0 and hvac_mode == 'heat') or (temp_delta < 0 and hvac_mode == 'cool')").copy()
        change_events_df['time_delta'] = (change_events_df['stable_time'] - change_events_df.index).dt.total_seconds()
        change_events_df['adapt_rate_degrees_per_hr'] = change_events_df['temp_delta'] / (change_events_df['time_delta'] / 3600.0)
        self.log(change_events_df)


class ClimateGoal(hass.Hass):
    def initialize(self):
        self.preferences = self.args['preferences']
        self.people_trackers = self.args["people_trackers"]
        self.presence_ent = self.args["presence_ent"]
        self.tasks = self.args.get('tasks',[])
        self.room = self.args['room']
        self.temp_ent = self.args['temp_ent']
        runtime = datetime.time(0, 0, 0)
        self.run_in(self.apply_climate_goal, 0)
        self.run_hourly(self.apply_climate_goal, runtime)
        for tracker in self.people_trackers:
            self.listen_state(self.on_state_changed, tracker)
        if self.presence_ent != True:
            self.listen_state(self.on_state_changed, self.presence_ent)
        for task in self.tasks:
            self.listen_state(self.on_state_changed, f'input_boolean.{task}')
        # NB we shouldn't actually update b/c the room's temp doesn't affect the goal
        #self.listen_state(self.on_state_changed, temp_ent)

    def on_state_changed(self, entity, attribute, old, new, kwargs):
        self.log(f"triggered update due to change in {entity} {attribute} from {old} to {new}")
        self.apply_climate_goal({})

    def apply_climate_goal(self, kwargs):
        min_to_reach_tolerable = 30 # TODO base on real numbers & configuration
        trackers = [self.get_state(x, attribute='all') for x in self.people_trackers]
        target_state = 'safe'
        for tracker in trackers:
            self.log(f"looking at tracker for climate goal: {tracker}")
            if tracker['state'] == 'home':
                target_state = 'tolerable'
                break
            if tracker['attributes']['travel_time_min'] <= min_to_reach_tolerable and tracker['attributes']['dir_of_travel'] == 'Towards':
                target_state = 'tolerable'
                break
        if self.presence_ent == True:
            if target_state == 'tolerable': # only activate if we're home
                target_state = 'agreeable'
        else:
            presence = self.get_state(self.presence_ent)
            if presence == 'on':
                target_state = 'agreeable'
                # TODO incorporatee room-level tracking info here for preference impl
        attrs = self.preferences[target_state].copy()
        self.log(f"setting climate goal for {self.room} to {target_state}, initial attrs {attrs}")
        for task in self.tasks:
            if self.get_state(f'input_boolean.{task}') == 'on':
                task_cfg = self.tasks[task]
                if 'nudge' in task_cfg: # TODO nothing tests this, yet
                    attrs['high'] += task_cfg['nudge']
                    attrs['low'] += task_cfg['nudge']
                else:
                    attrs = task_cfg.copy()
                self.log(f"applying task modifier {task} (cfg={task_cfg}) => new attrs = {attrs}")
        goal_ent = self.get_entity(f"sensor.climate_goal_{self.room}")
        attrs['temp_sensor'] = self.temp_ent
        if 'selfish' not in attrs: # apparently it saves the last time if not updated?
            attrs['selfish'] = False
        goal_ent.set_state(state=target_state, attributes=attrs)

class ClimateImplementor(hass.Hass):
    def initialize(self):
        self.manual_control = False # when someone has taken manual control, this is a dict from room to goal at that time (used to detect when to revert to automatic)
        self.last_setting = None # last climate setting, used to detect taking control
        self.climate_ent = self.args['climate_entity']
        self.weather_ent = self.args['weather_entity']
        self.window_exchange_min_diff = self.args.get('window_temp_diff',2)
        self.rooms = self.args['rooms']
        self.run_in(self.calculate, 0)
        runtime = datetime.time(0, 0, 0)
        self.run_hourly(self.calculate, runtime)
        for room in self.rooms:
            self.listen_state(self.on_goal_changed, f"sensor.climate_goal_{room}")
        self.listen_state(self.on_climate_changed, self.climate_ent)
        self.listen_state(self.on_state_changed, self.weather_ent)
        self.tracked_temp_sensors = {}
        self.tracked_temp_sensors_refcount = {}
        self.prior_goals = {} # stores the last goal for each associated room
        for room in self.rooms:
            goal = self.get_state(f"sensor.climate_goal_{room}", attribute='all')
            temp_ent = goal['attributes']['temp_sensor']
            if temp_ent not in self.tracked_temp_sensors:
                self.tracked_temp_sensors[temp_ent] = self.listen_state(self.room_temp_changed, temp_ent)
                self.tracked_temp_sensors_refcount[temp_ent] = 1
            else:
                self.tracked_temp_sensors_refcount[temp_ent] += 1

    def on_climate_changed(self, entity, attribute, old, new, kwargs):
        temp, mode = self.last_setting
        climate = self.get_state(self.climate_ent, attribute='all')
        newtemp = climate['attributes']['temperature']
        newmode = climate['state']
        if newmode != mode or newtemp != temp:
            self.log(f"Climate was changed manually. Last time we set it to {mode} and {temp}, but now it's {newmode} and {newtemp}")
            self.manual_control = {
                    room: self.get_state(f"sensor.climate_goal_{room}")
                    for room in self.rooms
            }
        self.log(f"triggered update due to climate change in {entity} {attribute} from {old} to {new}")
        self.calculate({})

    def room_temp_changed(self, entity, attribute, old, new, kwargs):
        self.log(f"triggered update due to room temp change in {entity} {attribute} from {old} to {new}")
        self.calculate({})
        if attribute == 'temp_sensor':
            self.tracked_temp_sensors_refcount[old] -= 1
            if self.tracked_temp_sensors_refcount[old] == 0:
                self.cancel_listen_state(old)
                del self.tracked_temp_sensors[old]
            self.tracked_temp_sensors[new] = self.listen_state(self.on_state_changed, temp_ent)
            self.log("updated the temp sensor for room ^")

    def on_goal_changed(self, entity, attribute, old, new, kwargs):
        self.log(f"triggered update due to change in goal for {entity} {attribute} from {old} to {new}")
        numChanged = 0
        for room in self.rooms:
            goal = self.get_state(f"sensor.climate_goal_{room}", attribute='all')
            if goal['state'] != self.manual_control[room] or goal['state'] == 'safe':
                numChanged += 1
        if numChanged == len(self.rooms):
            self.manual_control = False
        self.calculate({})

    def on_state_changed(self, entity, attribute, old, new, kwargs):
        self.log(f"triggered update due to change in {entity} {attribute} from {old} to {new}")
        self.calculate({})

    def calculate(self, kwargs):
        if self.manual_control:
            self.log(f"Manual control detected, not making changes")
            return
        thermostat_ent_parts = self.climate_ent.split('.')
        temp_impl_entity = self.get_entity(f"sensor.impl_{thermostat_ent_parts[1]}_temperature")
        mode_impl_entity = self.get_entity(f"sensor.impl_{thermostat_ent_parts[1]}_mode")
        room_goals = {}
        room_calibration = {}
        has_selfish = False
        for room in self.rooms:
            self.log(f'Fetching data for room {room}')
            goal = self.get_state(f"sensor.climate_goal_{room}", attribute='all')
            remote_temp_ent_parts = goal['attributes']['temp_sensor'].split('.')
            calibration = self.get_state(f"sensor.offset_calibrated_{thermostat_ent_parts[1]}_{remote_temp_ent_parts[1]}", attribute='all')
            if goal['attributes'].get('selfish', False):
                has_selfish = True
            room_goals[room] = goal
            room_calibration[room] = calibration
            self.log(f"Got data for room {room} (selfish={goal['attributes'].get('selfish', False)})")
        # First decide if we're heating, cooling, or failing (TODO alert somehow)
        goal_mode = None
        goal_rooms = {} # maps room to desired change in temperature
        # These 2 are used to determine whether a window could help by tracking the greatest need
        min_upper_temp = 100
        max_lower_temp = 0
        for room in self.rooms:
            # Scan all the rooms & find the heat or cooling deltas and the bounds to see if passive exchange is doable
            goal = room_goals[room]
            if has_selfish and not goal['attributes'].get('selfish', False):
                # if a room is being selfish, skip other rooms
                continue
            calibration = room_calibration[room]
            cur_temp = float(self.get_state(goal['attributes']['temp_sensor']))
            if cur_temp <= goal['attributes']['low']:
                goal_rooms[room] = goal['attributes']['low'] - cur_temp
                max_lower_temp = max(max_lower_temp, goal['attributes']['low'])
            if cur_temp >= goal['attributes']['high']:
                goal_rooms[room] = goal['attributes']['high'] - cur_temp
                min_upper_temp = min(min_upper_temp, goal['attributes']['high'])
            self.log(f"after {room} (cur={cur_temp}) with goal {goal['attributes']}. min_upper_temp={min_upper_temp} max_lower_temp={max_lower_temp}")
        # get the biggest delta room, and try that mode
        # This way if there's a heating/cooling conflict, we help the saddest room
        if goal_rooms:
            biggest_delta = max(goal_rooms, key=lambda x: abs(goal_rooms[x]))
            self.log(f"room with greatest temp change delta is {biggest_delta} (delta={goal_rooms[biggest_delta]})")
            if goal_rooms[biggest_delta] < 0:
                goal_mode = 'cooling'
            elif goal_rooms[biggest_delta] > 0:
                goal_mode = 'heating'
            else:
                self.error(f"Invalid goal room delta")
            self.log(f"Given that room delta, we will use goal_mode = {goal_mode}")
        
        # this is where we try to open a window
        outside_weather = self.get_state(self.weather_ent, attribute='all')
        outside_temp = outside_weather['attributes']['temperature']
        passive_cooling_eligible = outside_temp + self.window_exchange_min_diff < min_upper_temp
        passive_heating_eligible = outside_temp - self.window_exchange_min_diff > max_lower_temp
        if outside_weather['state'] in ['sunny', 'partlycloudy', 'cloudy'] and (goal_mode == 'heating' and passive_heating_eligible) or (goal_mode == 'cooling' and passive_cooling_eligible):
            # suggest opening a window
            #self.call_service(
            #        'notify/mobile_app_david_iphone',
            #        message=f"Consider opening a window since it's {outside_weather['state']} and we want to {goal_mode[:4]} in the {', '.join(goal_rooms)}")
            # TODO ideally we would wait for a while, then resume if the window doesn't open if it's got a sensor
            pass


        if goal_mode == 'cooling' and outside_temp < self.args['min_temp_for_ac']:
            self.error(f"Want to cool (cur={cur_temp}, ceiling={goal['attributes']['high']}), but it's too cold outside ({outside_temp})")
            mode_impl_entity.set_state(state="cannot_cool")
            temp_impl_entity.set_state(state=0)
            return
        if goal_mode == 'heating' and outside_temp > self.args['max_temp_for_heat']:
            self.error(f"Want to heat (cur={cur_temp}, floor={goal['attributes']['low']}), but it's too warm outside ({outside_temp})")
            mode_impl_entity.set_state(state="cannot_heat")
            temp_impl_entity.set_state(state=0)
            return
        if goal_mode is None:
            any_goals_changed = []
            for room, goal in room_goals.items():
                prior = self.prior_goals.get(room, None)
                if goal['state'] != prior:
                    any_goals_changed.append(room)
            if any_goals_changed:
                # In this case, since a goal has changed, we should still reimplement. otherwise, we'll get "stuck" in the tightest bounds since it always satisfies
                thermostat_mode = self.get_state(self.climate_ent)
                if thermostat_mode == 'heat':
                    goal_mode = 'heating'
                elif thermostat_mode == 'cool':
                    goal_mode = 'cooling'
                else:
                    self.error(f"Thermostat mode {thermostat_mode} isn't heat or cool, so it's not clear how to loosen the bound")
                    mode_impl_entity.set_state(state="unknown_mode")
                    temp_impl_entity.set_state(state=0)
                    return
                self.log(f"since {','.join(any_goals_changed)} changed their goals, we are continuing {goal_mode} (thermostat currently in {thermostat_mode}) but changing the thermostat for cost efficiency")
            else:
                self.log("Temperature is within comfort range, so not changing thermostat")
                #self.call_service('climate/set_hvac_mode', entity_id = self.climate_ent, hvac_mode = 'fan_only')
                mode_impl_entity.set_state(state="within_comfort")
                temp_impl_entity.set_state(state=0)
                return

        # Next find a low-high spread in the climate ent's temp domain
        if goal_mode == 'heating':
            target_temp = 60
        elif goal_mode == 'cooling':
            target_temp = 80
        else:
            target_temp = 70
            self.error(f"invalid goal_mode = {goal_mode}")
        for room in self.rooms:
            goal = room_goals[room]
            calibration = room_calibration[room]
            offset = calibration['attributes'][f'{goal_mode}_offset']
            if goal_mode == 'heating':
                room_target = 'low'
            elif goal_mode == 'cooling':
                room_target = 'high'
            room_target_temp = goal['attributes'][room_target]
            room_target_temp -= offset
            if goal_mode == 'heating':
                target_temp = max(target_temp, room_target_temp)
            elif goal_mode == 'cooling':
                target_temp = min(target_temp, room_target_temp)
            self.prior_goals[room] = goal['state']
            self.log(f'after {room} with goal {goal_mode} target climate temp is {target_temp} (room wants {room_target_temp}')
        # next, we must do a pass to see whether this target temp would put any rooms out of spec
        # in this case, we must warn & possibly compromise so that the offset is similar for each room?
        # find the room that would be most out of bounds for this solution, and compromise with it
        most_out_of_bounds = None
        most_out_of_bounds_room = None
        for room in self.rooms:
            goal = room_goals[room]
            calibration = room_calibration[room]
            offset = calibration['attributes'][f'{goal_mode}_offset']
            room_expected_temp = target_temp + offset
            if goal_mode == 'heating' and room_expected_temp > goal['attributes']['high']:
                self.log(f'{room} would be too hot given the current plan to set at {target_temp}')
                if most_out_of_bounds is None or most_out_of_bounds < room_expected_temp:
                    most_out_of_bounds = goal['attributes']['high'] - offset
                    most_out_of_bounds_room = room
                    self.log(f'{room} would be most out of bounds, which is {most_out_of_bounds}')
            elif goal_mode == 'cooling' and room_expected_temp < goal['attributes']['low']:
                self.log(f'{room} would be too cold given the current plan to set at {target_temp}')
                if most_out_of_bounds is None or most_out_of_bounds > room_expected_temp:
                    most_out_of_bounds = goal['attributes']['low'] - offset
                    most_out_of_bounds_room = room
                    self.log(f'{room} would be most out of bounds, which is {most_out_of_bounds}')
        # compromise
        if most_out_of_bounds is not None:
            compromise = (most_out_of_bounds + target_temp) / 2.0
            self.log(f"target would have been {target_temp}, but because of {most_out_of_bounds_room} requiring a minimum setting of {most_out_of_bounds}, we'll compromise to {compromise}")
            target_temp = compromise
        # set the thermostat
        self.log(f"calculated climate impl: {goal_mode} to {target_temp}")
        if goal_mode == 'heating':
            target_temp = math.ceil(target_temp)
        elif goal_mode == 'cooling':
            target_temp = math.floor(target_temp)
        self.log(f"rounded climate impl to {target_temp}")
        mode_impl_entity.set_state(state=goal_mode)
        temp_impl_entity.set_state(state=target_temp)
        #self.call_service('climate/set_temperature', entity_id = self.climate_ent, temperature = target_temp, hvac_mode = goal_mode[:4])
