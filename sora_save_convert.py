#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trails in the Sky 1st Chapter / 空の軌跡 the 1st -- save converter (Switch <-> Steam).

This is a standalone re-implementation of the file formats handled by
https://github.com/turtle-insect/TrailsintheSky1stChapter , plus the actual
conversion the GUI editor does not expose.

Formats
-------
Both platforms store the same game-state blob ("payload") whose first 12 bytes
are::

    0x00  uint32   unknown small field (preserved as-is)
    0x04  uint32   unknown small field (preserved as-is)
    0x08  uint32   CRC32 over payload[0x0C:] seeded with (payload_size - 0x0C)
    0x0C  ...      game data

In the two reference slot saves payload[0x00] is 3 (Switch) and 7 (Steam); it is
not covered by the checksum and the reference editor never touches it.  It may
be a save-format version rather than a platform tag, so it is copied through
unchanged.  If the game rejects a converted save, try forcing it with
``--set-u32 0x0=7`` (Steam) or ``--set-u32 0x0=3`` (Switch).

Two kinds of data use the exact same container:

* slot saves, dumped as ``saveNNN/data.dat`` (2048008-byte payload);
* system data (options, achievements), dumped as ``sdmemNNN/data.dat``
  (a few hundred bytes of payload).

The container and the icon convert cleanly in both cases.  Note, however, that
the *contents* of a system-data payload are platform specific (the Switch and
Steam samples even differ in length), so repacking system data across platforms
only carries the source platform's option blob; let the target platform
generate its own system data instead.

Only the container differs:

* Steam : one folder per slot containing
      - user.dat    = zstd(payload)
      - icon0.png   = 228x128 PNG screenshot
      - detail.json = slot metadata (Time/title/subtitle/detail/user_param)
* Switch: one self-contained file (data.dat)
      - 56-byte header, then the raw payload, then a "footer" that embeds
        the same PNG followed by the 28-byte date struct and the text fields
        (title / subtitle / detail, each NUL terminated).

The payload is copied verbatim (byte for byte); only the container and the
sidecar metadata are repacked, and the checksum is recomputed.

Dependency
----------
    pip install zstandard

Usage
-----
    python3 sora_save_convert.py info    save000_switch/data.dat
    python3 sora_save_convert.py convert save000_switch save000_steam_out
    python3 sora_save_convert.py convert save000_steam  save000_switch_out
    python3 sora_save_convert.py convert <src> <dst> --to switch
    python3 sora_save_convert.py selftest --switch save000_switch --steam save000_steam

License
-------
GPL-3.0.  The file-format knowledge comes from the GPL-3.0 licensed reference
editor (https://github.com/turtle-insect/TrailsintheSky1stChapter); this script
is an independent re-implementation and keeps the same license.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import struct
import sys
import zlib

try:
    import zstandard
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "error: the 'zstandard' module is required.\n"
        "       install it with:  pip install zstandard\n"
    )
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# Constants (all taken from the reference editor's source)
# --------------------------------------------------------------------------- #

SWITCH_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
STEAM_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

CHECKSUM_OFFSET = 0x08  # where the CRC32 lives inside the payload
CHECKSUM_START = 0x0C  # first byte covered by the CRC32

SWITCH_HEADER_SIZE = 0x38  # 14 * uint32
SWITCH_META_STRUCT_SIZE = 0x1C  # 7 * uint32 (date + user_param)
SWITCH_TRAILING_SIZE = 0x04  # trailing zero padding after the text block

DEFAULT_ZSTD_LEVEL = 3  # ZstdNet's default, i.e. what the editor writes

DETAIL_JSON_NAME = "detail.json"
ICON_NAME = "icon0.png"
STEAM_PAYLOAD_NAME = "user.dat"
SWITCH_PAYLOAD_NAME = "data.dat"


# --------------------------------------------------------------------------- #
# Checksum
# --------------------------------------------------------------------------- #


def calc_checksum(payload: bytes) -> int:
    """Reproduce the game's CRC32 routine (sora_1st.exe+3531D0).

    The game seeds the reflected CRC32 with ``len(payload) - 0x0C`` and runs it
    over everything after the 12-byte payload header.  For a 2048008-byte slot
    save that seed is 0x1F3FFC, the constant hard-coded in the reference editor;
    system data uses the same rule with its own (much smaller) size.
    """
    if len(payload) <= CHECKSUM_START:
        raise ValueError(f"payload too small to hold a checksum: {len(payload)} bytes")
    seed = len(payload) - CHECKSUM_START
    # The game's routine is a plain reflected CRC32 without the usual
    # pre/post inversion; zlib.crc32 inverts both, so undo that here.
    return zlib.crc32(payload[CHECKSUM_START:], seed ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


def read_checksum(payload: bytes) -> int:
    return struct.unpack_from("<I", payload, CHECKSUM_OFFSET)[0]


def store_checksum(payload: bytearray) -> int:
    value = calc_checksum(bytes(payload))
    struct.pack_into("<I", payload, CHECKSUM_OFFSET, value)
    return value


# --------------------------------------------------------------------------- #
# Switch container
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class SwitchSave:
    payload: bytes
    icon: bytes  # PNG embedded in the container
    date: tuple  # (year, month, day, hour, minute, second, user_param)
    title: bytes  # NUL terminated as stored
    subtitle: bytes  # NUL terminated as stored
    detail: bytes  # NUL terminated as stored
    trailing: bytes = b"\x00" * SWITCH_TRAILING_SIZE


@dataclasses.dataclass
class SteamSave:
    payload: bytes
    icon: bytes
    detail: dict


def _strip_nul(raw: bytes) -> bytes:
    return raw[:-1] if raw.endswith(b"\x00") else raw


def parse_switch(data: bytes) -> SwitchSave:
    if len(data) < SWITCH_HEADER_SIZE:
        raise ValueError("file is too small to be a Switch save container")

    fields = struct.unpack_from("<14I", data, 0)
    (header_size, footer_pos) = fields[0], fields[1]
    payload_len, icon_size = fields[7], fields[8]
    title_len, subtitle_len, detail_len = fields[10], fields[11], fields[12]

    if header_size != SWITCH_HEADER_SIZE:
        raise ValueError(
            f"unexpected header size {header_size} (expected {SWITCH_HEADER_SIZE})"
        )
    if not (header_size <= footer_pos <= len(data)):
        raise ValueError(f"footer position {footer_pos} outside the file")

    payload = data[header_size:footer_pos]
    if len(payload) != payload_len:
        raise ValueError(f"payload length mismatch: {len(payload)} != {payload_len}")

    icon = data[footer_pos : footer_pos + icon_size]
    if not icon.startswith(SWITCH_MAGIC_PNG):
        raise ValueError("embedded icon is not a PNG (is this really a Switch save?)")

    meta = data[footer_pos + icon_size :]
    if len(meta) < SWITCH_META_STRUCT_SIZE:
        raise ValueError("metadata block is truncated")

    date = struct.unpack_from("<7I", meta, 0)
    text = meta[SWITCH_META_STRUCT_SIZE:]
    total = title_len + subtitle_len + detail_len
    if total > len(text):
        raise ValueError("text fields extend past the end of the file")

    return SwitchSave(
        payload=payload,
        icon=icon,
        date=date,
        title=text[:title_len],
        subtitle=text[title_len : title_len + subtitle_len],
        detail=text[title_len + subtitle_len : total],
        trailing=text[total:],
    )


def build_switch(save: SwitchSave) -> bytes:
    payload, icon = save.payload, save.icon
    title, subtitle, detail = save.title, save.subtitle, save.detail
    trailing = (
        save.trailing if save.trailing is not None else b"\x00" * SWITCH_TRAILING_SIZE
    )

    payload_len, icon_size = len(payload), len(icon)
    footer_pos = SWITCH_HEADER_SIZE + payload_len
    meta_pos = footer_pos + icon_size
    text_pos = meta_pos + SWITCH_META_STRUCT_SIZE
    title_len, subtitle_len, detail_len = len(title), len(subtitle), len(detail)

    header = struct.pack(
        "<14I",
        SWITCH_HEADER_SIZE,
        footer_pos,
        meta_pos,
        text_pos,
        text_pos + title_len,
        text_pos + title_len + subtitle_len,
        text_pos + title_len + subtitle_len + detail_len,
        payload_len,
        icon_size,
        SWITCH_META_STRUCT_SIZE,
        title_len,
        subtitle_len,
        detail_len,
        len(trailing),
    )

    if len(save.date) != 7:
        raise ValueError("date tuple must hold 7 values")

    return (
        header
        + payload
        + icon
        + struct.pack("<7I", *save.date)
        + title
        + subtitle
        + detail
        + trailing
    )


# --------------------------------------------------------------------------- #
# Steam container
# --------------------------------------------------------------------------- #


def steam_compress(payload: bytes, level: int = DEFAULT_ZSTD_LEVEL) -> bytes:
    return zstandard.ZstdCompressor(level=level, write_content_size=True).compress(
        payload
    )


def steam_decompress(blob: bytes) -> bytes:
    return zstandard.ZstdDecompressor().decompress(blob, max_output_size=1 << 30)


def parse_detail_json(blob: bytes) -> dict:
    return json.loads(blob.decode("utf-8-sig"))


def build_detail_json(detail: dict) -> bytes:
    """Byte-identical to the game's own formatting (UTF-8, CRLF, 4 spaces)."""
    text = json.dumps(detail, indent=4, ensure_ascii=False)
    return text.replace("\n", "\r\n").encode("utf-8")


def read_steam(path: str) -> SteamSave:
    if os.path.isdir(path):
        directory = path
    else:
        directory = os.path.dirname(os.path.abspath(path))

    dat = os.path.join(directory, STEAM_PAYLOAD_NAME)
    icon_path = os.path.join(directory, ICON_NAME)
    detail_path = os.path.join(directory, DETAIL_JSON_NAME)

    for needed in (dat, icon_path, detail_path):
        if not os.path.isfile(needed):
            raise FileNotFoundError(f"missing Steam save file: {needed}")

    with open(dat, "rb") as handle:
        blob = handle.read()
    if not blob.startswith(STEAM_ZSTD_MAGIC):
        raise ValueError(f"{dat} is not a zstd stream (is this really a Steam save?)")

    with open(icon_path, "rb") as handle:
        icon = handle.read()
    with open(detail_path, "rb") as handle:
        detail = parse_detail_json(handle.read())

    return SteamSave(payload=steam_decompress(blob), icon=icon, detail=detail)


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


def switch_to_steam(save: SwitchSave) -> SteamSave:
    detail = {
        "Time": {
            "year": save.date[0],
            "month": save.date[1],
            "day": save.date[2],
            "hour": save.date[3],
            "minute": save.date[4],
            "second": save.date[5],
        },
        "title": _strip_nul(save.title).decode("utf-8"),
        "subtitle": _strip_nul(save.subtitle).decode("utf-8"),
        "detail": _strip_nul(save.detail).decode("utf-8"),
        "user_param": save.date[6],
    }
    return SteamSave(payload=save.payload, icon=save.icon, detail=detail)


def steam_to_switch(save: SteamSave) -> SwitchSave:
    time = save.detail["Time"]
    date = (
        int(time["year"]),
        int(time["month"]),
        int(time["day"]),
        int(time["hour"]),
        int(time["minute"]),
        int(time["second"]),
        int(save.detail.get("user_param", 0)),
    )
    return SwitchSave(
        payload=save.payload,
        icon=save.icon,
        date=date,
        title=save.detail["title"].encode("utf-8") + b"\x00",
        subtitle=save.detail["subtitle"].encode("utf-8") + b"\x00",
        detail=save.detail["detail"].encode("utf-8") + b"\x00",
    )


def apply_patches(payload: bytes, patches) -> bytes:
    """Apply `OFFSET=VALUE` uint32 patches (used only with --set-u32)."""
    if not patches:
        return payload
    buffer = bytearray(payload)
    for patch in patches:
        try:
            offset_text, value_text = patch.split("=", 1)
            offset = int(offset_text, 0)
            value = int(value_text, 0)
        except ValueError as exc:
            raise ValueError(
                f"bad --set-u32 value {patch!r} (expected OFFSET=VALUE)"
            ) from exc
        if offset < 0 or offset + 4 > len(buffer):
            raise ValueError(f"patch offset {offset:#x} is outside the payload")
        struct.pack_into("<I", buffer, offset, value & 0xFFFFFFFF)
    return bytes(buffer)


def finalize_payload(payload: bytes, patches) -> tuple:
    """Patch (optional), recompute the checksum, report whether it was valid."""
    was_valid = read_checksum(payload) == calc_checksum(payload)
    buffer = bytearray(apply_patches(payload, patches))
    store_checksum(buffer)
    return bytes(buffer), was_valid


# --------------------------------------------------------------------------- #
# Detection / IO
# --------------------------------------------------------------------------- #


def detect_kind(path: str) -> str:
    """Return 'switch' or 'steam' for a file or directory."""
    if os.path.isdir(path):
        if os.path.isfile(os.path.join(path, SWITCH_PAYLOAD_NAME)):
            return "switch"
        if os.path.isfile(os.path.join(path, STEAM_PAYLOAD_NAME)):
            return "steam"
        raise ValueError(
            f"{path!r} contains neither {SWITCH_PAYLOAD_NAME} nor {STEAM_PAYLOAD_NAME}"
        )

    with open(path, "rb") as handle:
        head = handle.read(8)
    if head.startswith(STEAM_ZSTD_MAGIC):
        return "steam"
    if len(head) >= 8:
        return "switch"
    raise ValueError(f"cannot determine the save format of {path!r}")


def find_switch_file(path: str) -> str:
    if os.path.isdir(path):
        return os.path.join(path, SWITCH_PAYLOAD_NAME)
    return path


def load_source(path: str):
    kind = detect_kind(path)
    if kind == "switch":
        with open(find_switch_file(path), "rb") as handle:
            return "switch", parse_switch(handle.read())
    return "steam", read_steam(path)


def write_steam(directory: str, save: SteamSave, level: int) -> list:
    os.makedirs(directory, exist_ok=True)
    outputs = {
        STEAM_PAYLOAD_NAME: steam_compress(save.payload, level),
        ICON_NAME: save.icon,
        DETAIL_JSON_NAME: build_detail_json(save.detail),
    }
    for name, blob in outputs.items():
        with open(os.path.join(directory, name), "wb") as handle:
            handle.write(blob)
    return list(outputs)


def write_switch(directory: str, save: SwitchSave) -> list:
    os.makedirs(directory, exist_ok=True)
    name = SWITCH_PAYLOAD_NAME
    with open(os.path.join(directory, name), "wb") as handle:
        handle.write(build_switch(save))
    return [name]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def describe(source_kind: str, save) -> str:
    lines = [f"format        : {source_kind}"]
    if source_kind == "switch":
        lines.append(f"payload       : {len(save.payload)} bytes")
        lines.append(f"icon          : {len(save.icon)} bytes")
        lines.append(f"date          : {save.date[:6]}")
        lines.append(f"user_param    : {save.date[6]}")
        lines.append(
            f"title         : {_strip_nul(save.title).decode('utf-8', 'replace')}"
        )
        lines.append(
            f"subtitle      : {_strip_nul(save.subtitle).decode('utf-8', 'replace')}"
        )
    else:
        lines.append(f"payload       : {len(save.payload)} bytes")
        lines.append(f"icon          : {len(save.icon)} bytes")
        time = save.detail.get("Time", {})
        lines.append(
            "date          : "
            + "-".join(str(time.get(key, "?")) for key in ("year", "month", "day"))
            + " "
            + ":".join(str(time.get(key, "?")) for key in ("hour", "minute", "second"))
        )
        lines.append(f"user_param    : {save.detail.get('user_param')}")
        lines.append(f"title         : {save.detail.get('title')}")
        lines.append(f"subtitle      : {save.detail.get('subtitle')}")

    checksum = read_checksum(save.payload)
    computed = calc_checksum(save.payload)
    lines.append(f"payload[0x00] : {struct.unpack_from('<I', save.payload, 0)[0]}")
    lines.append(f"payload[0x04] : {struct.unpack_from('<I', save.payload, 4)[0]}")
    lines.append(
        f"checksum      : 0x{checksum:08X} "
        + ("(ok)" if checksum == computed else f"(MISMATCH, expected 0x{computed:08X})")
    )
    return "\n".join(lines)


def cmd_info(args) -> int:
    kind, save = load_source(args.path)
    print(describe(kind, save))
    return 0


def cmd_convert(args) -> int:
    source_kind, save = load_source(args.src)
    target_kind = args.to or ("steam" if source_kind == "switch" else "switch")
    if target_kind == source_kind:
        raise SystemExit(
            f"source and target are both {source_kind}; use --to to force the other format"
        )

    payload, was_valid = finalize_payload(save.payload, args.set_u32)
    if not was_valid:
        print("warning: the source checksum was invalid; it has been recomputed")
    save = dataclasses.replace(save, payload=payload)

    target_names = (
        (STEAM_PAYLOAD_NAME, ICON_NAME, DETAIL_JSON_NAME)
        if target_kind == "steam"
        else (SWITCH_PAYLOAD_NAME,)
    )
    existing = [
        name for name in target_names if os.path.isfile(os.path.join(args.dst, name))
    ]
    if existing and not args.force:
        raise SystemExit(
            f"{args.dst} already contains {', '.join(existing)}; use --force to overwrite"
        )

    if target_kind == "steam":
        converted = switch_to_steam(save)
        written = write_steam(args.dst, converted, args.level)
    else:
        converted = steam_to_switch(save)
        written = write_switch(args.dst, converted)

    print(f"{source_kind} -> {target_kind}")
    print(
        f"payload       : {len(payload)} bytes (copied verbatim, checksum recomputed)"
    )
    if not args.set_u32:
        print(
            "note          : payload[0x00] = "
            f"{struct.unpack_from('<I', payload, 0)[0]} (kept as-is; use --set-u32 0x0=N to force)"
        )
    print(f"output dir    : {os.path.abspath(args.dst)}")
    for name in written:
        path = os.path.join(args.dst, name)
        print(f"  {name:<13} {os.path.getsize(path)} bytes")
    return 0


def cmd_selftest(args) -> int:
    failures = []

    def check(label, condition):
        print(("  PASS  " if condition else "  FAIL  ") + label)
        if not condition:
            failures.append(label)

    with open(find_switch_file(args.switch), "rb") as handle:
        switch_bytes = handle.read()
    switch_save = parse_switch(switch_bytes)
    steam_save = read_steam(args.steam)

    print("checksums")
    check(
        "switch payload checksum is valid",
        read_checksum(switch_save.payload) == calc_checksum(switch_save.payload),
    )
    check(
        "steam payload checksum is valid",
        read_checksum(steam_save.payload) == calc_checksum(steam_save.payload),
    )

    print("container round trips")
    check(
        "switch parse -> build is byte exact", build_switch(switch_save) == switch_bytes
    )
    with open(os.path.join(args.steam, DETAIL_JSON_NAME), "rb") as handle:
        raw_detail = handle.read()
    check(
        "steam detail.json re-serialises byte exact",
        build_detail_json(steam_save.detail) == raw_detail,
    )

    print("switch -> steam -> switch")
    as_steam = switch_to_steam(switch_save)
    blob = steam_compress(as_steam.payload, DEFAULT_ZSTD_LEVEL)
    back = steam_to_switch(
        SteamSave(
            payload=steam_decompress(blob), icon=as_steam.icon, detail=as_steam.detail
        )
    )
    check("payload survives the round trip", back.payload == switch_save.payload)
    check("icon survives the round trip", back.icon == switch_save.icon)
    check("date survives the round trip", tuple(back.date) == tuple(switch_save.date))
    check(
        "text fields survive the round trip",
        (back.title, back.subtitle, back.detail)
        == (switch_save.title, switch_save.subtitle, switch_save.detail),
    )
    check("rebuilt switch file is byte exact", build_switch(back) == switch_bytes)

    print("steam -> switch -> steam")
    as_switch = steam_to_switch(steam_save)
    check(
        "payload survives the round trip",
        parse_switch(build_switch(as_switch)).payload == steam_save.payload,
    )
    check(
        "icon survives the round trip",
        parse_switch(build_switch(as_switch)).icon == steam_save.icon,
    )
    again = switch_to_steam(parse_switch(build_switch(as_switch)))
    check(
        "detail.json survives the round trip",
        build_detail_json(again.detail) == raw_detail,
    )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sora_save_convert.py",
        description="Convert Trails in the Sky 1st Chapter saves between Switch and Steam.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  sora_save_convert.py info save000_switch\n"
            "  sora_save_convert.py convert save000_switch save000_steam_out\n"
            "  sora_save_convert.py convert save000_steam  save000_switch_out\n"
            "  sora_save_convert.py selftest --switch save000_switch --steam save000_steam\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show what is inside a save")
    info.add_argument("path", help="save folder or .dat/user.dat file")
    info.set_defaults(func=cmd_info)

    convert = sub.add_parser(
        "convert", help="convert a save to the other platform's format"
    )
    convert.add_argument("src", help="source save folder or file")
    convert.add_argument("dst", help="output folder")
    convert.add_argument(
        "--to",
        choices=("steam", "switch"),
        help="target format (default: the other one)",
    )
    convert.add_argument(
        "--level", type=int, default=DEFAULT_ZSTD_LEVEL, help="zstd level for user.dat"
    )
    convert.add_argument(
        "--set-u32",
        action="append",
        metavar="OFFSET=VALUE",
        help="patch a uint32 in the payload before packing (repeatable, e.g. 0x0=7)",
    )
    convert.add_argument(
        "--force", action="store_true", help="overwrite existing output files"
    )
    convert.set_defaults(func=cmd_convert)

    selftest = sub.add_parser(
        "selftest", help="verify the converter against known-good saves"
    )
    selftest.add_argument(
        "--switch", required=True, help="folder/file holding a Switch save"
    )
    selftest.add_argument("--steam", required=True, help="folder holding a Steam save")
    selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
