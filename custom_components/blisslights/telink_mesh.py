"""Async Telink BLE mesh protocol client.

The pairing handshake and packet encrypt/decrypt logic is ported from
google/python-dimond (Apache License 2.0), which itself contains code
derived from python-tikteck, Copyright 2016 Matthew Garrett
<mjg59@srcf.ucam.org>. Ported from bluepy (sync) to bleak (async), and
given a persistent-connection-with-idle-timeout lifecycle modeled on
Bluetooth-Devices/led-ble, the library backing Home Assistant's core
led_ble integration.

https://github.com/google/python-dimond
https://github.com/Bluetooth-Devices/led-ble
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Coroutine
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS,
)
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)
from Crypto.Cipher import AES

from .const import (
    BRIGHTNESS_LEVELS,
    CHAR_COMMAND,
    CHAR_NOTIFY,
    CHAR_PAIR,
    CMD_CONTROL,
    CMD_OPCODE,
    CMD_POWER,
)

_LOGGER = logging.getLogger(__name__)

DISCONNECT_DELAY = 120
BLEAK_BACKOFF_TIME = 0.25
DEFAULT_ATTEMPTS = 3
PAIR_RESPONSE_WAIT = 0.3

# On power-on, the projector resumes its own default multi-color effect
# before accepting new commands; a color/brightness command sent immediately
# after power-on can be overwritten by that resume. Confirmed live: without
# this delay, turning on with e.g. a solid green ends up showing red+green+
# blue instead.
POWER_ON_SETTLE_DELAY = 1.0


def _validate_channel_value(value: int) -> None:
    if not 0 <= value <= 255:
        raise ValueError(f"Value {value} is outside the valid range of 0-255")


class PairingFailedError(Exception):
    """Raised when the mesh pairing handshake does not complete."""


def _aes_ecb_reversed(key: bytes, data: bytes) -> bytes:
    """Telink's byte-reversed AES-ECB primitive.

    Telink mesh reverses both the key and the plaintext/ciphertext byte
    order around a standard AES-ECB encrypt. This exact construction (not a
    plain AES-ECB call) is required for the device to accept the pairing
    handshake and packets.
    """
    cipher = AES.new(bytes(reversed(key)), AES.MODE_ECB)
    return bytes(reversed(cipher.encrypt(bytes(reversed(data)))))


def _derive_mesh_key(mesh_name: str, mesh_password: str) -> bytes:
    """XOR the (zero-padded to 16 bytes) mesh name and password."""
    name = mesh_name.encode("ascii").ljust(16, b"\x00")[:16]
    password = mesh_password.encode("ascii").ljust(16, b"\x00")[:16]
    return bytes(a ^ b for a, b in zip(name, password))


def generate_session_key(
    mesh_key: bytes, nonce_local: bytes, nonce_remote: bytes
) -> bytes:
    """Derive the per-connection session key from the pairing nonces."""
    data = nonce_local[:8] + nonce_remote[:8]
    return _aes_ecb_reversed(mesh_key, data)


def build_pairing_challenge(mesh_key: bytes, nonce_local: bytes) -> bytes:
    """Encrypt the mesh key using our padded random nonce as the AES key.

    This mirrors python-dimond's `key_encrypt`, which (unlike `generate_sk`)
    uses the nonce as the AES key and the mesh key as the plaintext -- the
    two functions use opposite operand roles in the original.
    """
    nonce_padded = nonce_local.ljust(16, b"\x00")[:16]
    return _aes_ecb_reversed(nonce_padded, mesh_key)


def encrypt_packet(
    session_key: bytes, mac_reversed: bytes, packet: bytearray
) -> bytearray:
    """Encrypt a 20-byte command packet in place, per the Telink mesh format.

    mac_reversed is the 6-byte device MAC address with byte order reversed
    (i.e. least-significant octet first).
    """
    auth_nonce = bytes(
        [
            mac_reversed[0],
            mac_reversed[1],
            mac_reversed[2],
            mac_reversed[3],
            0x01,
            packet[0],
            packet[1],
            packet[2],
            15,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ]
    )
    authenticator = bytearray(_aes_ecb_reversed(session_key, auth_nonce))
    for i in range(15):
        authenticator[i] ^= packet[i + 5]

    mac_tag = _aes_ecb_reversed(session_key, bytes(authenticator))
    packet[3] = mac_tag[0]
    packet[4] = mac_tag[1]

    iv = bytes(
        [
            0,
            mac_reversed[0],
            mac_reversed[1],
            mac_reversed[2],
            mac_reversed[3],
            0x01,
            packet[0],
            packet[1],
            packet[2],
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ]
    )
    pad = _aes_ecb_reversed(session_key, iv)
    for i in range(15):
        packet[i + 5] ^= pad[i]

    return packet


def decrypt_notify_packet(
    session_key: bytes, mac_reversed: bytes, raw: bytes
) -> bytes | None:
    """Decrypt a device-originated notify packet, per the official Telink SDK.

    Ported from com.telink.crypto.AES.decrypt(key, iv, packet) and
    LightController.onNotify() in the decompiled BlissLights app. This is
    NOT symmetric with encrypt_packet: notify packets use a different byte
    layout (MAC at bytes[5:7], encrypted payload at bytes[7:20], vs.
    outgoing packets' MAC at [3:5] / payload at [5:20]), and the IV borrows
    the packet's own first 5 bytes instead of our local sequence counter
    (getSecIVS: mac_reversed[0:3] + raw[0:5]), since the device has no
    knowledge of our app-side sequence number.

    Returns the 13-byte decrypted payload, or None if the MAC doesn't
    verify (wrong session key, or not actually a notify-shaped packet).
    """
    if len(raw) < 20:
        return None
    iv8 = mac_reversed[0:3] + raw[0:5]
    packet = bytearray(raw)

    pad_block = bytearray(16)
    pad_block[1:9] = iv8
    pad = _aes_ecb_reversed(session_key, bytes(pad_block))
    for i in range(13):
        packet[7 + i] ^= pad[i]

    mac_block = bytearray(16)
    mac_block[0:8] = iv8
    mac_block[8] = 13
    mac = bytearray(_aes_ecb_reversed(session_key, bytes(mac_block)))
    for i in range(13):
        mac[i] ^= packet[7 + i]
        if i == 12:
            mac = bytearray(_aes_ecb_reversed(session_key, bytes(mac)))

    if packet[5] != mac[0] or packet[6] != mac[1]:
        return None
    return bytes(packet[7:20])


def mac_reversed_from_address(address: str) -> bytes:
    """Convert an "AA:BB:CC:DD:EE:FF" address into Telink's reversed byte order."""
    raw = bytes.fromhex(address.replace(":", "").replace("-", ""))
    return bytes(reversed(raw))


class TelinkMeshClient:
    """Maintains a persistent, authenticated connection to one mesh device.

    Pairing derives a fresh session key on every new connection, so a
    reconnect (idle timeout or error) transparently re-pairs before the next
    command is sent.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        mesh_name: str,
        mesh_password: str,
        vendor_id: int,
    ) -> None:
        self._ble_device = ble_device
        self._mesh_key = _derive_mesh_key(mesh_name, mesh_password)
        self._vendor_id = vendor_id
        self._mac_reversed = mac_reversed_from_address(ble_device.address)

        self._client: BleakClientWithServiceCache | None = None
        self._session_key: bytes | None = None
        self._packet_count = random.randrange(0xFFFF)

        # CMD_CONTROL sets color, laser, motor, brightness, and breathe mode
        # all in a single packet, so we track the last-set value of each and
        # resend the full state whenever one of them changes.
        self._red = 255
        self._green = 255
        self._blue = 255
        # The device's master 1-3 brightness dial is a separate, coarser
        # control from the (confirmed live) continuous per-channel R/G/B
        # dimming -- fixed at max here since per-channel values already give
        # real continuous control; see BRIGHTNESS_LEVELS in const.py.
        self._brightness_level = BRIGHTNESS_LEVELS[-1]
        self._laser_brightness = 255
        self._motor = True
        self._breathe = False
        self._powered_on = False

        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._expected_disconnect = False
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self.loop = asyncio.get_running_loop()

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update the BLEDevice, e.g. after HA re-discovers the same address."""
        self._ble_device = ble_device

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def name(self) -> str:
        return self._ble_device.name or self._ble_device.address

    # -- public commands ---------------------------------------------------

    async def async_turn_on(self) -> None:
        await self._send_command(CMD_OPCODE, bytes([CMD_POWER, 0x01, 0x01]))
        self._powered_on = True

    async def async_turn_off(self) -> None:
        await self._send_command(CMD_OPCODE, bytes([CMD_POWER, 0x00, 0x01]))
        self._powered_on = False

    @property
    def powered_on(self) -> bool:
        return self._powered_on

    async def async_set_red(self, value: int) -> None:
        _validate_channel_value(value)
        self._red = value
        await self._send_control_command()

    async def async_set_green(self, value: int) -> None:
        _validate_channel_value(value)
        self._green = value
        await self._send_control_command()

    async def async_set_blue(self, value: int) -> None:
        _validate_channel_value(value)
        self._blue = value
        await self._send_control_command()

    async def async_set_rgb(self, rgb: tuple[int, int, int]) -> None:
        """Set all three channels in one command. Mainly for ble_probe.py."""
        red, green, blue = rgb
        for value in (red, green, blue):
            _validate_channel_value(value)
        self._red, self._green, self._blue = red, green, blue
        await self._send_control_command()

    async def async_set_laser_brightness(self, brightness: int) -> None:
        """Set the laser's brightness (0 = off, 255 = full), a real PWM dimmer.

        Confirmed live: unlike the master RGB brightness dial, this is a
        continuous dial, not a 3-level enum -- e.g. 32/64/200 all produced
        visibly different intensities. Very low values (1-3) don't produce
        visible light at all, matching a normal dimmer floor.
        """
        _validate_channel_value(brightness)
        self._laser_brightness = brightness
        await self._send_control_command()

    async def async_set_motor(self, enabled: bool) -> None:
        self._motor = enabled
        await self._send_control_command()

    @property
    def red(self) -> int:
        return self._red

    @property
    def green(self) -> int:
        return self._green

    @property
    def blue(self) -> int:
        return self._blue

    @property
    def laser_brightness(self) -> int:
        return self._laser_brightness

    @property
    def laser_enabled(self) -> bool:
        return self._laser_brightness > 0

    @property
    def motor_enabled(self) -> bool:
        return self._motor

    async def _ensure_powered_on(self) -> None:
        """Turn the unit on first if needed, e.g. from a channel/laser/motor
        entity being toggled while the device is off -- mirrors what the
        official app does before its own color/brightness commands.
        """
        if not self._powered_on:
            await self.async_turn_on()
            await asyncio.sleep(POWER_ON_SETTLE_DELAY)

    async def _send_control_command(self) -> None:
        await self._ensure_powered_on()
        # Confirmed against the decompiled app: motor uses 0x00/0xFF for
        # off/on (not 0/1 -- that encoding is only used by breathe below).
        # R/G/B and laser are each independent continuous 0-255 dimmers,
        # confirmed live -- this isn't a single blended color.
        await self._send_command(
            CMD_OPCODE,
            bytes(
                [
                    CMD_CONTROL,
                    self._red,
                    self._green,
                    self._blue,
                    self._laser_brightness,
                    0xFF if self._motor else 0x00,
                    self._brightness_level,
                    int(self._breathe),
                ]
            ),
        )

    async def async_connect(self) -> None:
        """Connect and pair without sending a command. Mainly useful for debugging."""
        await self._ensure_connected()

    async def async_send_raw(self, command: int, data: bytes, target: int = 0) -> None:
        """Send an arbitrary opcode/payload. For protocol experimentation only."""
        await self._send_command(command, data, target)

    async def async_start_notify(self, callback: Any) -> None:
        """Subscribe to the notify characteristic. For protocol experimentation only.

        callback is invoked as callback(characteristic, data: bytearray) per
        bleak's start_notify convention. Raw and undecoded -- there's no
        confirmed notification format for this device yet.
        """
        await self._ensure_connected()
        assert self._client is not None  # noqa: S101
        await self._client.start_notify(CHAR_NOTIFY, callback)

    async def async_stop_notify(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.stop_notify(CHAR_NOTIFY)

    def decrypt_notification(self, raw: bytes) -> bytes | None:
        """Decrypt a raw notify payload, or None if its MAC doesn't verify."""
        assert self._session_key is not None  # noqa: S101
        return decrypt_notify_packet(self._session_key, self._mac_reversed, raw)

    async def stop(self) -> None:
        """Disconnect and cancel any pending idle-disconnect timer."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        await self._execute_disconnect()

    # -- connection lifecycle ------------------------------------------------

    async def _ensure_connected(self) -> None:
        if self._client and self._client.is_connected:
            self._reset_disconnect_timer()
            return
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                self._reset_disconnect_timer()
                return
            self._client = await self._connect_and_pair()
            self._reset_disconnect_timer()

    async def _connect_and_pair(self) -> BleakClientWithServiceCache:
        """Connect and run the pairing handshake, retrying on rejection.

        The device intermittently rejects the pairing handshake on an
        otherwise-healthy connection with correct credentials (observed
        live, not just on credential mismatch); a same-connection retry of
        just the handshake doesn't help, but reconnecting from scratch
        usually does. This is unrelated to the device's physical pairing
        button, which only gates accepting a *new* mesh key.
        """
        last_error: PairingFailedError | None = None
        for attempt in range(DEFAULT_ATTEMPTS):
            _LOGGER.debug("%s: Connecting (attempt %d)", self.name, attempt + 1)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
            )
            _LOGGER.debug("%s: Connected, pairing", self.name)
            try:
                self._session_key = await self._pair(client)
            except PairingFailedError as ex:
                last_error = ex
                await client.disconnect()
                if attempt < DEFAULT_ATTEMPTS - 1:
                    await asyncio.sleep(BLEAK_BACKOFF_TIME)
                continue
            except Exception:
                await client.disconnect()
                raise
            else:
                return client
        assert last_error is not None  # noqa: S101
        raise last_error

    async def _pair(self, client: BleakClientWithServiceCache) -> bytes:
        """Run the mesh challenge/response handshake and derive a session key."""
        nonce_local = bytes(random.getrandbits(8) for _ in range(8))
        challenge = build_pairing_challenge(self._mesh_key, nonce_local)
        packet = bytes([0x0C]) + nonce_local + challenge[:8]
        await client.write_gatt_char(CHAR_PAIR, packet, response=True)
        await asyncio.sleep(PAIR_RESPONSE_WAIT)
        response = await client.read_gatt_char(CHAR_PAIR)
        if len(response) < 9:
            raise PairingFailedError(
                f"Pairing response too short: {response.hex()}"
            )
        nonce_remote = bytes(response[1:9])
        return generate_session_key(self._mesh_key, nonce_local, nonce_remote)

    def _reset_disconnect_timer(self) -> None:
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
        self._expected_disconnect = False
        self._disconnect_timer = self.loop.call_later(
            DISCONNECT_DELAY, self._disconnect
        )

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        if self._expected_disconnect:
            _LOGGER.debug("%s: Disconnected", self.name)
            return
        _LOGGER.warning("%s: Device unexpectedly disconnected", self.name)

    def _disconnect(self) -> None:
        self._disconnect_timer = None
        self._create_background_task(self._execute_disconnect())

    def _create_background_task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _execute_disconnect(self) -> None:
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            self._session_key = None
            if client and client.is_connected:
                await client.disconnect()

    # -- command sending -----------------------------------------------------

    def _build_packet(self, command: int, data: bytes, target: int = 0) -> bytearray:
        packet = bytearray(20)
        packet[0] = self._packet_count & 0xFF
        packet[1] = (self._packet_count >> 8) & 0xFF
        packet[5] = target & 0xFF
        packet[6] = (target >> 8) & 0xFF
        packet[7] = command
        packet[8] = self._vendor_id & 0xFF
        packet[9] = (self._vendor_id >> 8) & 0xFF
        packet[10 : 10 + len(data)] = data
        self._packet_count += 1
        if self._packet_count > 0xFFFF:
            self._packet_count = 1
        return packet

    async def _send_command(self, command: int, data: bytes, target: int = 0) -> None:
        await self._ensure_connected()
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, waiting", self.name
            )
        async with self._operation_lock:
            await self._send_command_locked(command, data, target)

    @retry_bluetooth_connection_error(DEFAULT_ATTEMPTS)
    async def _send_command_locked(
        self, command: int, data: bytes, target: int
    ) -> None:
        try:
            await self._execute_command_locked(command, data, target)
        except BleakDBusError as ex:
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: Backing off, disconnecting due to error: %s", self.name, ex
            )
            await self._execute_disconnect()
            raise
        except BleakError as ex:
            _LOGGER.debug(
                "%s: Disconnecting due to error: %s", self.name, ex
            )
            await self._execute_disconnect()
            raise
        except BLEAK_EXCEPTIONS:
            _LOGGER.debug("%s: communication failed", self.name, exc_info=True)
            raise
        except BleakNotFoundError:
            _LOGGER.error(
                "%s: device not found or no longer in range", self.name
            )
            raise

    async def _execute_command_locked(
        self, command: int, data: bytes, target: int
    ) -> None:
        assert self._client is not None  # noqa: S101
        # Re-pair transparently if a reconnect happened without going through
        # _ensure_connected (shouldn't normally occur, but keeps this method
        # self-contained and safe to retry).
        if self._session_key is None:
            self._session_key = await self._pair(self._client)
        packet = self._build_packet(command, data, target)
        encrypted = encrypt_packet(self._session_key, self._mac_reversed, packet)
        await self._client.write_gatt_char(
            CHAR_COMMAND, bytes(encrypted), response=False
        )
