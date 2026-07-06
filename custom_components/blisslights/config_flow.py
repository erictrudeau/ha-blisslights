"""Config flow for the BlissLights integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import (
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_VENDOR_ID,
    DEFAULT_MESH_NAME,
    DEFAULT_MESH_PASSWORD,
    DEFAULT_VENDOR_ID,
    DOMAIN,
)
from .telink_mesh import PairingFailedError, TelinkMeshClient

_LOGGER = logging.getLogger(__name__)


def _parse_vendor_id(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as ex:
        raise vol.Invalid("invalid_vendor_id") from ex


CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MESH_NAME, default=DEFAULT_MESH_NAME): str,
        vol.Required(CONF_MESH_PASSWORD, default=DEFAULT_MESH_PASSWORD): str,
        vol.Required(
            CONF_VENDOR_ID, default=hex(DEFAULT_VENDOR_ID)
        ): _parse_vendor_id,
    }
)


class BlissLightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BlissLights."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered device."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_credentials()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup: pick a discovered device or type an address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            return await self.async_step_credentials()

        current_addresses = self._async_current_ids(include_ignore=False)
        discovered = {
            info.address: f"{info.name} ({info.address})"
            for info in async_discovered_service_info(self.hass)
            if info.address not in current_addresses
        }

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): (
                    vol.In(discovered) if discovered else str
                )
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect mesh credentials and verify pairing against the real device."""
        errors: dict[str, str] = {}
        assert self._address is not None

        if user_input is not None:
            ble_device = (
                self._discovery_info.device
                if self._discovery_info
                else bluetooth.async_ble_device_from_address(
                    self.hass, self._address, True
                )
            )
            if ble_device is None:
                errors["base"] = "cannot_connect"
            else:
                client = TelinkMeshClient(
                    ble_device,
                    user_input[CONF_MESH_NAME],
                    user_input[CONF_MESH_PASSWORD],
                    user_input[CONF_VENDOR_ID],
                )
                try:
                    await client.async_connect()
                except PairingFailedError:
                    errors["base"] = "pairing_failed"
                except BLEAK_EXCEPTIONS:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during BlissLights pairing")
                    errors["base"] = "unknown"
                else:
                    await client.stop()
                    return self.async_create_entry(
                        title=(
                            self._discovery_info.name
                            if self._discovery_info
                            else self._address
                        ),
                        data={
                            CONF_ADDRESS: self._address,
                            CONF_MESH_NAME: user_input[CONF_MESH_NAME],
                            CONF_MESH_PASSWORD: user_input[CONF_MESH_PASSWORD],
                            CONF_VENDOR_ID: user_input[CONF_VENDOR_ID],
                        },
                    )

        return self.async_show_form(
            step_id="credentials",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
            description_placeholders={"address": self._address},
        )
