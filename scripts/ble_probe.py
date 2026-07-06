#!/usr/bin/env python3
"""Standalone CLI to scan, pair, and send commands to a BlissLights
projector directly over BLE, without a running Home Assistant instance.

This is the primary tool for validating (and if needed, correcting) the
Telink mesh protocol constants in custom_components/blisslights/const.py
-- DEFAULT_VENDOR_ID, CMD_OPCODE, CMD_POWER, CMD_CONTROL -- against the
real device before trusting them in the Home Assistant integration.

Setup:
    pip install bleak pycryptodome bleak-retry-connector

Usage:
    python scripts/ble_probe.py --scan
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --pair
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --power on
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --power off
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --color 255 0 0
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --brightness 50
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --laser 255
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --motor off
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF --command 0xf0 --data 65,1,1

Flags combine into a single connection, e.g. turn on, set color, and
disable the laser all at once:
    python scripts/ble_probe.py --address AA:BB:CC:DD:EE:FF \\
        --power on --color 255 0 0 --brightness 100 --laser 0

If a command has no visible effect, the opcode/payload/vendor_id are the
first things to try changing -- pass --vendor-id/--mesh-name/--mesh-password
to override the guessed defaults, and use --command/--data to experiment
with opcodes that aren't wired up as named flags yet.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

from bleak import BleakScanner


def _load_protocol_modules():
    """Load const.py and telink_mesh.py directly, bypassing blisslights/__init__.py.

    The package __init__.py (and config_flow.py, light.py) import from
    `homeassistant`, which isn't installed outside a HA environment. const.py
    and telink_mesh.py have no such dependency, so we load just those two as
    a minimal fake "blisslights" package to satisfy telink_mesh's relative
    `from .const import ...`.
    """
    pkg_dir = Path(__file__).resolve().parent.parent / "custom_components" / "blisslights"
    pkg = types.ModuleType("blisslights")
    pkg.__path__ = [str(pkg_dir)]
    sys.modules["blisslights"] = pkg

    def _load(name: str):
        spec = importlib.util.spec_from_file_location(
            f"blisslights.{name}", pkg_dir / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"blisslights.{name}"] = module
        spec.loader.exec_module(module)
        return module

    return _load("const"), _load("telink_mesh")


_const, _telink_mesh = _load_protocol_modules()
DEFAULT_MESH_NAME = _const.DEFAULT_MESH_NAME
DEFAULT_MESH_PASSWORD = _const.DEFAULT_MESH_PASSWORD
DEFAULT_VENDOR_ID = _const.DEFAULT_VENDOR_ID
MESH_SERVICE_UUID = _const.MESH_SERVICE_UUID
TelinkMeshClient = _telink_mesh.TelinkMeshClient


async def _scan() -> None:
    print("Scanning for 10s... (make sure the projector is powered and in range)")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    if not devices:
        print("No BLE devices found.")
        return
    for address, (device, adv) in devices.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        marker = (
            "  <-- advertises the Telink mesh service, likely the projector"
            if MESH_SERVICE_UUID in uuids
            else ""
        )
        print(f"{address}  name={device.name!r}  rssi={adv.rssi}  uuids={uuids}{marker}")


async def _connect(args: argparse.Namespace) -> TelinkMeshClient:
    device = await BleakScanner.find_device_by_address(args.address, timeout=10.0)
    if device is None:
        raise SystemExit(
            f"Could not find a BLE device at {args.address}. "
            "Run --scan first and confirm it's in range."
        )
    client = TelinkMeshClient(device, args.mesh_name, args.mesh_password, args.vendor_id)
    await client.async_connect()
    print(
        f"Paired with mesh_name={args.mesh_name!r} mesh_password={args.mesh_password!r} "
        f"vendor_id=0x{args.vendor_id:04x}"
    )
    return client


async def _run(args: argparse.Namespace) -> None:
    if args.scan:
        await _scan()
        return

    if not args.address:
        raise SystemExit("--address is required (or use --scan to find it)")

    client = await _connect(args)
    try:
        if args.power is not None:
            if args.power == "on":
                await client.async_turn_on()
            else:
                await client.async_turn_off()
            print(f"Sent power-{args.power}. Did the projector respond?")

        if args.color is not None:
            red, green, blue = args.color
            await client.async_set_rgb((red, green, blue))
            print(f"Sent RGB({red}, {green}, {blue}). Did the color change correctly?")

        if args.brightness is not None:
            await client.async_set_brightness(args.brightness)
            print(f"Sent brightness {args.brightness}%. Did it dim correctly?")

        if args.laser is not None:
            await client.async_set_laser_brightness(args.laser)
            print(f"Sent laser brightness {args.laser}. Did the laser respond correctly?")

        if args.motor is not None:
            await client.async_set_motor(args.motor == "on")
            print(f"Sent motor {args.motor}. Did the rotation respond correctly?")

        if args.command is not None:
            data = bytes(int(b, 0) for b in args.data.split(",")) if args.data else b""
            await client.async_send_raw(args.command, data)
            print(
                f"Sent raw command 0x{args.command:02x} data={data.hex()}. "
                "Observe the projector."
            )

        if args.listen is not None:
            def _on_notify(_characteristic: Any, data: bytearray) -> None:
                raw = bytes(data)
                line = f"  notify: raw={raw.hex()}"
                decrypted = client.decrypt_notification(raw)
                if decrypted is not None:
                    line += f"  decrypted={decrypted.hex()}"
                else:
                    line += "  (MAC did not verify)"
                print(line)

            print(
                f"Subscribed to the notify characteristic, listening for "
                f"{args.listen}s -- press buttons / change settings on the "
                "device or its app now."
            )
            await client.async_start_notify(_on_notify)
            await asyncio.sleep(args.listen)
            await client.async_stop_notify()
            print("Done listening.")
    finally:
        await client.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan for nearby BLE devices, flagging any advertising the mesh service",
    )
    parser.add_argument("--address", help="BLE MAC address, e.g. AA:BB:CC:DD:EE:FF")
    parser.add_argument("--mesh-name", default=DEFAULT_MESH_NAME)
    parser.add_argument("--mesh-password", default=DEFAULT_MESH_PASSWORD)
    parser.add_argument(
        "--vendor-id", type=lambda s: int(s, 0), default=DEFAULT_VENDOR_ID
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="Just run the pairing handshake (implied by any other flag below)",
    )
    parser.add_argument("--power", choices=("on", "off"))
    parser.add_argument(
        "--color", type=int, nargs=3, metavar=("RED", "GREEN", "BLUE")
    )
    parser.add_argument("--brightness", type=int, metavar="PERCENT")
    parser.add_argument(
        "--laser", type=int, metavar="0-255", help="Laser brightness, 0=off, 255=full"
    )
    parser.add_argument("--motor", choices=("on", "off"))
    parser.add_argument(
        "--command", type=lambda s: int(s, 0), metavar="OPCODE", help="e.g. 0xf0"
    )
    parser.add_argument(
        "--data", default="", metavar="BYTES", help="Comma-separated bytes, e.g. 65,1,1"
    )
    parser.add_argument(
        "--listen",
        type=float,
        metavar="SECONDS",
        help="Subscribe to the notify characteristic and print raw notifications",
    )

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
