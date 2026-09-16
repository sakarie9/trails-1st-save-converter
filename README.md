# Trails in the Sky 1st Chapter — Switch ⇄ Steam save converter

[中文说明 / Chinese README](README.zh-CN.md)

`sora_save_convert.py` is a small standalone Python script that converts
**Trails in the Sky 1st Chapter / 空の軌跡 the 1st** save data between the
**Nintendo Switch** and **Steam (PC)** formats, in both directions. It handles
both kinds of data the game writes: slot saves (`saveNNN`) and system data
(`sdmemNNN`).

It is a re-implementation of the file formats documented by the GUI save editor
[turtle-insect/TrailsintheSky1stChapter](https://github.com/turtle-insect/TrailsintheSky1stChapter),
which can *edit* both formats but has no "convert to the other platform"
command. This script adds exactly that conversion.

The game-state blob (the "payload") is copied **byte for byte** — nothing inside
your save is rewritten or re-encoded. Only the container and the slot metadata
are repacked, and the save's CRC32 is recomputed.

---

## Requirements

* Python 3.8 or newer
* The `zstandard` module:

```bash
pip install zstandard
```

No other third-party dependency is needed (Pillow is *not* required).

## Quick start

```bash
# Switch -> Steam
python3 sora_save_convert.py convert save000_switch save000_steam_out

# Steam -> Switch
python3 sora_save_convert.py convert save000_steam  save000_switch_out

# Look at what is inside a save
python3 sora_save_convert.py info save000_switch

# Verify the converter against a known-good pair of saves
python3 sora_save_convert.py selftest --switch save000_switch --steam save000_steam
```

> **Only convert save slots (`saveNNN`).** That is all you need to keep playing
> on the other platform. Do **not** convert system data (`sdmemNNN`): it only
> holds platform-specific options and records that the target platform generates
> by itself, and there is nothing in it worth migrating.

`SRC` may be a save **folder** or a single **file**:

* `data.dat` (or a folder containing it) is detected as a Switch save.
* `user.dat` (or a folder containing it) is detected as a Steam save. When you
  point at a bare `user.dat`, its `icon0.png` and `detail.json` are taken from
  the same folder.

Common options:

* `--to steam|switch` — force the target format (default: the other platform).
* `--level N` — zstd compression level for `user.dat` (default `3`, the level
  the reference editor writes).
* `--set-u32 OFFSET=VALUE` — patch a little-endian `uint32` in the payload
  before packing (repeatable, e.g. `--set-u32 0x0=7`). The checksum is
  recomputed afterwards.
* `--force` — overwrite files that already exist in the destination folder.

Run `python3 sora_save_convert.py --help` for the full list.

## What gets written

| Target | Files produced in `DST` |
| --- | --- |
| Switch | `data.dat` |
| Steam | `user.dat`, `icon0.png`, `detail.json` |

`detail.json` is rewritten with UTF-8, CRLF line endings and 4-space indentation,
which is byte-identical to what the game itself writes.

## The two save formats

Both platforms use the same payload layout. Its first 12 bytes are:

| Offset | Size | Meaning |
| --- | --- | --- |
| `0x00` | u32 | small field, copied through unchanged (see caveats) |
| `0x04` | u32 | small field, copied through unchanged |
| `0x08` | u32 | CRC32 over `payload[0x0C:]`, seeded with `payload_size - 0x0C` |
| `0x0C` | — | game data |

Only the container differs:

**Steam** — one folder per save slot under
`%USERPROFILE%\Saved Games\Falcom\Trails in the Sky 1st Chapter\savedata\`,
containing:

* `user.dat` — the payload, compressed with zstd;
* `icon0.png` — a 228×128 screenshot used in the load menu;
* `detail.json` — slot metadata (`Time`, `title`, `subtitle`, `detail`,
  `user_param`).

**Switch** — one self-contained `data.dat` per slot:

```
[56-byte header][raw payload][228×128 PNG][7×u32 date][title\0subtitle\0detail\0][4 zero bytes]
```

The 56-byte header is 14 little-endian `uint32` values holding the header size,
the footer position, the payload length, the PNG size, and the offsets/lengths
of the metadata block. The script rebuilds all of them from scratch, so no
"template" Switch save is needed for Steam → Switch conversion.

### Two kinds of data, one container

| Folder | Payload | Notes |
| --- | --- | --- |
| `saveNNN` | 2,048,008 bytes | actual game progress; the interesting one to migrate |
| `sdmemNNN` | a few hundred bytes | system data: options, achievements, etc. |

Both use the identical Switch container and the identical Steam slot layout, so
the script repacks either. The `icon0.png` of the system data is even identical
on both platforms. The **payload contents**, however, are platform specific for
system data (the Switch and Steam samples differ in length and in the options
they store), so repacking system data only carries the source platform's blob.
Let the target platform create its own system data; migrate slot saves.

## What has been verified

The reference saves used during development (`save000_*` slot saves and
`sdmem000_*` system data) are **not shipped with the script**; run `selftest`
against your own pair. Every check passed for both kinds of data:

* both payload checksums are valid (the formula above reproduces the game's own
  values for the 2,048,008-byte slot payload and the few-hundred-byte system
  payload alike);
* Switch `parse → build` reproduces `data.dat` byte for byte;
* Steam `detail.json` re-serialises byte for byte;
* `Switch → Steam → Switch` reproduces `data.dat` byte for byte;
* `Steam → Switch → Steam` preserves the payload, `icon0.png` and `detail.json`
  byte for byte.

## Caveats

* **`payload[0x00]`.** In the reference *slot* saves this field is `3` (Switch)
  and `7` (Steam). It is *not* covered by the checksum and the reference editor
  never touches it. It may be a save-format version rather than a platform tag,
  so the script copies it through unchanged. If the game refuses a converted
  save, try forcing it:
  `--set-u32 0x0=7` (going to Steam) or `--set-u32 0x0=3` (going to Switch).
* **System data is not progress.** As noted above, the `sdmemNNN` payload is
  platform specific, so converting it across platforms is a repack, not a
  migration. Prefer keeping the target platform's own `sdmemNNN`.
* **Back up your saves.** Keep a copy of the original slot folder before
  replacing anything, and consider turning off Steam Cloud for the game while
  you swap files, so a stale cloud copy does not overwrite your conversion.
* **Dumping a Switch save requires a homebrewed console** (e.g. JKSV,
  Checkpoint, nxdumptool). The script only works on the extracted `data.dat`.
* **Format revisions.** The container layout and the checksum rule come from the
  current game build; a future patch that changes the save format would require
  updating the script.
* **Not official.** This is an unofficial fan tool. Use at your own risk.

## Credits and references

* [turtle-insect/TrailsintheSky1stChapter](https://github.com/turtle-insect/TrailsintheSky1stChapter)
  — the Switch & Steam save editor whose formats this script re-implements.
* [Steam discussion: save folder layout](https://steamcommunity.com/app/3375780/discussions/0/695374081774704068/)
  — confirms the `detail.json` / `icon0.png` / `user.dat` slot structure.
* [3DM forum thread](https://bbs.3dmgame.com/thread-6618321-1-1.html) — community
  tool that also advertises Switch ⇄ Steam conversion.

## License

GPL-3.0. The file-format knowledge comes from the GPL-3.0 licensed reference
editor; this script is an independent re-implementation and keeps the same
license.
