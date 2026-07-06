"""Constants for the BlissLights integration."""

from typing import Final

DOMAIN: Final = "blisslights"

# Standard Telink mesh SDK GATT service/characteristics. BlissLights 2.0 uses
# the stock Telink demo UUIDs (confirmed via UART/APK teardown, see
# https://community.home-assistant.io/t/reverse-engineering-blisslights-2-0-bluetooth-star-projector/387349),
# not a vendor-customized service.
MESH_SERVICE_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d1910"
CHAR_NOTIFY: Final = "00010203-0405-0607-0809-0a0b0c0d1911"
CHAR_COMMAND: Final = "00010203-0405-0607-0809-0a0b0c0d1912"
CHAR_OTA: Final = "00010203-0405-0607-0809-0a0b0c0d1913"
CHAR_PAIR: Final = "00010203-0405-0607-0809-0a0b0c0d1914"

CONF_MESH_NAME: Final = "mesh_name"
CONF_MESH_PASSWORD: Final = "mesh_password"
CONF_VENDOR_ID: Final = "vendor_id"

# Reported factory default for this device family; user's app may have
# repaired with a custom mesh name/password via the QR-share feature, in
# which case these must be overridden in the config flow.
DEFAULT_MESH_NAME: Final = "telink_mesh0"
DEFAULT_MESH_PASSWORD: Final = "123"

# Confirmed live: matches the ManufacturerData key broadcast by the device
# (0x0211) and Manufacture.getDefault().getVendorId() in the official
# BlissLights app's decompiled Telink SDK wrapper (com.quhwa.mesh).
DEFAULT_VENDOR_ID: Final = 0x0211

# Wire-format opcode (packet byte 7) for every app->device command on this
# product line. Reverse-engineered from the official BlissLights Android app
# (com.quhwa.mesh / com.quhwa.blisslights, Telink SDK wrapper): unlike the
# generic stock Telink opcodes (0xD0=on/off, 0xE2=RGB, etc., which this
# device's firmware does NOT respond to), this app wraps every command in a
# single vendor "user command" opcode and multiplexes the actual command via
# the first data byte (see CMD_POWER / CMD_CONTROL below). Confirmed live
# against a real device: powers on/off and sets color/brightness correctly.
CMD_OPCODE: Final = 0xF0

# Sub-command (data[0]) to turn the whole unit on/off.
# Payload: [CMD_POWER, on_off (0|1), 0x01]
CMD_POWER: Final = 0x41

# Sub-command (data[0]) that atomically sets color, laser, motor, brightness,
# and breathe (fade) mode in one packet -- there is no way to set just one of
# these fields independently.
# Payload: [CMD_CONTROL, R, G, B, laser (0|1), motor (0|1), brightness (1-3), breathe (0|1)]
CMD_CONTROL: Final = 0x47

# Brightness is a discrete 3-level dial on this device (low/medium/high), not
# a continuous 0-100 or 0-255 range -- confirmed via the app's brightness
# radio buttons, which map directly to these values.
BRIGHTNESS_LEVELS: Final = (1, 2, 3)

DEVICE_TIMEOUT: Final = 30
