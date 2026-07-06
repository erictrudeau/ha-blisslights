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
    DEFAULT_DEVICE_NAME,
    DEFAULT_MESH_NAME,
    DEFAULT_MESH_PASSWORD,
    DEFAULT_VENDOR_ID,
    DOMAIN,
)
from .telink_mesh import PairingFailedError, TelinkMeshClient

_LOGGER = logging.getLogger(__name__)


CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MESH_NAME, default=DEFAULT_MESH_NAME): str,
        vol.Required(CONF_MESH_PASSWORD, default=DEFAULT_MESH_PASSWORD): str,
        vol.Required(CONF_VENDOR_ID, default=hex(DEFAULT_VENDOR_ID)): str,
    }
)


class BlissLightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BlissLights."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None
        self._tried_defaults = False
        self._mesh_name: str | None = None
        self._mesh_password: str | None = None
        self._vendor_id: int | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered device."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self.context["title_placeholders"] = {"name": DEFAULT_DEVICE_NAME}
        return await self.async_step_credentials()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup: pick a discovered device or type an address."""
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
            and DEFAULT_VENDOR_ID in info.manufacturer_data
        }

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): (
                    vol.In(discovered) if discovered else str
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect mesh credentials, trying the confirmed factory defaults first."""
        assert self._address is not None
        errors: dict[str, str] = {}

        if user_input is None and not self._tried_defaults:
            self._tried_defaults = True
            error = await self._async_try_pair(
                DEFAULT_MESH_NAME, DEFAULT_MESH_PASSWORD, DEFAULT_VENDOR_ID
            )
            if error is None:
                return self._async_create_entry()
            # Factory defaults didn't work -- most likely the device was
            # re-paired via the official app's QR-share feature with custom
            # credentials. Fall through to ask instead of failing outright.
        elif user_input is not None:
            try:
                vendor_id = int(user_input[CONF_VENDOR_ID], 0)
            except ValueError:
                errors["base"] = "invalid_vendor_id"
            else:
                error = await self._async_try_pair(
                    user_input[CONF_MESH_NAME], user_input[CONF_MESH_PASSWORD], vendor_id
                )
                if error is None:
                    return self._async_create_entry()
                errors["base"] = error

        return self.async_show_form(
            step_id="credentials",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
            description_placeholders={"address": self._address},
        )

    async def _async_try_pair(
        self, mesh_name: str, mesh_password: str, vendor_id: int
    ) -> str | None:
        """Attempt pairing with the given credentials.

        Returns None on success (and stashes the credentials for
        _async_create_entry), or an error code string on failure.
        """
        ble_device = (
            self._discovery_info.device
            if self._discovery_info
            else bluetooth.async_ble_device_from_address(
                self.hass, self._address, True
            )
        )
        if ble_device is None:
            return "cannot_connect"

        client = TelinkMeshClient(ble_device, mesh_name, mesh_password, vendor_id)
        try:
            await client.async_connect()
        except PairingFailedError:
            return "pairing_failed"
        except BLEAK_EXCEPTIONS:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during BlissLights pairing")
            return "unknown"

        await client.stop()
        self._mesh_name = mesh_name
        self._mesh_password = mesh_password
        self._vendor_id = vendor_id
        return None

    def _async_create_entry(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=DEFAULT_DEVICE_NAME,
            data={
                CONF_ADDRESS: self._address,
                CONF_MESH_NAME: self._mesh_name,
                CONF_MESH_PASSWORD: self._mesh_password,
                CONF_VENDOR_ID: self._vendor_id,
            },
        )
