This repository contains systems for a few nice things:

- BLE-based room-level presence with ESPHome for iOS devices
- Presence based light controller, with many useful affordances (guest mode, reautomation, manual control, adaptive lighting, and more)
- Presence and event based thermostat controller, with many useful affordances (heat pump awareness, drafty-home awareness, sleep mode, and more)
- ESPHome binary sensor for occupancy--allows you to combine multiple binary sensors for presence (it's on when any are on, off when all are off)
- ESPHome LD2410B/C bluetooth integration, so that you can directly connect & automate up to 3 LD2410B/C to an ESPHome device
- ESPHome LD2410B/C bluetooth provisioning helper, so that you can quickly get the MAC address of each of your LD2410 sensors
- ESPHome IRK provisioning helper, so that you can pair your phone to an ESPHome device and extract the IRK for passive BLE tracking

# Bluetooth room-level tracking for iPhones, Apple watches, and other "untrackable" devices using ESPHome

This code implements the cryptographic identification algorithm, plus some nifty signal processing, to get a pretty reliable indicator of which room a device is in.
Then, it links multiple devices & a GPS-based device tracker together, to determine which room a person is in, or whether they've left the building.
It combines multiple devices by assuming that the most recently moved device is being carried, while stationary devices may have been left behind.
It also fuses data from GPS tracking, BLE passive scans, and door/garage sensors to create better signals for arrival & leaving, even when the GPS tracker breaks.

**Note** You'll need to use AppDaemon from the `dev` branch, because AppDaemon/appdaemon#1626 hasn't been release to the home assistant addon yet.

## Ideas for Use cases

You can use the person tracker entities with cards like State Switch and Tab Redirect so that dashboards show content relevant to the room you're in.

If you have multi-zone HVAC, you can automatically adjust rooms to match the temperature preferences of their occupants.

You can glance to see which room you left your phone/watch in when you took it off & forgot.

I wouldn't use this for presence detection, because it updates on the scale of dozens of seconds (due to the signal cleaning).

Here's an example of a tab redirect configuration that I use (I repeat this for each other user, using their device tracker):

```yaml
type: custom:tab-redirect-card
redirect:
  - user: David
    entity_id: device_tracker.david_fused
    entity_state: away
    redirect_to_tab_index: 4
  - user: David
    entity_id: device_tracker.david_fused
    entity_state: just_left
    redirect_to_tab_index: 4
  - user: David
    entity_id: device_tracker.david_irk
    entity_state: main-floor
    redirect_to_tab_index: 1
  - user: David
    entity_id: device_tracker.david_irk
    entity_state: bedroom
    redirect_to_tab_index: 2
  - user: David
    entity_id: device_tracker.david_irk
    entity_state: downstairs
    redirect_to_tab_index: 3
  - user: Person2
    entity_id: device_tracker.person2_irk
    entity_state: main-floor
    redirect_to_tab_index: 1
# ...etc...
```

### Status viewing

I use `multiple-entity-row` to show the current tracker status.
This type of configuration allows you to see the states of all the underlying & sensor-fused trackers.
I also configure a tap action for the devices so that I can override the automatic device detection, since sometimes an RF glitch can make the system think that you're with the wrong device.

```
type: vertical-stack
cards:
  - type: entities
    title: People
    entities:
      - entity: device_tracker.david_irk
        type: custom:multiple-entity-row
        name: David IRK
        state_header: Person
        secondary_info:
          attribute: last-updated
          name: false
        entities:
          - entity: device_tracker.david_phone_irk
            name: Phone
            tap_action:
              action: call-service
              service: button.press
              service_data:
                entity_id: button.irk_tracker_make_primary_david_david_phone
          - entity: device_tracker.david_watch_irk
            name: Watch
            tap_action:
              action: call-service
              service: button.press
              service_data:
                entity_id: button.irk_tracker_make_primary_david_david_watch
      - entity: device_tracker.david_iphone
        name: David Tracker
        icon: mdi:human
        type: custom:multiple-entity-row
        state_header: iCloud3
        secondary_info:
          attribute: last-updated
          name: false
        entities:
          - entity: device_tracker.david_fused
            name: Fused
  - type: horizontal-stack
    cards:
      - type: custom:mushroom-entity-card
        entity: button.irk_tracker_make_primary_david_david_phone
        secondary_info: none
        name: David phone primary
        tap_action:
          action: toggle
      - type: custom:mushroom-entity-card
        entity: button.irk_tracker_make_primary_david_david_watch
        secondary_info: none
        name: David watch primary
        tap_action:
          action: toggle
```

## Appdaemon Configuration

You can probably use the tracker configuration in apps.yaml without many changes, however here are some things to consider:

- `away_tracker_arrival_delay_secs` should last for the duration from when you come into GPS zone range, and when you want to be considered "home" (i.e. I like the garage door card to come up when I'm right in front of my house).
- `away_trackers` could be from a phone app, or better yet, iCloud3.
- `rssi_adjustments` are constant offsets so that if you have different generations of ESP32 devices, or some cases affect the signal more than others, you can shift them to be comparable.
- `pullout_sensors` are for your front door, garage, etc, so that you can tell when you've just left. You should configure which ESP devices are nearest to the exit point & sensor, so that only the household members who are actually heading out are identified as leaving. `within_top` allows for the sensor to be within that many of the closest sensor, depending on how quickly you'll pass by sensors on your way out of that exit.

You'll need to set up your room aliases, which map ESP32 devices to the room they belong to.
If you notice that a device is read equally from multiple rooms, use the `secondary_clarifiers` to list the rooms that ESP32 could be associated with, and then the next-most strong signal will disambiguate.
This may require you to add more ESP32s to rooms for clarifying purposes.
You can still include a `default` with the `secondary_clarifiers`, so that localization defaults to that room rather than `unknown`.
Clarifiers can also map from a specific ESP32 device to a different room, so that you can mark hallways or others "spaces between" two ESP32 devices.

Other settings aren't likely to need to change.

## Building your own Automations

Once you've configured the system, you'll want to create automations based on the `device_tracker.${person}_irk` and `home_focused_tracker` that you configure.

The `_irk` tracker gives room-level positioning data, or `away` when that person's not at home.
This can be used for automating preferences and views based on what room you're in.

The fused tracker gives 4 states: `home`, `away`, `just_arrived`, and `just_left`.
This can be used for automating views & events when coming and going from the home.

## Advanced Appdaemon irk tracker settings

- `tracking_window_minutes` is the duration that we consider observations of BLE beacons "active"
- `tracking_min_superplurality` is the amount of signal strength by which a new "strongest room" must exceed the previous room to consider the change to occur (this provides hysteresis)
- `ping_halflife_seconds` is the halflife by which we downweight old observations within the tracking window.

## ESPHome config

Any ESP32 with bluetooth will work for this.
You should copy `irk_resolver.h` and `irk_locator.yaml` into your homeassistant's `config/esphome` folder.
Then, you'll just need to add the following to your config:


```yaml
packages:
  irk_locator: !include {file: irk_locator.yaml, irk_source: $device_name}
```

**Note**: You must specify `irk_source` to be the source that will be used in the appdaemon config.

### Protect your HomeAssistant database

You will really want to add this to your `configuration.yaml`, so that you don't overload your database saving these events.
They will be excessively numerous.

```yaml
recorder:
  exclude:
    event_types:
      - esphome.ble_tracking_beacon
```

## Getting your device keys for identification

See https://espresense.com/beacons/apple for how to get the IRKs for apple watches.

I pair my iPhone with Windows, and then use https://www.reddit.com/r/hackintosh/comments/mtvj5m/howto_keep_bluetooth_devices_paired_across_macos/ (or a similar approach with pstools + regedit) to get the Identity Resolving Key for each phone.

# Presence & Task based lighting automation

`lights.py` is a system for controlling lights, where we define a priority sequence of triggers (such as presence detection, activities like watching tv, etc), and the light is automatically controlled by these.
The light can also be linked to "Adaptive Lighting" for automatic circadian control.
If a light is ever controlled by an external switch or app, it switches to manual mode, which can be canceled by toggling it off & on, or by pressing a button entity for reautomation that's created by the light (I make these buttons appear on my dashboard only when the light is in manual mode).
Lights also generate a "Guest Mode" switch (for obvious reasons).

## Priority triggers
The key idea with this system is that presence based lighting should be driven by "triggers"--things that we are doing or places we are.
Some examples of tasks are:
- Being in a room
- Watching the TV
- Playing a video game
- Cooking dinner
- Sleeping

You can refer to `apps.yaml` for inspiration with configurations I use at my house.

For example, the lights in the living room should be on when we're at home (there's no presence detection in this room), but they should be dimmed while we're watching TV.
Another example is that the bedroom lights should be on when we're in the room, but they should be very dimmed once one partner gets in bed.
As a last example, a room may turn on to a lower brightness when presence is detected in an adjacent room, so that you always walk in to a dimly lit room (rather than a dark room).

These triggers are listed in their precedence order, and triggers can specify additional modifiers.
For example, lights can have their brightness capped (e.g. for mood lighting while watching TV, irrespective of adaptive lighting's state), and triggers can include transition times and delay durations (so that lights can remain on at a lower setting for several minutes after a room has been vacated, so you don't walk back into a dark room).

## Lighting details

You can specify a different instance of adaptive lighting for each light, so that you can compensate for brightness & color temperature differences between different brands of bulbs.

Each light will have an `input_boolean.guest_mode_${light}` automatically created, which disables the automation entirely.

When you adjust a light (either at a switch or via HomeAssistant), it will flip to "manual mode".
If you toggle the light off & on again, it will return to automatic mode.
I have found that it can be unclear whether a light is manually controlled or not.
I use mushroom cards, and this is the conditional chip I add to each room's card for each light in the room:

```yaml
type: conditional
conditions:
  - entity: sensor.light_state_living_room
    state: manual
chip:
  type: template
  tap_action:
    action: call-service
    service: button.press
    data: {}
    target:
      entity_id: button.reautomate_living_room
  icon: mdi:lightbulb-auto
  icon_color: yellow
  content: Re-automate Living Room
```

This way, you can just tap the chip to re-automate the light, and it is hidden while the light is automatic.

# Thermostat Control

`temperature.py` hass a (bunch of) systems for controlling thermostats, but I only use one now.

The Basic Thermostat takes control of a climate entity and automates normal thermostat interactions.
It makes the assumption that you'll manually set your thermostat to heat or cool mode depending on the season, and it automates the temperature adjustments.

## Away mode

The thermostat uses presence trackers to determine if you're home or away.
When you're away, it stores the current setpoint for when you return, and turns the thermostat up or down as needed.
So, if you change the thermostat because things feel out of the ordinary today, that will persist for the rest of the day.

## Draft compensation

It chooses the day's target temperature based on the outdoor conditions.
You specify the splitpoint and the warm & cool day settings for your thermostat's modes.

This way, you can have your AC cooler on very hot days (since a lukewarm day might not need to be cooled as much), or you may want your heat higher on cold days to compensate for the draft.

## Heat Pump

If your heat pump can't keep up, you want your backup heat to kick in.
But, if you let your house cool when you're not at home, and then you return, your thermostat might immediately fire up the backup heat.
If this isn't what you want, the controller will "walk" the temperature up to the target when you arrive home so that you don't trigger the backup heat unnecessarily.

## Setting up sleep mode detection with iOS (if you use the daily alarm & wind down features)
It listens to events generated by your phone (like your alarm going off or evening wind-down mode) to move into & out of sleep mode.
You can change the names of these events in the configuration if you like.

First, go to Companion App settings in the iOS app, and add an action `morning_alarm` and an action `wind_down` (feel free to fill in the rest, but it doesn't affect the functionality).

Next, go to the Shortcuts app (built in to iOS). Go to the Automation tab, and create a new automation: When my wake-up alarm goes off, "Perform Action" `morning_alarm`. Uncheck the "Ask Before Running" so that it happens every time.

Again, we'll make one more Automation in Shortcuts; this time, When Wind Down starts, "Perform Action" `wind_down`. Once again, you'll want to uncheck "Ask Before Running".

# Occupancy Combining Sensor

This is something you'll want to use to combine multiple presence detectors on-device, such as LD2410b, other mmWave sensors, and PIR sensors.

```yaml
external_components:
  - source: github://dgrnbrg/appdaemon-configs

binary_sensor:
  - platform: presence_combo
    name: Basement Occupancy
    device_class: occupancy
    filters:
      - delayed_off: ${occupancy_delay_off}
    ids:
      - computer_area_id_occupancy_detected
      - entrance_id_occupancy_detected
      - workout_id_occupancy_detected
```

# IRK Provisioning Helper

This creates a text sensor that will show the IRK of the most recently paired device. Just find the ESPHome device by its name in your phone's bluetooth.

```yaml
external_components:
  - source: github://dgrnbrg/appdaemon-configs

irk_enrollment:
```

# LD2410B/C Provisioning Helper

Just copy `ld2410ble_mac_discovery.yaml` into your esphome folder. Then, this will print each LD2410 to the ESPHome log (as they are detected), and it will show the last 3 detected sensors in a text sensor.

I would recommend powering up each LD2410B and waiting to see it pop up, then copy & pasting its MAC address before moving on.

```yaml
packages:
  ld2410_provisioning: !include ld2410ble_mac_discovery.yaml
```

# LD2410B/C ESPHome driver

This will allow you to directly pair LD2410B/C with your ESPHome device, so that you can run local automations with them.
Once you copied the yaml file into your esphome folder, see below for an example configuration.
You may need to wait up to a minute for the connection to be established--this driver automatically attempts to recover lost connections.

Use `HLKRadarTool` on your phone's app store to change settings on the sensors, such as their password or detection timeouts.
If you need to connect with `HLKRadarTool` and you don't see your sensor, you may need to turn off the enable switch that was added to ESPHome--the sensor can only pair with one other device at a time.

If you specify a name, make sure to include a trailing space (` `) so that the name is formatted correctly. At a minimum, provide the `ld2410_id` and `mac_address` for your sensor.
You can also specify `sensor_throttle` and `binary_sensor_debounce` to reduce the update rate (these devices update hundredes of times per second).

```yaml
packages:
  base: !include device-base.yaml
  ld2410: !include {file: ld2410ble.yaml, vars: { mac_address: 'XX:XX:XX:XX:XX:XX', ld2410_password: "newpw1", ld2410_id: "computer_area_id" } }
  ld2410_2: !include {file: ld2410ble.yaml, vars: { mac_address: 'XX:XX:XX:XX:XX:XX',  ld2410_id: "entrance_id", ld2410_name: "Garage Entrance " } }
  ld2410_3: !include {file: ld2410ble.yaml, vars: { mac_address: 'XX:XX:XX:XX:XX:XX',  ld2410_id: "workout_id", ld2410_name: "Workout Area " } }
```

# Deployment (reminder for myself)

```
scp irk_tracker.py homeassistant:/config/appdaemon/apps/
scp temperature.py homeassistant:/config/appdaemon/apps/
scp lights.py homeassistant:/config/appdaemon/apps/
scp apps.yaml homeassistant:/config/appdaemon/apps/
```

