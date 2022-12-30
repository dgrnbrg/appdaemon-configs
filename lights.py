import hassapi as hass
import adbase as ad
import datetime
import math


def parse_conditional_expr(cause):
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
        xs = [x.strip() for x in cause.split('!=')]
        #print(f"parsing a negative state override light trigger")
        entity = xs[0]
        present_state = None
        absent_state = xs[1]
    return present_state, absent_state, entity

class LightController(hass.Hass):
    """
    This does presence-based light control.

    First, for global settings:
    - adaptive_lighting is used as a source for automatic brightness and color temperature adjustment for a circadian household
    - light is the actual controlled light entity for this
    - off_transition is the number of seconds to fade to off, by default
    - daily_off_time is when the light is reset to its automatic state

    Then, we have triggers. The triggers each have their constituent information continuously updated.
    The first active trigger is applied to the light, so that triggers can preempt others.
    No active triggers means the light is off.
    Triggers have many configurable settings:
    - transition is the duration that the light fades when activating this trigger
    - delay_on/delay_off requires that the trigger's presence/task inputs to be on/off for that many seconds before triggering on/off
    - state can be "turned_off" for a trigger that turns the light off completely
    - max_brightness clamps the brightness of the circadian lighting (for mood, e.g. when watching tv or going to the bathroom late at night)
    - presence/task are the same. When any of the conditions are true (they can be on/off entities, or a text entity with == or !=), the trigger turns on. When they're all false, the trigger turns off.
    - condition is similar to presence, except that every condition must be true for the trigger to turn on.

    Presence and condition can be thought of as the following:
    presence is used for whether something is being done, such as being in a space, sleeping, or doing an activity
    condition is used to validate that it's an appropriate time, such as whether it's dark out
    """
    @ad.app_lock
    def initialize(self):
        self.light = self.args['light']
        self.light_name = self.light.split('.')[1] # drop the domain
        self.reautomate_button = f'button.reautomate_{self.light_name}'
        self.guest_mode_switch = f'input_boolean.guest_mode_{self.light_name}'
        self.do_update = set()
        self.target_brightness = 0
        self.off_transition = self.args.get('off_transition', 5)
        self.fake_when_away = self.args.get('fake_when_away', True)
        self.state = 'init'
        #print(f"light controller args: {self.args}")
        self.daily_off_time = self.args.get('daily_off_time', '04:00:00')
        self.triggers = []
        for i, t in enumerate(self.args.get('triggers',[]) or []):
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
            trigger['on_timers'] = []
            trigger['off_timers'] = []
            trigger['states'] = {}
            trigger['state'] = 'init'
            # causes can have 2 subheadings, "presence/task" and "condition".
            # At least one from "presence/task" must be true, and everything from
            # "condition" must be true, in order to activate the trigger
            any_causes = t.get('task', t.get('presence', None))
            if not isinstance(any_causes, list):
                any_causes = [any_causes]
            trigger['any_causes'] = any_causes
            #self.log(f"on {self.light}, looking at {any_causes}")
            all_causes = t.get('condition', [])
            if not isinstance(all_causes, list):
                all_causes = [all_causes]
            any_causes = [parse_conditional_expr(x) for x in any_causes]
            all_causes = [parse_conditional_expr(x) for x in all_causes]
            pes = trigger['presence_entities'] = [e for (_,_,e) in any_causes]
            ces = trigger['condition_entities'] = [e for (_,_,e) in all_causes]
            if len(pes) + len(ces) != len(set(ces + pes)):
                raise ValueError(f"Condition and presence entities must appear only once each")
            for present_state, absent_state, entity in any_causes + all_causes:
                if t.get('turns_on', True):
                    if entity in pes:
                        duration = t.get('delay_on', 0)
                    else:
                        duration = 0
                    if present_state:
                        self.listen_state(self.trigger_on, entity, new=present_state, duration=duration, trigger=i, immediate=True)
                    else:
                        self.listen_state(self.trigger_on, entity, duration=duration, trigger=i, absent_state=absent_state, immediate=True)
                if t.get('turns_off', True):
                    if entity in pes:
                        duration = t.get('delay_off', 0)
                    else:
                        duration = 0
                    if absent_state:
                        self.listen_state(self.trigger_off, entity, new=absent_state, duration=duration, trigger=i, immediate=True)
                    else:
                        self.listen_state(self.trigger_off, entity, duration=duration, trigger=i, present_state=present_state, immediate=True)
            self.triggers.append(trigger)
        self.listen_state(self.on_adaptive_lighting_temp, self.args['adaptive_lighting'], attribute='color_temp_kelvin', immediate=True)
        self.listen_state(self.on_adaptive_lighting_brightness, self.args['adaptive_lighting'], attribute='brightness_pct', immediate=True)
        self.listen_event(self.service_snoop, "call_service")
        self.run_daily(self.reset_manual, self.daily_off_time)
        self.log(f"Completed initialization for {self.light}")
        self.get_entity(self.reautomate_button).set_state(state='unknown', attributes={'friendly_name': f'Reautomate {self.light}'})
        guest_switch_ent = self.get_entity(self.guest_mode_switch)
        if not guest_switch_ent.exists():
            guest_switch_ent.set_state(state='off', attributes={'friendly_name': f'Guest mode for {self.light}'})
        elif guest_switch_ent.get_state() == 'on':
            self.state = 'guest'

    @ad.app_lock
    def reset_manual(self, kwargs):
        if self.get_state(self.guest_mode_switch) == 'on':
            # skip reset to manual when the guest mode is on
            return
        if self.state == 'manual' or self.state == 'manual_off':
            self.state = 'returning'
            self.update_light()

    @ad.app_lock
    def trigger_off(self, entity, attr, old, new, kwargs):
        if 'present_state' in kwargs: # this may be a false trigger if using a state comparison
            if new == kwargs['present_state']:
                return
        trigger = self.triggers[kwargs['trigger']]
        old_state = trigger['state']
        trigger['states'][entity] = 'off'
        all_presence_off = True
        any_condition_off = False
        for t,v in trigger['states'].items():
            if v != 'off' and t in trigger['presence_entities']:
                all_presence_off = False
            if v == 'off' and t in trigger['condition_entities']:
                any_condition_off = True
        #self.log(f'trigger off for {self.light} because {entity} is off. all presence off={all_presence_off}. all condition off={any_condition_off}. prev={old_state}. states = {trigger["states"]}')
        if all_presence_off or any_condition_off:
            trigger['state'] = 'off'
            if old_state != 'off':
                self.update_light()

    @ad.app_lock
    def trigger_on(self, entity, attr, old, new, kwargs):
        if 'absent_state' in kwargs: # this may be a false trigger if using a state comparison
            if new == kwargs['absent_state']:
                return
        #self.log(f"Trigger on running for the {kwargs['trigger']} trigger, and the length is {len(self.triggers)}")
        trigger = self.triggers[kwargs['trigger']]
        old_state = trigger['state']
        trigger['states'][entity] = 'on'
        all_conditions_on = True
        any_presence_on = False
        for t,v in trigger['states'].items():
            if v == 'on' and t in trigger['presence_entities']:
                any_presence_on = True
            if v != 'on' and t in trigger['condition_entities']:
                all_conditions_on = False
        if all_conditions_on and any_presence_on:
            trigger['state'] = 'on'
            #self.log(f'trigger on for {self.light} because {entity} went on. any presence on={any_presence_on}. all conditions on={all_conditions_on}. prev={old_state}')
            if old_state != 'on':
                self.update_light()

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
        if data['domain'] != 'light' and data['domain'] != 'button' and data['domain'] != 'input_boolean':
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
        if data['domain'] == 'button' and data['service'] == 'press' and (entity_id == self.reautomate_button or entity_id == [self.reautomate_button]):
            self.state = 'returning'
            self.update_light()
            self.log(f'reautomating {self.light}')
            return
        guest_switch = self.get_entity(self.guest_mode_switch)
        if data['domain'] == 'input_boolean' and (entity_id == self.guest_mode_switch or entity_id == [self.guest_mode_switch]):
            if data['service'] == 'turn_on':
                guest_switch.set_state(state='on')
                self.state = 'guest'
            elif data['service'] == 'turn_off':
                guest_switch.set_state(state='off')
                self.state = 'returning'
            elif data['service'] == 'toggle':
                if guest_switch.get_state() == 'on':
                    guest_switch.set_state(state='off')
                    self.state = 'returning'
                elif guest_switch.get_state() == 'off':
                    self.state = 'guest'
                    guest_switch.set_state(state='on')
            self.update_light()
            return
        if guest_switch.get_state() == 'on':
            # we shouldn't do any of the manual control stuff
            #self.log(f"not handling manual overrides due to guest mode {self.light}")
            return
        if has:
            # janky support for toggle
            if data['domain'] == 'light' and data['service'] == 'toggle':
                cur_state = self.get_state(self.light)
                print(f"toggle reveals the entity is {cur_state}")
                if cur_state == 'on':
                    data['service'] = 'turn_off'
                elif cur_state == 'off':
                    data['service'] = 'turn_on'
                else:
                    self.log(f"Unexpected state for {self.light}: {cur_state}")
            if data['domain'] == 'light' and data['service'] == 'turn_on':
                if 'brightness_pct' in service_data:
                    new_brightness = service_data['brightness_pct']
                    delta = abs(new_brightness - self.target_brightness) / new_brightness
                    if delta > 0.05:
                        # probably was a manual override
                        self.state = 'manual'
                        self.update_light()
                    #self.log(f'saw a change in brightness. delta is {delta}. state is now {self.state}')
                elif 'color_temp' in service_data:
                    new_color_temp = service_data['color_temp']
                    delta = abs(new_color_temp - self.color_temp) / new_color_temp
                    if delta > 0.05:
                        # probably was a manual override
                        self.state = 'manual'
                        self.update_light()
                    #self.log(f'saw a change in color temp. delta is {delta}. state is now {self.state}')
                elif 'color_temp_kelvin' in service_data or 'kelvin' in service_data:
                    new_color_temp = service_data.get('kelvin', service_data.get('color_temp_kelvin'))
                    delta = abs(new_color_temp - self.color_temp) / new_color_temp
                    if delta > 0.05:
                        # probably was a manual override
                        self.state = 'manual'
                        self.update_light()
                    #self.log(f'saw a change in color temp (kelvin). delta is {delta}. state is now {self.state}')
                else:
                    #self.log(f"saw {self.light} turn on without settings.")
                    if self.state == 'manual_off':
                        #self.log(f"from on: Returning to automatic {service_data}.")
                        self.state = 'returning'
                    elif self.state == 'off' or isinstance(self.state, int) and self.triggers[self.state]['target_state'] == 'turned_off': # it turned on but it should be off
                        #self.log(f"saw an unexpected change to on, going to manual")
                        self.state = 'manual'
                        self.update_light()
            # check if we did a turn off, and 
            elif data['domain'] == 'light' and data['service'] == 'turn_off' :
                #self.log(f"saw {self.light} turn off without settings (cur state = {self.state}).")
                # if the state isn't off or a trigger that is supposed to be turned off
                if self.state != 'off' and isinstance(self.state, int) and self.triggers[self.state]['target_state'] != 'turned_off':
                    #self.log(f"saw an unexpected change to off, going to manual")
                    self.state = 'manual_off'
                    self.update_light()
                elif self.state == 'manual': # does turning off mean we return to auto?
                    #self.log(f"from off: Returning to automatic {service_data}.")
                    self.state = 'returning'
                    self.update_light()

    def update_light(self):
        if len(self.do_update) != 2:
            return
        def update_stored_state():
            # make sure we have the up-to-date state stored
            state_entity = self.get_entity(f"sensor.light_state_{self.light_name}")
            if str(self.state) != state_entity.get_state():
                old_state = state_entity.get_state()
                state_repr = str(self.state)
                if state_repr == 'manual_off':
                    state_repr = 'manual'
                state_entity.set_state(state=state_repr, attributes={'old_state': old_state, 'active_triggers': [x['index'] for x in self.triggers if x['state'] == 'on'], 'internal_state': self.state})
        # check each trigger to see if it's enabled.
        # also handle the delay functions
        if self.state == 'manual' or self.state == 'manual_off':
            # don't be automatic in this case
            #self.log(f"not updating light b/c it's in manual mode")
            update_stored_state()
            return
        if self.state == 'guest':
            # don't be automatic, except match color temperature
            update_stored_state()
            light_ent = self.get_entity(self.light)
            light_ent_state = light_ent.get_state()
            if light_ent_state == 'on':
                #self.log(f'updating color temperature for guest mode light {self.light} = {light_ent_state} to {self.color_temp}')
                light_ent.turn_on(color_temp_kelvin=self.color_temp)
            else:
                pass
                #self.log(f'guest mode light active {self.light} = {light_ent_state}')
            return
        for trigger in self.triggers:
            if trigger['state'] == 'on':
                #if self.state != trigger['index']:
                self.state = trigger['index']
                brightness = min(self.brightness, trigger['max_brightness'])
                self.target_brightness = brightness
                if trigger['target_state'] == 'turned_on':
                    #self.log(f"Matched {self.light} trigger {trigger}, setting brightness to {brightness} and temp to {self.color_temp}")
                    self.get_entity(self.light).turn_on(brightness_pct=brightness, kelvin=self.color_temp, transition=trigger['transition'])
                elif trigger['target_state'] == 'turned_off':
                    self.get_entity(self.light).turn_off(transition=trigger['transition'])
                    #self.log(f"Matched {self.light} trigger {trigger}, turning off")
                else:
                    self.log(f"Matched {self.light} trigger {trigger}, but the target_state wasn't understood")
                update_stored_state()
                return
        self.state = 'off'
        # no triggers were active, so either we're off or we're faking
        self.get_entity(self.light).turn_off(transition=self.off_transition)
        #self.log(f"no triggers active for {self.light}, turning off")
        update_stored_state()

