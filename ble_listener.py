import asyncio
import aiohttp
from bleak import BleakScanner
import sys
import argparse

parser = argparse.ArgumentParser(description='stream ble advertisements to homeassistant')
parser.add_argument('--access-token', help='Long lived home assistant access token', required=True)
parser.add_argument('--homeassistant', default='homeassistant.local', help='address of home assistant')
parser.add_argument('--event', default='esphome.ble_tracking_beacon', help="Event to publish (you probably don't want to change this")
parser.add_argument('--source', help='source to label the advertisements with (probably the room or device name)', required=True)
parser.add_argument('-v', '--verbose', help='Output debugging info', action='store_true')
args=parser.parse_args()

if args.verbose:
    print(f'Launching with args {args}')

steps = []
for i in range(20):
    step = ['['] + [' '] * 20 + [']']
    step[i + 1] = 'x'
    steps.append(''.join(step))
steps_rev = []
for s in steps[1:-1]:
    steps_rev.append(s)
steps_rev.reverse()
steps.extend(steps_rev)

async def main():
    stop_event = asyncio.Event()

    # TODO: add something that calls stop_event.set()

    url = f"http://{args.homeassistant}:8123/api/events/{args.event}"
    counter = 0
    async with aiohttp.ClientSession() as session:
        async def callback(device, advertising_data):
            data = {
                  "addr": device.address,
                  "source": args.source,
                  "rssi": device.rssi,
            }
            nonlocal counter
            counter += 1
            if args.verbose:
                print(f"recieving {steps[counter % len(steps)]}\r", end='')
            async with session.post(url, json = data, headers={'Authorization': f'Bearer {args.access_token}'}) as response:
                  data = await response.text()

        async with BleakScanner(callback) as scanner:
            # Important! Wait for an event to trigger stop, otherwise scanner
            # will stop immediately.
            await stop_event.wait()

    # scanner stops when block exits
    pass

asyncio.run(main())

