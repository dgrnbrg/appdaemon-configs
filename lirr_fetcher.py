import hassapi as hass
import adbase as ad
import datetime
import time
import requests


def fetch_data(from_sta, to_sta):
    def parse_trip(trip):
        duration_min = (trip['trip_end'] - trip['trip_start']) // 60
        legs = trip['legs']
        train = legs[0]['train']
        details = train['details']
        stop = details['stops'][0]
        sched_time = datetime.datetime.fromtimestamp(stop['sched_time'])
        sched_time_fmt = sched_time.strftime('%-I:%M %p')
        status = stop['stop_status']
        track = stop['t2s_track']
        return {'time': sched_time_fmt, 'status': status, 'track': track, 'num_legs': len(legs), 'shuttle': legs[0]['is_shuttle'], 'duration': duration_min}
    u = f'https://backend-unified.mylirr.org/plan?from={from_sta}&to={to_sta}&fares=ALL&time={round(time.time() * 1000)}&arrive_by=false'
    r = requests.get(u, headers={'accept': 'application/json', 'accept-version': '3.0', 'dnt': '1'})
    parsed_trips = [parse_trip(t) for t in r.json()['trips']]
    parsed_trips = [t for t in parsed_trips if t['num_legs'] == 1]
    # drop the departed trips except the most recent
    num_departed = len([t for t in parsed_trips if t['status'] == 'DEPARTED'])
    for i in range(num_departed-1):
        parsed_trips.pop(0)
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
                    'friendly_name': f'{self.ordinals[i]} Train',
                    'icon': 'mdi:train',
                })
        to_penn = fetch_data(from_sta='PWS', to_sta='NYK')[:6]
        publish_entities('lirr_penn', to_penn)
        to_port = fetch_data(to_sta='PWS', from_sta='NYK')[:6]
        publish_entities('lirr_pw', to_port)
