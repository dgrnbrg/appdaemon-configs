import hassapi as hass
import adbase as ad
import datetime
import math

def color_temperature_kelvin_to_mired(kelvin_temperature: float) -> int:
    """Convert degrees kelvin to mired shift."""
    return math.floor(1000000 / kelvin_temperature)

def color_temperature_mired_to_kelvin(mired_temperature: float) -> int:
    """Convert absolute mired shift to degrees kelvin."""
    return math.floor(1000000 / mired_temperature)

class LightController(hass.Hass):
    @ad.app_lock
    def initialize(self):
        self.light = self.args['light']
        self.do_update = set()
        self.target_brightness = 0
        self.off_transition = self.args.get('off_tranisition', 5)
        self.fake_when_away = self.args.get('fake_when_away', True)
        # self.people_trackers = self.args['people_trackers']
        #if not isinstance(self.people_trackers, list):
        #    self.people_trackers = [self.people_trackers]
        #for t in self.people_trackers:
        #    self.listen_state(self.on_people_tracker_changed, t)
        self.state = 'init'
        #print(f"light controller args: {self.args}")
        self.daily_off_time = self.args.get('daily_off_time', '04:00:00')
        self.triggers = []
        for i, t in enumerate(self.args['triggers']):
            if 'presence' in t and 'task' in t:
                self.error(f"Trigger {t} should have presence or task as the trigger")
            trigger = {'index': i}
            trigger['max_brightness'] = t.get('max_brightness', 100)
            if trigger['max_brightness'] and isinstance(trigger['max_brightness'], str):
                if trigger['max_brightness'].endswith('%'):
                    trigger['max_brightness'] = int(trigger['max_brightness'][:-1])
                else:
                    trigger['max_brightness'] = int(trigger['max_brightness'])
            trigger['transition'] = t.get('transition', 3)
            trigger['target_state'] = t.get('state', 'turned_on')
            conditions = t.get('condition', [])
            if not isinstance(conditions, list):
                conditions = [conditions]
            trigger['conditions'] = conditions
            trigger['on_timers'] = []
            trigger['off_timers'] = []
            trigger['states'] = {}
            trigger['state'] = 'init'
            causes = t.get('task', t.get('presence', None))
            if not isinstance(causes, list):
                causes = [causes]
            trigger['causes'] = causes
            self.log(f"on {self.light}, looking at {causes}")
            for cause in causes:
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
                    xs = [x.trim() for x in cause.split('!=')]
                    #print(f"parsing a negative state override light trigger")
                    entity = xs[0]
                    present_state = None
                    absent_state = xs[1]
                if t.get('turns_on', True):
                    if present_state:
                        self.listen_state(self.trigger_on, entity, new=present_state, duration=t.get('delay_on', 0), trigger=i, immediate=True)
                    else:
                        self.listen_state(self.trigger_on, entity, duration=t.get('delay_on', 0), trigger=i, absent_state=absent_state, immediate=True)
                if t.get('turns_off', True):
                    if absent_state:
                        self.listen_state(self.trigger_off, entity, new=absent_state, duration=t.get('delay_off', 0), trigger=i, immediate=True)
                    else:
                        self.listen_state(self.trigger_off, entity, duration=t.get('delay_off', 0), trigger=i, present_state=present_state, immediate=True)
            self.triggers.append(trigger)
        self.listen_state(self.on_adaptive_lighting_temp, self.args['adaptive_lighting'], attribute='color_temp_mired', immediate=True)
        self.listen_state(self.on_adaptive_lighting_brightness, self.args['adaptive_lighting'], attribute='brightness_pct', immediate=True)
        #self.update_people_tracker()
        self.listen_event(self.service_snoop, "call_service")
        self.run_daily(self.reset_manual, self.daily_off_time)
        self.log(f"Completed initialization for {self.light}")

    @ad.app_lock
    def reset_manual(self, kwargs):
        if self.state == 'manual' or self.state == 'manual_on':
            self.state = 'returning'
            self.update_light()

    @ad.app_lock
    def trigger_off(self, entity, attr, old, new, kwargs):
        if 'present_state' in kwargs: # this may be a false trigger if using a state comparison
            if new == kwargs['present_state']:
                return
        trigger = self.triggers[kwargs['trigger']]
        for cond in trigger['conditions']: # don't trigger if conditions aren't met
            if self.get_state(cond) != 'on':
                return
        old_state = trigger['state']
        trigger['states'][entity] = 'off'
        all_off = True
        for t,v in trigger['states'].items():
            if v != 'off':
                all_off = False
                break
        self.log(f'trigger off for {self.light} because {entity} is off. all off={all_off}. prev={old_state}. states = {trigger["states"]}')
        if all_off:
            trigger['state'] = 'off'
            if old_state != 'off':
                self.update_light()

    @ad.app_lock
    def trigger_on(self, entity, attr, old, new, kwargs):
        if 'absent_state' in kwargs: # this may be a false trigger if using a state comparison
            if new == kwargs['absent_state']:
                return
        self.log(f"Trigger on running for the {kwargs['trigger']} trigger, and the length is {len(self.triggers)}")
        trigger = self.triggers[kwargs['trigger']]
        for cond in trigger['conditions']: # don't trigger if conditions aren't met
            if self.get_state(cond) != 'on':
                return
        old_state = trigger['state']
        trigger['states'][entity] = 'on'
        trigger['state'] = 'on'
        self.log(f'trigger on for {self.light} because {entity} is on. prev={old_state}')
        if old_state != 'on':
            self.update_light()

    @ad.app_lock
    def update_people_tracker(self):
        # TODO figure out how to actually apply this stuff
        self.state = 'away'
        for p in self.people_trackers:
            s = self.get_state(p)
            if s == 'home': # someone is home
                self.state = 'home'

    #def on_people_tracker_changed(self, entity, attribute, old, new, kwargs):
    #    self.update_people_tracker()

    @ad.app_lock
    def on_adaptive_lighting_brightness(self, entity, attribute, old, new, kwargs):
        self.do_update.add('bright')
        self.brightness = new
        self.update_light()

    @ad.app_lock
    def on_adaptive_lighting_temp(self, entity, attribute, old, new, kwargs):
        self.do_update.add('temp')
        self.color_temp = new
        self.update_light()

    @ad.app_lock
    def service_snoop(self, event_name, data, kwargs):
        if data['domain'] != 'light':
            return
        #print(f"service snooped {data}")
        service_data = data['service_data']
        has = False
        if 'entity_id' in service_data:
            entity_id = service_data['entity_id']
            if isinstance(entity_id, list):
                has = self.light in entity_id
            else:
                has = self.light == entity_id
        if has:
            if data['service'] == 'turn_on':
                if 'brightness_pct' in service_data:
                    new_brightness = service_data['brightness_pct']
                    delta = abs(new_brightness - self.target_brightness) / new_brightness
                    if delta > 0.05:
                        # probably was a manual override
                        self.state = 'manual'
                    self.log(f'saw a change in brightness. delta is {delta}. state is now {self.state}')
                elif 'color_temp' in service_data:
                    new_color_temp = service_data['color_temp']
                    delta = abs(new_color_temp - self.color_temp) / new_color_temp
                    if delta > 0.05:
                        # probably was a manual override
                        self.state = 'manual'
                    self.log(f'saw a change in color temp. delta is {delta}. state is now {self.state}')
                elif 'color_temp_kelvin' in service_data:
                    new_color_temp = color_temperature_kelvin_to_mired(service_data['color_temp_kelvin'])
                    delta = abs(new_color_temp - self.color_temp) / new_color_temp
                    if delta > 0.05:
                        # probably was a manual override
                        self.state = 'manual'
                    self.log(f'saw a change in color temp (kelvin). delta is {delta}. state is now {self.state}')
                else:
                    self.log(f"saw {self.light} turn on without settings.")
                    if self.state == 'manual_off':
                        self.log(f"from on: Returning to automatic {service_data}.")
                        self.state = 'returning'
                        self.update_light()
                    else:
                        self.log(f"saw an unexpected change to on, going to manual")
                        self.state = 'manual'
            # check if we did a turn off, and 
            elif data['service'] == 'turn_off' :
                self.log(f"saw {self.light} turn off without settings (cur state = {self.state}).")
                # if the state isn't off or a trigger that is supposed to be turned off
                if self.state != 'off' and isinstance(self.state, int) and self.triggers[self.state]['target_state'] != 'turned_off':
                    self.log(f"saw an unexpected change to off, going to manual")
                    self.state = 'manual_off'
                elif self.state == 'manual': # does turning off mean we return to auto?
                    self.log(f"from off: Returning to automatic {service_data}.")
                    self.state = 'returning'
                    self.update_light()

    def update_light(self):
        if len(self.do_update) != 2:
            return
        # check each trigger to see if it's enabled.
        # also handle the delay functions
        if self.state == 'manual' or self.state == 'manual_off':
            # don't be automatic in this case
            self.log(f"not updating light b/c it's in manual mode")
            return
        for trigger in self.triggers:
            if trigger['state'] == 'on':
                #if self.state != trigger['index']:
                self.state = trigger['index']
                brightness = min(self.brightness, trigger['max_brightness'])
                self.target_brightness = brightness
                if trigger['target_state'] == 'turned_on':
                    self.get_entity(self.light).turn_on(brightness_pct=brightness, color_temp=self.color_temp, transition=trigger['transition'])
                    self.log(f"Matched {self.light} trigger {trigger}, setting brightness to {brightness}")
                elif trigger['target_state'] == 'turned_off':
                    self.get_entity(self.light).turn_off(transition=trigger['transition'])
                    self.log(f"Matched {self.light} trigger {trigger}, turning off")
                else:
                    self.log(f"Matched {self.light} trigger {trigger}, but the target_state wasn't understood")
                return
        self.state = 'off'
        # no triggers were active, so either we're off or we're faking
        self.get_entity(self.light).turn_off(transition=self.off_transition)
        self.log(f"no triggers active for {self.light}, turning off")

