This repository contains systems for a few nice things:

- BLE-based room-level presence with ESPHome for iOS devices
- Presence based light controller, with many useful affordances (guest mode, reautomation, manual control, adaptive lighting, and more)
- Presence and event based thermostat controller, with many useful affordances (heat pump awareness, drafty-home awareness, sleep mode, and more)

# Bluetooth room-level tracking for iPhones, Apple watches, and other "untrackable" devices using ESPHome

This code implements the cryptographic identification algorithm, plus some nifty signal processing, to get a pretty reliable indicator of which room a device is in.
Then, it links multiple devices & a GPS-based device tracker together, to determine which room a person is in, or whether they've left the building.
It combines multiple devices by assuming that the most recently moved device is being carried, while stationary devices may have been left behind.

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
  - user: David
    entity_id: device_tracker.david_irk
    entity_state: away
    redirect_to_tab_index: 4
  - user: Person2
    entity_id: device_tracker.person2_irk
    entity_state: main-floor
    redirect_to_tab_index: 1
# ...etc...
```

### Status viewing

I use `multiple-entity-row` to show the current tracker status.
I also configure a tap action for the devices so that I can override the automatic device detection, since sometimes an RF glitch can make the system think that you're with the wrong device.

```
type: entities
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
    name: David iCloud3
    icon: mdi:human
```

## Appdaemon Configuration

You can probably use the tracker configuration in apps.yaml without many changes, however here are some things to consider:

- `away_tracker_arrival_delay_secs` should last for the duration from when you come into GPS zone range, and when you want to be considered "home" (i.e. I like the garage door card to come up when I'm right in front of my house).
- `away_trackers` could be from a phone app, or better yet, iCloud3.
- `rssi_adjustments` are constant offsets so that if you have different generations of ESP32 devices, or some cases affect the signal more than others, you can shift them to be comparable.

You'll need to set up your room aliases, which map ESP32 devices to the room they belong to.
If you notice that a device is read equally from multiple rooms, use the `secondary_clarifiers`, and then the next-most strong signal will disambiguate.
This may require you to add more ESP32s to rooms for clarifying purposes.
You can still include a `default` with the `secondary_clarifiers`, so that localization defaults to that room rather than `unknown`.

Other settings aren't likely to need to change.

## Advanced Appdaemon irk tracker settings

- `tracking_window_minutes` is the duration that we consider observations of BLE beacons "active"
- `tracking_min_superplurality` is the amount of signal strength by which a new "strongest room" must exceed the previous room to consider the change to occur (this provides hysteresis)
- `ping_halflife_seconds` is the halflife by which we downweight old observations within the tracking window.

## ESPHome config

Any ESP32 with bluetooth will work for this. You'll just need to add the following to your config:

```yaml
bluetooth_proxy:

esp32_ble_tracker:
  on_ble_advertise:
  - then:
    - homeassistant.event:
        event: esphome.ble_tracking_beacon
        data:
          source: ${name}
          rssi: !lambda |-
            return x.get_rssi();
          addr: !lambda |-
            return x.address_str();
```

**Note**: You should change `${name}` to whatever substitution you use for a device name, or just hardcode it there.

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

# Deployment (reminder for myself)

```
scp irk_tracker.py homeassistant:/config/appdaemon/apps/
scp temperature.py homeassistant:/config/appdaemon/apps/
scp lights.py homeassistant:/config/appdaemon/apps/
scp apps.yaml homeassistant:/config/appdaemon/apps/
```
