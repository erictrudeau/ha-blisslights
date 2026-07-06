"""BlissLights light platform: power, RGB color, and brightness."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BlissLightsConfigEntry
from .telink_mesh import TelinkMeshClient

# On power-on, the projector resumes its own default multi-color effect
# before accepting new commands; a color/brightness command sent immediately
# after power-on can be overwritten by that resume. Confirmed live: without
# this delay, turning on with e.g. a solid green ends up showing red+green+
# blue instead.
POWER_ON_SETTLE_DELAY = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlissLightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlissLights lights."""
    client = entry.runtime_data.client
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        [
            BlissLightsEntity(client, address, entry.title),
            BlissLightsLaserLight(client, address, entry.title),
        ]
    )


class BlissLightsEntity(LightEntity):
    """Representation of a BlissLights star projector's RGB light.

    State is optimistic: this protocol has no confirmed status-readback
    opcode for this device, so displayed state reflects the last command
    sent, not a live read from the projector.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_icon = "mdi:weather-night"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        self._client = client
        self._attr_unique_id = address
        self._attr_device_info = DeviceInfo(
            name=name,
            manufacturer="BlissLights",
            model="Sky Lite 2.0",
            connections={(dr.CONNECTION_BLUETOOTH, address)},
        )
        self._attr_is_on = False
        self._attr_rgb_color = (255, 255, 255)
        self._attr_brightness = 255

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting color and/or brightness in the same call."""
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]

        if not self._attr_is_on:
            await self._client.async_turn_on()
            if ATTR_RGB_COLOR in kwargs or ATTR_BRIGHTNESS in kwargs:
                await asyncio.sleep(POWER_ON_SETTLE_DELAY)
        if ATTR_RGB_COLOR in kwargs:
            await self._client.async_set_rgb(self._attr_rgb_color)
        if ATTR_BRIGHTNESS in kwargs:
            await self._client.async_set_brightness(
                round(self._attr_brightness / 255 * 100)
            )

        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._client.async_turn_off()
        self._attr_is_on = False
        self.async_write_ha_state()


class BlissLightsLaserLight(LightEntity):
    """The projector's blue laser, a dimmable light (not just on/off).

    Confirmed live: the laser has its own continuous 0-255 PWM brightness,
    independent of the RGB brightness dial -- e.g. 32/64/200 produced
    visibly different intensities. State is optimistic, like the main light.
    """

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_icon = "mdi:creation"
    _attr_translation_key = "laser"
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        self._client = client
        self._attr_unique_id = f"{address}_laser"
        self._attr_device_info = DeviceInfo(
            name=name,
            manufacturer="BlissLights",
            model="Sky Lite 2.0",
            connections={(dr.CONNECTION_BLUETOOTH, address)},
        )
        self._attr_is_on = client.laser_enabled
        self._attr_brightness = client.laser_brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 255)
        await self._client.async_set_laser_brightness(brightness)
        self._attr_is_on = True
        self._attr_brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.async_set_laser_brightness(0)
        self._attr_is_on = False
        self.async_write_ha_state()
