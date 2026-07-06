# BlissLights (Home Assistant / HACS)

A Home Assistant custom integration for the **BlissLights 2.0 Bluetooth Star Projector**, controlling power, RGB color, brightness, laser, and rotation motor over BLE.

There's no official Home Assistant integration for this device, and BlissLights' own companion apps have disappeared from the Play Store. This integration talks to the projector directly using the stock **Telink BLE mesh** protocol its firmware is built on.

## Status: v0.1, protocol confirmed live

Pairing, power on/off, RGB color, brightness, laser, and rotation motor have all been verified against a real BlissLights 2.0 projector. The command format was reverse-engineered from the official BlissLights Android app's decompiled Telink SDK wrapper (`com.quhwa.mesh`), not just generic Telink/AwoX conventions — this device's firmware ignores the stock Telink opcodes (`0xD0`/`0xE2`/etc.) that many other white-label BLE bulbs respond to. Instead every command uses a single vendor opcode (`0xF0`) with the actual sub-command multiplexed into the first data byte.

The light entity exposes power/color/brightness; laser and rotation motor are separate switch entities on the same device.

Notes on the confirmed protocol:

- Brightness is a discrete 3-level dial (low/medium/high) on this hardware, not a smooth 0-255 range. HA's 0-255 brightness slider is quantized down to one of 3 levels.
- Color, laser, motor, and brightness are all set by a single atomic command — there's no way to change just the color without also specifying laser/motor state. Changing color/brightness from the light entity re-sends whatever laser/motor state the switches were last set to.
- Laser and motor use `0x00`/`0xFF` for off/on in this command (not `0x00`/`0x01` — that encoding is only used by the separate breathe/fade field). Easy to get backwards; confirmed by testing both directions live.
- The projector's RGB "color" isn't a single blended LED — setting e.g. `(128, 0, 255)` lights red and blue elements individually rather than mixing to purple. This is a hardware characteristic, not a bug in the integration.
- On power-on, the projector briefly resumes its own default multi-color effect before accepting new commands. A color/brightness command sent immediately after power-on can get overwritten by that resume; the light entity waits ~1s after powering on before sending color/brightness to avoid this race.

If pairing fails with a device that previously worked (`Pairing response too short: 0e`), the device's pairing window has likely closed — hold the physical pairing button on the projector and retry.

## Installation (HACS custom repository)

1. In HACS, go to **Integrations → ⋮ → Custom repositories**.
2. Add this repository's URL, category **Integration**.
3. Install "BlissLights", then restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration**, search "BlissLights".
5. Pick your device (if Bluetooth discovery found it) or enter its MAC address manually, then confirm the mesh credentials.

## Manual installation

Copy `custom_components/blisslights` into your Home Assistant config's `custom_components/` directory and restart.

## Credits

- Protocol groundwork from the Home Assistant Community thread [Reverse Engineering Blisslights 2.0 Bluetooth Star Projector?](https://community.home-assistant.io/t/reverse-engineering-blisslights-2-0-bluetooth-star-projector/387349)
- Pairing/encryption logic ported from [google/python-dimond](https://github.com/google/python-dimond) (Apache-2.0), itself derived from Matthew Garrett's python-tikteck.
- The generic Telink opcode enum was cross-checked against [fsaris/EspHome-AwoX-BLE-mesh-hub](https://github.com/fsaris/EspHome-AwoX-BLE-mesh-hub), an independent Telink-mesh implementation for a different white-label brand -- but this device's actual command set (`CMD_OPCODE`/`CMD_POWER`/`CMD_CONTROL`) came from decompiling the official BlissLights Android app (`com.quhwa.mesh`, `com.quhwa.blisslights`), which the generic opcodes did not match.
- Integration scaffolding follows Home Assistant core's [`led_ble`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/led_ble) integration and the [`led-ble`](https://github.com/Bluetooth-Devices/led-ble) library's connection-lifecycle pattern.
