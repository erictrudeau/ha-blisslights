# BlissLights (Home Assistant / HACS)

A Home Assistant custom integration for the **BlissLights Sky Lite 2.0** star projector, controlling power, its independent Red/Green/Blue/laser channels, and rotation motor over BLE.

There's no official Home Assistant integration for this device, and BlissLights' own companion apps have disappeared from the Play Store. This integration talks to the projector directly using the stock **Telink BLE mesh** protocol its firmware is built on.

## Status: v0.1, protocol confirmed live

Pairing, power on/off, color channels, laser, and rotation motor have all been verified against a real BlissLights Sky Lite 2.0 projector. The command format was reverse-engineered from the official BlissLights Android app's decompiled Telink SDK wrapper (`com.quhwa.mesh`), not just generic Telink/AwoX conventions — this device's firmware ignores the stock Telink opcodes (`0xD0`/`0xE2`/etc.) that many other white-label BLE bulbs respond to. Instead every command uses a single vendor opcode (`0xF0`) with the actual sub-command multiplexed into the first data byte.

The projector's Red, Green, Blue, and laser are each their own dimmable light entity (not a single color-picker light), plus a Power switch for the whole unit and a Rotation switch for the motor — all on the same device. Turning on any of the color/laser lights or the motor switch will power the unit on automatically if it was off; the Power switch is the only way to turn the whole thing off.

Notes on the confirmed protocol:

- The projector's color LEDs don't blend into one color — setting what would be `(128, 0, 255)` on a normal RGB light lights the red and blue elements individually rather than mixing to purple. This is a hardware characteristic, not a bug, which is why Red/Green/Blue are separate light entities here instead of one color picker.
- Red, Green, Blue, and the laser are each an independent, continuous 0-255 PWM dimmer — confirmed live that 32/64/200 all produce visibly distinct intensities on each, not just a few discrete steps. Very low values (1-3) don't produce visible light at all, which is a normal dimmer floor rather than a bug.
- Separately, the device also has a coarser master 3-level brightness dial (low/medium/high, confirmed via the app's brightness radio buttons) alongside the continuous per-channel values above. Its interaction with the per-channel values hasn't been tested beyond "high" live, so the integration always sends the max level and doesn't expose it as a separate control.
- Color, laser, motor, and the master brightness dial are all set by a single atomic command — there's no way to change just one channel without also resending the others' last-known values, which the integration handles internally.
- Motor uses `0x00`/`0xFF` for off/on in this command (not `0x00`/`0x01` — that encoding is only used by the separate breathe/fade field). Easy to get backwards; confirmed by testing both directions live.
- On power-on, the projector briefly resumes its own default multi-color effect before accepting new commands. A color command sent immediately after power-on can get overwritten by that resume; the integration waits ~1s after auto-powering on before sending any channel/laser/motor command to avoid this race.
- The device's BLE advertisement doesn't include the mesh service UUID (that's only visible via GATT service discovery after connecting), so Bluetooth discovery and the manual-add device picker both match on `manufacturer_id` (0x0211/529, confirmed present in every advertisement) instead. A `service_uuid` matcher looks reasonable but silently never fires.
- The device does send BLE notifications, and `telink_mesh.py`/`ble_probe.py --listen` can genuinely decrypt them (ported from the official app's `com.telink.crypto.AES.decrypt`/`getSecIVS`, confirmed live: the vendor ID and mesh address decode correctly). But investigated live and ruled out as a state source: the payload never varies beyond one counter byte, regardless of what's actually pressed on the device (15+ physical button presses produced only 7 notifications, all with identical content otherwise) -- it's a generic low-level mesh heartbeat, not a power/color/laser/motor status push. The light/switch entities' `assumed_state = True` is correct as-is; there's no cheap way to read real state back from this device.

If pairing fails with a device that previously worked (`Pairing response too short: 0e`), the device's pairing window has likely closed — hold the projector's power button until its light blinks 6 times, then retry.

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
