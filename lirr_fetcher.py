import hassapi as hass
import traceback
import adbase as ad
import datetime
import time
import requests


def fetch_data(from_sta, to_sta, label):
    def parse_trip(trip):
        duration_min = (trip['trip_end'] - trip['trip_start']) // 60
        legs = trip['legs']
        train = legs[0]['train']
        details = train['details']
        try:
            stop = details['stops'][0]
            sched_time = datetime.datetime.fromtimestamp(stop['sched_time'])
            sched_time_fmt = sched_time.strftime('%-I:%M %p')
        except:
            sched_time = '??? AM/PM'
        try:
            status = stop['stop_status']
        except:
            status = 'unknown'
        try:
            track = stop['t2s_track']
        except:
            track = 'Track unknown'
        return {'time': sched_time_fmt, 'status': status, 'track': track, 'num_legs': len(legs), 'shuttle': legs[0]['is_shuttle'], 'duration': duration_min, 'numeric_time': stop['sched_time'], 'label': label}
    try:
        u = f'https://backend-unified.mylirr.org/plan?from={from_sta}&to={to_sta}&fares=ALL&time={round(time.time() * 1000)}&arrive_by=false'
        r = requests.get(u, headers={'accept': 'application/json', 'accept-version': '3.0', 'dnt': '1'})
        parsed_trips = [parse_trip(t) for t in r.json()['trips']]
    except:
        for t in parsed_trips:
            t['label'] = '(Error) ' + t['label']
        traceback.print_exc()
    parsed_trips = [t for t in parsed_trips if t['num_legs'] == 1]
    return parsed_trips

class LirrFetcher(hass.Hass):
    def initialize(self):
        # Run every 5 minutes starting now
        self.run_every(self.update_lirr_data, "now", 300)
        self.ordinals = ['First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth']

    def update_lirr_data(self, kwargs):
        def publish_entities(entity_prefix, trips):
            for i, trip in enumerate(trips):
                ent = self.get_entity(f'sensor.{entity_prefix}_{i}')
                state = trip['time']
                if trip['shuttle']:
                    state += ' (shuttle)'
                ent.set_state(state = trip['time'], attributes = {
                    'track': trip['track'],
                    'stop_status': trip['status'].replace('_', ' ').capitalize(),
                    'duration': f"{trip['duration']} min",
                    'friendly_name': trip['label'],#f'{self.ordinals[i]} Train ({trip["label"]})',
                    'icon': 'mdi:train',
                })
        def merge_routes(x, y):
            routes = x + y
            routes.sort(key=lambda x: x['numeric_time'])
            # drop the departed trips except the most recent
            num_departed = len([t for t in routes if t['status'] == 'DEPARTED'])
            for i in range(num_departed-1):
                routes.pop(0)
            while routes[0]['numeric_time'] + 60*int(self.args.get('max_lookback_mins', 45)) < time.time():
                routes.pop(0)
            return routes[:6]
        to_penn = fetch_data(from_sta='PWS', to_sta='NYK', label='Penn Station')
        to_gc = fetch_data(from_sta='PWS', to_sta='_GC', label='Grand Central')
        publish_entities('lirr_penn', merge_routes(to_penn, to_gc))
        from_penn = fetch_data(from_sta='NYK', to_sta='PWS', label='Penn Station')
        from_gc = fetch_data(from_sta='_GC', to_sta='PWS', label='Grand Central')
        publish_entities('lirr_pw', merge_routes(from_penn, from_gc))
