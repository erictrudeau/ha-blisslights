"""BlissLights light platform: power, RGB color, and brightness."""

from __future__ import annotations

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlissLightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlissLights light."""
    client = entry.runtime_data.client
    async_add_entities([BlissLightsEntity(client, entry.data[CONF_ADDRESS], entry.title)])


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
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        self._client = client
        self._attr_unique_id = address
        self._attr_device_info = DeviceInfo(
            name=name,
            manufacturer="BlissLights",
            model="2.0 Bluetooth Star Projector",
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
