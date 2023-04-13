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
