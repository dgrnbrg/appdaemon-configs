import hassapi as hass
import adbase as ad
import datetime
import math
import time

class EveningTracker(hass.Hass):
    def initialize(self):
        self.run_at_sunset(self.dusk_cb, offset=int(self.args.get('sunset_offset','0')))
        self.run_at_sunrise(self.morning_cb, offset=int(self.args.get('sunrise_offset','0')))

    def morning_cb(self, kwargs):
        self.turn_off(self.args['tracker'])

    def dusk_cb(self, kwargs):
        self.turn_on(self.args['tracker'])

class RoomAugmenter(hass.Hass):

    def get_arg_as_list(self, name):
        x = self.args.get(name, [])
        if isinstance(x, str):
            x = [x]
        return x

    def initialize(self):
        self.grace_token = None
        self.trapped_token = None
        self.debug_mode = self.args.get('debug', False)
        self.current_state = 'unknown'
        self.retaining_irks = []
        self.sensor_id = self.args['sensor_id']
        self.room_names = set(self.get_arg_as_list('room'))
        self.entity_states = {}
        self.tracker_ents = self.get_arg_as_list('irk_trackers')
        for tracker in self.tracker_ents:
            if self.debug_mode:
                self.log(f"listening to {tracker}")
            self.listen_state(self.irk_tracked, tracker, duration=self.args.get('irk_stability_duration', 30))
            self.entity_states[tracker] = self.get_state(tracker)
        self.opening_ents = self.get_arg_as_list('openings')
        for opening in self.opening_ents:
            if self.debug_mode:
                self.log(f"listening to {opening}")
            self.listen_state(self.opening_state, opening, immediate=True)
            self.entity_states[opening] = 'unknown'
        self.border_ents = self.get_arg_as_list('border')
        for border in self.border_ents:
            if self.debug_mode:
                self.log(f"listening to {border}")
            self.listen_state(self.border_crossed_state, border, immediate=True)
            self.entity_states[border] = 'unknown'
        self.interior_ents = self.get_arg_as_list('interior')
        for interior in self.interior_ents:
            if self.debug_mode:
                self.log(f"listening to {interior}")
            self.listen_state(self.interior_detected_state, interior, immediate=True)
            self.entity_states[interior] = 'unknown'
        if self.interior_ents and not self.border_ents:
            raise ValueError('If you only have activities and no borders, make them all borders and zero acivities please')
        ent = self.get_entity(self.sensor_id)
        ent.set_state(state = 'on' if self.any_borders_on() or self.any_interior_on() else 'off', attributes={'current_state': 'init'})
        if self.debug_mode:
            self.log('finish init')

    def border_crossed_state(self, entity, attr, old, new, kwargs):
        if new == 'unavailable':
            return
        old_agg_state = self.any_borders_on()
        self.entity_states[entity] = new
        if self.current_state.startswith('interior'):
            return # higher priority, so disregard
        did_update = False
        if old_agg_state != self.any_borders_on():
            did_update = True
            if self.any_borders_on():
                self.update_state('border on')
            else:
                self.update_state('border off')
        if self.debug_mode:
            self.log(f'border {entity}={new} any-borders={self.any_borders_on()} {self.current_state} (did_update={did_update})')

    def any_borders_on(self):
        for b in self.border_ents:
            if self.entity_states[b] == 'on':
                return True
        return False

    def interior_detected_state(self, entity, attr, old, new, kwargs):
        if new == 'unavailable':
            return
        self.old_agg_state = self.any_interior_on()
        self.entity_states[entity] = new
        did_update = False
        if old_agg_state != self.any_interior_on():
            did_update = True
            if self.any_interior_on():
                self.update_state('interior on')
            else:
                self.update_state('interior off')
        if self.debug_mode:
            self.log(f'interior {entity}={new} any-interior={self.any_interior_on()} {self.current_state} (did_update={did_update})')

    def opening_is_open(self):
        if not self.opening_ents:
            return True
        for opening in self.opening_ents:
            if self.entity_states[opening] == 'on':
                return True
        return False

    def opening_state(self, entity, attr, old, new, kwargs):
        if new == 'unavailable':
            return
        self.entity_states[entity] = new
        if new == 'on':
            self.update_state('just opened')
        if new == 'off':
            self.update_state('just closed')

    def irk_tracked(self, entity, attr, old, new, kwargs):
        self.entity_states[entity] = new
        if new not in self.room_names:
            if entity in self.retaining_irks:
                self.retaining_irks.remove(entity)
                self.current_state = f'retained by {self.retaining_irks}'
            if not self.retaining_irks:
                self.update_state('no retaining irks')
        if self.debug_mode:
            self.log(f'irk {entity}={new} {self.current_state}')

    def any_interior_on(self):
        for i in self.interior_ents:
            if self.entity_states[i] == 'on':
                return True
        return False

    def close_grace_expired(self, kwargs):
        self.grace_token = None # otherwise, we'll try to cancel ourself later, but we can't because we already fired
        self.update_state('close grace expired')

    def trapped_wait_expired(self, kwargs):
        self.trapped_token = None
        self.update_state('trapped expired')

    def update_state(self, new_state):
        old_state = self.current_state
        publish_state = None
        if new_state == 'interior on':
            # Activity in the interior is retained until we see possible exit motion
            self.current_state = 'interior on'
            publish_state = 'on'
        elif new_state == 'interior off':
            # if any borders are on, we downgrade to them. otherwise, stay in interior off
            if self.any_borders_on():
                self.current_state = 'border on'
            elif not self.any_interior_on() and old_state != 'unknown':
                self.current_state = 'interior off'
            # [test: when should we actually publish?] we only copy the existing state here, b/c it should already be on (or off at initialization)
            if old_state == 'unknown':
                publish_state = self.get_state(self.sensor_id)
            else:
                publish_state = 'on'
        elif new_state == 'border on':
            # If we're not in an interior state, we'll move to the border state
            if not self.current_state.startswith('interior '):
                self.current_state = 'border on'
                publish_state = 'on'
        elif new_state == 'border off' and self.current_state == 'border on':
            if self.opening_is_open():
                self.current_state = 'off'
                self.retaining_irks = [x for x in self.tracker_ents if self.entity_states[x] in self.room_names]
                if self.debug_mode:
                    self.log(f'border on->off, retain = {self.retaining_irks}, rooms={self.room_names}, trackers={self.tracker_ents} tracker_states = {[self.entity_states[x] for x in self.tracker_ents]}')
                if self.retaining_irks:
                    self.current_state = f'retained by {self.retaining_irks}'
                    publish_state = 'on'
                else:
                    self.current_state = 'off'
                    publish_state = 'off'
            else:
                self.current_state = 'trapped'
                publish_state = 'on'
                self.trapped_token = self.run_in(self.trapped_wait_expired, delay=self.args.get('trapped_max_period_seconds', 60*30))
        elif new_state == 'no retaining irks' and self.current_state.startswith('retained by '):
            self.current_state = 'off'
            publish_state = 'off'
        elif new_state == 'just opened':
            self.current_state = 'border on'
            publish_state = 'on'
        elif new_state == 'just closed':
            self.grace_token = self.run_in(self.close_grace_expired, delay=self.args.get('closing_grace_period_seconds', 5))
        elif new_state == 'close grace expired':
            self.current_state = 'off'
            publish_state = 'off'
        elif new_state == 'trapped expired':
            self.current_state = 'off'
            publish_state = 'off'
        # Cancel a delayed off if there is one
        if self.grace_token is not None and new_state != 'just closed':
            if self.current_state == 'trapped':
                if self.debug_mode:
                    self.log(f"we can't be trapped when on grace period, reverting")
                self.current_state = old_state
                publish_state = None
            else:
                self.cancel_timer(self.grace_token)
                self.grace_token = None
        if self.trapped_token is not None and new_state != 'trapped':
            self.cancel_timer(self.trapped_token)
            self.trapped_token = None
        if self.debug_mode:
            self.log(f'Updated state due to {new_state} from {old_state} to {self.current_state}, publishing "{publish_state}"')
        if publish_state is not None:
            ent = self.get_entity(self.sensor_id)
            attrs = {'current_state': self.current_state}
            for k,v in self.entity_states.items():
                attrs[k] = v
            ent.set_state(state = publish_state, attributes=attrs)


class BedStateManager(hass.Hass):
    def initialize(self):
        self.listen_event(self.ios_wake_cb, "ios.action_fired", actionName=self.args['wake_event'])
        self.ssids = self.args['home_ssids']
        self.persons_asleep = {}
        self.persons_away = {}
        runtime = datetime.time(0, 0, 0)
        self.bed_presence = {}
        bp_cfg = self.args['bed_presence']
        for person, cfg in self.args['iphones'].items():
            self.listen_state(self.sleep_check_cb, cfg['charging'], new=lambda x: x.lower() in ['charging', 'full'], immediate=True, person=person, cfg=cfg, constrain_start_time=self.args['bedtime_start'], constrain_end_time=self.args['bedtime_end'])
            self.listen_state(self.sleep_check_cb, cfg['ssid'], new=lambda x: x in self.args['home_ssids'], person=person, cfg=cfg, constrain_start_time=self.args['bedtime_start'], constrain_end_time=self.args['bedtime_end'])
            self.bed_presence[person] = bp_cfg.get(person, bp_cfg['default']) if not isinstance(bp_cfg, str) else bp_cfg
            self.listen_state(self.sleep_check_cb, self.bed_presence[person], new='on', person=person, cfg=cfg, constrain_start_time=self.args['bedtime_start'], constrain_end_time=self.args['bedtime_end'])
            self.persons_asleep[person] = False
            self.persons_away[person] = False
            self.run_hourly(self.check_far_away, runtime, person=person, cfg=cfg)
            self.run_in(self.check_far_away, delay=0, person=person, cfg=cfg)
        self.log(f"bed presence cfg worked out to {self.bed_presence}")

    def check_far_away(self, kwargs):
        person = kwargs['person']
        cfg = kwargs['cfg']
        orig_away = self.persons_away[person]
        if float(self.get_state(cfg['distance'])) > float(self.args['away_distance']):
            self.persons_away[person] = True
        else:
            self.persons_away[person] = False
        if orig_away != self.persons_away[person]:
            msg = 'away' if self.persons_away[person] else 'nearby'
            self.log(f"[check far away] {person} changed to {msg}")

    def ios_wake_cb(self, event_name, data, kwargs):
        person = None
        cfg = None
        for p in self.args['iphones']:
            if p in data['sourceDeviceID']:
                person = p
                cfg = self.args['iphones'][p]
                break
        if person is None:
            self.log(f"ios wake event didn't match any person: {data}")
            return
        self.persons_asleep[person] = False
        self.turn_off(cfg['bed_tracker'])
        self.log(f"ios wake event for {person} registered")
        # if everyone here is awake
        all_awake = True
        for p in [k for k,v in self.persons_away.items() if v == False]:
            if self.persons_asleep[p]:
                all_awake = False
                break
        if all_awake: # everyone home is awake now
            self.turn_off(self.args['bed_tracker'])
            self.log(f"also, now everyone is awake")

    def sleep_check_cb(self, entity, attr, old, new, kwargs):
        person = kwargs['person']
        cfg = kwargs['cfg']
        if self.get_state(self.bed_presence[person]) == 'off':
            # someone must be in bed
            self.log(f"saw {entity} become {new}, but not activating sleep for {person} because {self.bed_presence[person]} isn't in bed")
            return
        if self.get_state(cfg['ssid']) not in self.args['home_ssids']:
            # we must be connected to home wifi
            self.log(f"saw {entity} become {new}, but not activating sleep for {person} because they're not connected to wifi")
            return
        if self.get_state(cfg['charging']).lower() not in ['charging', 'full']:
            # we must be charging
            self.log(f"saw {entity} become {new}, but not activating sleep for {person} because they're not charging")
            return
        self.turn_on(cfg['bed_tracker'])
        self.persons_asleep[person] = True
        self.log(f"sleep for {person} registered")
        for p in [k for k,v in self.persons_away.items() if v == False]:
            if not self.persons_asleep[p]:
                self.log(f"{p} is not away and not asleep")
                return
        self.turn_on(self.args['bed_tracker'])
        self.log(f"also, now everyone is asleep")
