"""BlissLights switch platform: rotation-motor control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    """Set up the BlissLights motor switch."""
    client = entry.runtime_data.client
    address = entry.data[CONF_ADDRESS]
    async_add_entities([BlissLightsMotorSwitch(client, address, entry.title)])


class BlissLightsMotorSwitch(SwitchEntity):
    """Controls the projector's rotation motor.

    State is optimistic, like the light entity: this protocol has no
    confirmed status-readback opcode, so displayed state reflects the last
    command sent, not a live read from the projector.
    """

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "motor"
    _attr_icon = "mdi:sync"

    def __init__(self, client: TelinkMeshClient, address: str, name: str) -> None:
        self._client = client
        self._attr_unique_id = f"{address}_motor"
        self._attr_device_info = DeviceInfo(
            name=name,
            manufacturer="BlissLights",
            model="Sky Lite 2.0",
            connections={(dr.CONNECTION_BLUETOOTH, address)},
        )
        self._attr_is_on = client.motor_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._client.async_set_motor(True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.async_set_motor(False)
        self._attr_is_on = False
        self.async_write_ha_state()
