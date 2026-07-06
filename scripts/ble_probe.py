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
    python scripts/ble_probe.py scan
    python scripts/ble_probe.py pair AA:BB:CC:DD:EE:FF
    python scripts/ble_probe.py on AA:BB:CC:DD:EE:FF
    python scripts/ble_probe.py off AA:BB:CC:DD:EE:FF
    python scripts/ble_probe.py color AA:BB:CC:DD:EE:FF 255 0 0
    python scripts/ble_probe.py brightness AA:BB:CC:DD:EE:FF 50
    python scripts/ble_probe.py laser AA:BB:CC:DD:EE:FF on
    python scripts/ble_probe.py motor AA:BB:CC:DD:EE:FF off
    python scripts/ble_probe.py raw AA:BB:CC:DD:EE:FF 0xf0 65,1,1

If a command has no visible effect, the opcode/payload/vendor_id are the
first things to try changing -- pass --vendor-id/--mesh-name/--mesh-password
to override the guessed defaults, and use 'raw' to experiment with opcodes
that aren't wired up as named commands yet (e.g. laser or motor control).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

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


async def cmd_scan(_args: argparse.Namespace) -> None:
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
            "Run 'scan' first and confirm it's in range."
        )
    client = TelinkMeshClient(device, args.mesh_name, args.mesh_password, args.vendor_id)
    await client.async_connect()
    print(
        f"Paired with mesh_name={args.mesh_name!r} mesh_password={args.mesh_password!r} "
        f"vendor_id=0x{args.vendor_id:04x}"
    )
    return client


async def cmd_pair(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.stop()


async def cmd_on(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.async_turn_on()
    print("Sent power-on. Did the projector turn on?")
    await client.stop()


async def cmd_off(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.async_turn_off()
    print("Sent power-off. Did the projector turn off?")
    await client.stop()


async def cmd_color(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.async_set_rgb((args.red, args.green, args.blue))
    print(f"Sent RGB({args.red}, {args.green}, {args.blue}). Did the color change correctly?")
    await client.stop()


async def cmd_brightness(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.async_set_brightness(args.percent)
    print(f"Sent brightness {args.percent}%. Did it dim correctly?")
    await client.stop()


async def cmd_laser(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.async_set_laser(args.state == "on")
    print(f"Sent laser {args.state}. Did the laser respond correctly?")
    await client.stop()


async def cmd_motor(args: argparse.Namespace) -> None:
    client = await _connect(args)
    await client.async_set_motor(args.state == "on")
    print(f"Sent motor {args.state}. Did the rotation respond correctly?")
    await client.stop()


async def cmd_raw(args: argparse.Namespace) -> None:
    client = await _connect(args)
    data = bytes(int(b, 0) for b in args.data.split(",")) if args.data else b""
    await client.async_send_raw(args.command, data)
    print(f"Sent raw command 0x{args.command:02x} data={data.hex()}. Observe the projector.")
    await client.stop()


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("address", help="BLE MAC address, e.g. AA:BB:CC:DD:EE:FF")
    parser.add_argument("--mesh-name", default=DEFAULT_MESH_NAME)
    parser.add_argument("--mesh-password", default=DEFAULT_MESH_PASSWORD)
    parser.add_argument(
        "--vendor-id", type=lambda s: int(s, 0), default=DEFAULT_VENDOR_ID
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "scan", help="Scan for nearby BLE devices, flagging any advertising the mesh service"
    ).set_defaults(func=cmd_scan)

    p = sub.add_parser("pair", help="Run the pairing handshake only")
    _add_common_args(p)
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("on", help="Send power-on")
    _add_common_args(p)
    p.set_defaults(func=cmd_on)

    p = sub.add_parser("off", help="Send power-off")
    _add_common_args(p)
    p.set_defaults(func=cmd_off)

    p = sub.add_parser("color", help="Send an RGB color")
    _add_common_args(p)
    p.add_argument("red", type=int)
    p.add_argument("green", type=int)
    p.add_argument("blue", type=int)
    p.set_defaults(func=cmd_color)

    p = sub.add_parser("brightness", help="Send a brightness percentage (0-100)")
    _add_common_args(p)
    p.add_argument("percent", type=int)
    p.set_defaults(func=cmd_brightness)

    p = sub.add_parser("laser", help="Turn the laser on or off")
    _add_common_args(p)
    p.add_argument("state", choices=("on", "off"))
    p.set_defaults(func=cmd_laser)

    p = sub.add_parser("motor", help="Turn the rotation motor on or off")
    _add_common_args(p)
    p.add_argument("state", choices=("on", "off"))
    p.set_defaults(func=cmd_motor)

    p = sub.add_parser(
        "raw", help="Send a raw opcode + comma-separated data bytes, for protocol experimentation"
    )
    _add_common_args(p)
    p.add_argument("command", type=lambda s: int(s, 0), help="Opcode, e.g. 0xd0")
    p.add_argument("data", nargs="?", default="", help="Comma-separated bytes, e.g. 01,00,00")
    p.set_defaults(func=cmd_raw)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
