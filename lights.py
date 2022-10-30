import hassapi as hass
import math
import datetime

class LightController(hass.Hass):
    def initialize(self):
        self.light = self.args['light']
        self.fake_when_away = self.args.get('fake_when_away', True)
        self.people_trackers = self.args['people_trackers']
        if not isinstance(self.people_trackers, list):
            self.people_trackers = [self.people_trackers]
        #for t in self.people_trackers:
        #    self.listen_state(self.on_people_tracker_changed, t)
        self.state = 'init'
        self.daily_off_time = self.args.get('daily_off_time', '04:00:00')
        self.triggers = []
        for i, t in enumerate(self.args['triggers']):
            if 'presence' in t and 'task' in t:
                self.error(f"Trigger {t} should have presence or task as the trigger")
            trigger = {'index': i}
            trigger['max_brightness'] = t.get('max_brightness', None)
            if trigger['max_brightness'].endswith('%'):
                trigger['max_brightness'] = int(trigger['max_brightness'][:-1])
            trigger['transition'] = t.get('transition', 3)
            trigger['on_timers'] = []
            trigger['off_timers'] = []
            trigger['states'] = {}
            trigger['state'] = 'init'
            tasks = t.get('task', t['presence'])
            if isinstance(trigger['tasks'], list):
                trigger['tasks'] = tasks
            else:
                trigger['tasks'] = [tasks]
            for t in tasks:
                if t.get('turns_on', True):
                    self.listen_state(self.trigger_on, t, new='on', duration=t.get('delay_on', None), trigger=trigger)
                if t.get('turns_off', True):
                    self.listen_state(self.trigger_off, t, new='off', duration=t.get('delay_off', None), trigger=trigger)
            self.triggers.append(trigger)
        self.listen_state(self.on_adaptive_lighting_temp, self.args['adaptive_lighting'], attribute='color_temp_mired', immediate=True)
        self.listen_state(self.on_adaptive_lighting_brightness, self.args['adaptive_lighting'], attribute='brightness_pct', immediate=True)
        #self.update_people_tracker()
        self.listen_event(self.service_snoop, "call_service")
        self.run_daily(self.reset_manual, self.daily_off_time)
        self.update_light()

    def reset_manual(self, kwargs):
        if self.state == 'manual':
            self.state = 'off'
            self.update_light()

    def trigger_off(self, entity, attr, old, new, kwargs):
        old_state = trigger['state']
        trigger = kwargs['trigger']
        trigger['states'][entity] = 'off'
        all_off = True
        for t,v in trigger['states'].items():
            if v != 'off':
                all_off = False
                break
        if all_off:
            trigger['state'] = 'off'
            if old_state != 'off':
                self.update_light()

    def trigger_on(self, entity, attr, old, new, kwargs):
        old_state = trigger['state']
        trigger = kwargs['trigger']
        trigger['states'][entity] = 'on'
        trigger['state'] = 'on'
        if old_state != 'on':
            self.update_light()

    def update_people_tracker(self):
        # TODO figure out how to actually apply this stuff
        self.state = 'away'
        for p in self.people_trackers:
            s = self.get_state(p)
            if s == 'home': # someone is home
                self.state = 'home'

    #def on_people_tracker_changed(self, entity, attribute, old, new, kwargs):
    #    self.update_people_tracker()

    def on_adaptive_lighting_brightness(self, entity, attribute, old, new, kwargs):
        self.brightness = new

    def on_adaptive_lighting_temp(self, entity, attribute, old, new, kwargs):
        self.color_temp = new

    def service_snoop(self, event_name, data, kwargs):
        if data['domain'] == 'light':
            if data['service'] == 'turn_on':
                new_brightness = data['service_data']['brightness_pct']
                if math.abs(new_brightness - self.brightness) / self.brightness > 0.05:
                    # probably was a manual override
                    self.state = 'manual'
            elif data['service'] == 'turn_off':
                new_brightness = data['service_data']['brightness_pct']
                if math.abs(new_brightness - self.brightness) / self.brightness > 0.05:
                    # probably was a manual override
                    self.state = 'off'

    def update_light(self):
        # check each trigger to see if it's enabled.
        # also handle the delay functions
        if self.state == 'manual':
            # don't be automatic in this case
            return
        for trigger in self.triggers:
            if trigger['state'] == 'on':
                if self.state != trigger['index']:
                    self.state = trigger['index']
                    brightness = max(self.brightness, trigger['max_brightness'])
                    self.get_entity(self.light).turn_on(brightness_pct=brightness, color_temp=self.color_temp, transition=self.transition)
                return
        self.state = 'off'
        # no triggers were active, so either we're off or we're faking
        self.get_entity(self.light).turn_off(transition=self.transition)

