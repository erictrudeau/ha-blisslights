"""BlissLights light platform: independent Red/Green/Blue channels and laser.

The projector's color LEDs don't blend -- setting e.g. (128, 0, 255) lights
red and blue elements individually rather than mixing to purple, confirmed
live for both the color channels and the laser (each is its own continuous
0-255 PWM dimmer). So each channel is its own light entity rather than one
composite RGB color picker.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
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
    """Set up the BlissLights lights."""
    client = entry.runtime_data.client
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        [
            BlissLightsRedLight(client, address, entry.title),
            BlissLightsGreenLight(client, address, entry.title),
            BlissLightsBlueLight(client, address, entry.title),
            BlissLightsLaserLight(client, address, entry.title),
        ]
    )


class _BlissLightsDimmableLight(LightEntity):
    """Shared base for the projector's independently dimmable channels.

    State is optimistic: this protocol has no confirmed status-readback
    opcode, so displayed state reflects the last command sent, not a live
    read from the projector.
    """

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_icon = "mdi:cloud-outline"
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(
        self,
        client: TelinkMeshClient,
        address: str,
        name: str,
        unique_suffix: str,
        initial_brightness: int,
    ) -> None:
        self._client = client
        self._attr_unique_id = f"{address}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            name=name,
            manufacturer="BlissLights",
            model="Sky Lite 2.0",
            connections={(dr.CONNECTION_BLUETOOTH, address)},
        )
        self._attr_is_on = initial_brightness > 0
        self._attr_brightness = initial_brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 255)
        await self._async_set_channel_brightness(brightness)
        self._attr_is_on = True
        self._attr_brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_channel_brightness(0)
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_set_channel_brightness(self, value: int) -> None:
        raise NotImplementedError


class BlissLightsRedLight(_BlissLightsDimmableLight):
    """The projector's red channel."""

    _attr_translation_key = "red"

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        super().__init__(client, address, name, "red", client.red)

    async def _async_set_channel_brightness(self, value: int) -> None:
        await self._client.async_set_red(value)


class BlissLightsGreenLight(_BlissLightsDimmableLight):
    """The projector's green channel."""

    _attr_translation_key = "green"

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        super().__init__(client, address, name, "green", client.green)

    async def _async_set_channel_brightness(self, value: int) -> None:
        await self._client.async_set_green(value)


class BlissLightsBlueLight(_BlissLightsDimmableLight):
    """The projector's blue channel."""

    _attr_translation_key = "blue"

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        super().__init__(client, address, name, "blue", client.blue)

    async def _async_set_channel_brightness(self, value: int) -> None:
        await self._client.async_set_blue(value)


class BlissLightsLaserLight(_BlissLightsDimmableLight):
    """The projector's blue laser, a dimmable light (not just on/off)."""

    _attr_icon = "mdi:creation"
    _attr_translation_key = "laser"

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        super().__init__(client, address, name, "laser", client.laser_brightness)

    async def _async_set_channel_brightness(self, value: int) -> None:
        await self._client.async_set_laser_brightness(value)
