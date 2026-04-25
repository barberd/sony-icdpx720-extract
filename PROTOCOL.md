# Sony ICD-PX720 USB Protocol & Data Format Notes

Reverse-engineered from USB capture (Wireshark usbmon) of Sony Digital Voice
Editor communicating with the device, plus disassembly of
IcdNStor3.dll and IcdComm4.dll from the DVE3 installer.

## USB Device Info

- VID: 054c (Sony), PID: 0387
- USB 2.0 High Speed (480Mbps)
- Device Class: 0xFF (Vendor Specific)
- Interface Class: 0xFF (Vendor Specific)
- Endpoints: EP 0x81 IN (Bulk, 512b), EP 0x02 OUT (Bulk, 512b)
- Control endpoint max packet: 64 bytes

## Transport Layer

All commands use vendor control transfers on endpoint 0. Bulk endpoint 0x81
is used for data transfers (file listings and audio data). EP 0x02 OUT is
not used in observed sessions.

### Control Transfer Types

| Name | bmRequestType | bRequest | wValue | wIndex | Direction |
|------|--------------|----------|--------|--------|-----------|
| SEND | 0x41 | 128 | 0xabab | 0 | Host→Device (data=command) |
| POLL | 0xc1 | 1 | 0xabab | 0 | Device→Host (4 bytes) |
| READ | 0xc1 | 129 | 0xabab | 0 | Device→Host (N bytes) |
| GET_STATUS | 0x80 | 0 | 0x0000 | 0 | Device→Host (2 bytes) |

### POLL Response (4 bytes)

- Byte 0: 0x0f when data pending, 0x00 when idle
- Byte 1: Bit 7 (0x80) = "ready for READ". If clear, poll again.
- Bytes 2-3: May encode response size (unconfirmed)

### Command/Response Pattern

Every command follows this sequence:

```
SEND(command) → POLL_WAIT(until byte[1] & 0x80) → READ(N) → POLL(expect 0x00000000)
```

For commands that trigger bulk data (LIST_END, READ_FILE):

```
SEND → POLL_WAIT → READ(response) → [bulk IN packets] → POLL_WAIT → READ(completion) → POLL
```

## Command Packet Format

All SEND payloads start with a 12-byte magic header:

```
00 e0 00 08 00 46 ab ab 00 00 00 00
```

This is a fixed constant stored in IcdComm4.dll at offset 0x28184.

Followed by a 2-byte command code, then command-specific payload.

### Command Codes

| Code | Name | Payload | Description |
|------|------|---------|-------------|
| 09 00 | QUERY | sub(1) + 0xff + 8×0x00 | Query device info. sub: 01=device info, 03=capabilities, 04=storage |
| 09 20 | LIST_READ | 00 ff + 8×0x00 | Read next page of folder/file listing |
| 09 10 | LIST_END | 01 ff + 8×0x00 | End listing, triggers bulk file entry dump |
| 0a 00 | SET_PREFERENCE | sub + payload | Set device preferences (CIcdComm4::SetPreferenceMenu). Used to select folder view. |
| 11 ff | READ_FILE | 26-byte payload | Download file data |

### QUERY Responses

| Sub | Response Size | Content |
|-----|--------------|---------|
| 0x01 | 116 bytes | Device info, contains model string "ICD-PX720" at ~offset 0x24 |
| 0x03 | 48 bytes | Unknown, possibly capabilities. Needs 2+ polls before ready. |
| 0x04 | 82 bytes | Storage info |

### SET_PREFERENCE (SetPreferenceMenu)

Command 0x0a with sub-command at offset 0x0d-0x0e. This is
`CIcdComm4::SetPreferenceMenu`, which writes a 26-byte
`_SETPREFERENCEMENUFORVOCE` struct to the device. Sony's software
(PXVoice.dll) reads the current preferences from the device, updates the
timestamp with the current local time, and writes them back during
initialization. The folder view byte (offset 0x25) controls which folder
the subsequent LIST commands enumerate.

The "all folders" variant (folder "F") is a fixed 50-byte packet:

```
00e000080046abab 00000000 0a0004 50 0000001a
00000000 0001 000000000001 ff00ff 0046
0000 0101 07ea 0413 101e 0900
```

Packet structure:

```
Offset  Size  Description
0x00    12    Magic header
0x0c    1     Command = 0x0a
0x0d    2     htons(sub-command): 0x0004=by-index, 0x0001=by-name
0x0f    1     Constant 0x50 ('P')
0x10    4     htonl(0x1a) — payload size (26 bytes)
0x14    4     htonl(0x00)
0x18    26    _SETPREFERENCEMENUFORVOCE struct (copied mostly raw, uint16 at
              struct offset 0x0c is byte-swapped via htons)
```

Key fields in the 26-byte struct (at packet offset 0x18):

```
Struct   Packet
offset   offset   Description
0x11     0x29     Folder letter: 'A'-'E' for individual folders, 'F' for all
0x16     0x2e     Timestamp: year (big-endian uint16)
0x18     0x30     Timestamp: month, day
0x1a     0x32     Timestamp: hour, minute (only 2 bytes shown; seconds follow)
```

A second variant uses sub-command 0x0001 (by-name) with the folder name as a
string (e.g. "User") in the payload instead of the struct.

### READ_FILE Payload (26 bytes = 13 big-endian uint16)

```
[0]  0x0001   constant
[1]  file_idx  1-based file index
[2]  0x0000
[3]  0x0000
[4]  offset    start block (1-based, inclusive)
[5]  0x0000
[6]  end       end block (inclusive)
[7]  0xffff
[8]  0xffff
[9]  0x0000
[10] 0x0000
[11] flags1    0x000f for download, 0x0001 for preview
[12] flags2    0xa000 first chunk, 0xa400 continuation
```

Block size = 1024 bytes. Each block = 2 bulk packets (512 bytes each).

## Init Sequence

```
1. GET_STATUS → POLL
2. QUERY 0x01 → POLL_WAIT → READ(116) → POLL
3. QUERY 0x03 → POLL_WAIT → READ(48) → POLL    (may need 2+ polls)
4. QUERY 0x04 → POLL_WAIT → READ(82) → POLL
5. SET_PREFERENCE "F" → POLL_WAIT → READ(24) → POLL
6. LIST_READ ×3 → (each: POLL_WAIT → READ(356) → POLL)
7. LIST_END → POLL_WAIT → READ(24)
8. [bulk file listing packets]
9. POLL_WAIT → READ(24) → POLL
```

## Bulk File Listing Format

After LIST_END + READ(24), the device sends ~115 bulk packets (512 bytes each)
containing the complete file inventory. No explicit trigger needed — device
starts sending after the LIST_END response.

### Packet Types in Bulk Listing

**Flash Address Table** (1 packet, at ~packet index 7):
Identified by bytes 8-11 = `80 00 00 00`. Contains 16-byte records:

```
Offset  Size  Description
0-3     u32   start_addr_high (big-endian)
4-7     u32   start_addr_low
8-11    u32   end_addr_high | 0x80000000 (last-segment flag)
12-15   u32   end_addr_low
```

Each record represents one segment of a recording's location on flash. Terminated by
`0xffffffff`. File size in bytes = `end - start + 1` (where end has bit 31
masked off in the high word). Block count = `ceil((end - start) / 1024)`.

If bit 31 of `end_addr_high` is set (`0x80000000`), this is the last (or only)
segment of the file. If clear, the next record continues the same file.
Multi-segment files occur when a recording spans non-contiguous flash regions
(e.g. after deletions create gaps). Total file size = sum of all segment sizes.

**File Entry** (1 packet each, starting ~packet index 15):
Identified by first 4 bytes = `ff ff 90 00`.

```
Offset  Size  Description
0x000   4     Magic: ff ff 90 00
0x004   16    Filename (null-terminated ASCII, e.g. "250411_001")
0x014   242   Zeros (padding)
0x106   10    ff padding
0x110   4     ff ff 90 00 (second marker)
0x114   104   Zeros
0x17c   4     ff ff ff ff
0x180   2     00 00
0x182   2     Constant 0x3318 across all files (possibly bitrate-related)
0x184   60    ff padding
0x1c0   1     Unknown (values: 0x78, 0x31, 0x38, 0xac — not correlated with size)
0x1c1   1     0x00
0x1c2   2     00 ff
0x1c4   2     Year (big-endian, e.g. 0x07e9 = 2025)
0x1c6   1     Month
0x1c7   1     Day
0x1c8   1     Hour
0x1c9   1     Minute
0x1ca   1     Second
0x1cb   1     Unknown (0x05 for 2025 recordings)
0x1cc   11    Device name "SONY ICD-PX" (null-terminated)
0x1e4   8     Constant 33 09 0c 01 14 68 65 5f (firmware/serial?)
0x1ff   1     Unknown — possibly a device-internal flash block identifier
```

Files starting with "Z" (e.g. Z0000040) are system/empty slots, not recordings.

### Byte at 0x1ff

Purpose not fully understood. Values observed (0xa0, 0x2e, 0xeb, 0x95) don't
correlate with file listing order or flash table position. May be a
device-internal flash block identifier. Not needed for extraction.

## Download Protocol

Each file is downloaded in chunks of 1000 blocks (1,024,000 bytes).

### Chunk Sequence

```
SEND(READ_FILE) → POLL_WAIT → READ(40)    # pre-bulk
[bulk IN packets]                           # audio data
POLL_WAIT → READ(40) → POLL               # post-bulk completion
```

### Chunk Parameters

**First chunk**: offset=1, end=min(999, file_blocks), flags=(0xf, 0xa000)
- Returns (end - offset + 1) blocks = 2000 bulk packets for a full chunk
- All data is audio

**Continuation chunks**: offset=prev_end, end=min(offset+1000, file_blocks), flags=(0xf, 0xa400)
- Returns (end - offset + 1) blocks = 2002 bulk packets for a full chunk
- First 1024 bytes (2 packets) overlap with end of previous chunk — must be stripped
- The overlap exists because offset equals the previous chunk's end (inclusive on both sides)

**Last chunk**: same as continuation but fewer packets since end = file_blocks

### Packet Counts (observed)

- First chunk: 2000 packets (1000 blocks × 2)
- Full continuation: 2002 packets (1001 blocks × 2, includes 1-block overlap)
- Last chunk: varies, always < 2002

### File Size Calculation

```python
flash_size_bytes = flash_end - flash_start  # from flash address table
block_count = math.ceil(flash_size_bytes / 1024)
exact_file_size = flash_end - flash_start + 1  # for trimming output
```

The `+1` was confirmed by matching against Digital Voice Editor output (which adds
ID3 tags but the audio portion is exactly `flash_end - flash_start + 1` bytes).

## Delete Protocol (from disassembly, not tested)

Decoded from `CIcdComm4::DeleteMessage` in IcdComm4.dll. **Not captured in any
USB session — the following is based solely on disassembly and has not been
verified against a real device.**

### Command

Standard 12-byte magic header, followed by:

```
Offset  Size  Description
0x0c    1     Command = 0x12
0x0e    1     Folder ID (param_1, a single byte)
0x0f    1     0x00
0x10    2     htons(file_index)
0x12    99    Deletion mask (optional, 99 bytes from caller — possibly a
              bitmap of which files to delete, or zeros)
```

Total command size: 0x75 (117) bytes. Sent via `SendCmd`, response via
`ReceiveCmd`.

### Observed behavior

Deleting a recording removes its flash address table entry but leaves the
file entry packet (the `ffff9000` record) in the bulk listing. Deleted files
appear in the listing with a name but have no flash data. The device does not
compact or renumber remaining entries — flash table entries for surviving files
stay in their original order.

## Upload Protocol (from disassembly, not tested)

Decoded from `CIcdComm4::AddMessageST` and `CIcdComm4::SetMessageInfo` in
IcdComm4.dll. **Not captured in any USB session — the following is based
solely on disassembly and has not been verified against a real device.**

### Command (0x200 bytes)

```
Offset  Size  Description
0x00    12    Standard magic header
0x0c    1     Command = 0x10
0x0d    1     (unused/padding)
0x0e    2     htons(param_1) — hypothesized: folder number
0x10    2     htons(param_2) — hypothesized: file index or slot
0x12    2     htons(param_3) — sub-command (short, values -1/0/2 observed)
0x14    4     htonl(data_size) — size of audio data to upload in bytes
0x18    0x1e8 Serialized file metadata (see below)
```

### File metadata (0x1e8 bytes at offset 0x18)

This is a serialized `_ENTRYTYPE1000FORPX` structure. `SetMessageInfo` has
three modes: mode 0 copies raw (already big-endian), mode 1 byte-swaps from
host to big-endian (used by `AddMessageST`), mode 2 byte-swaps from big-endian
to host (used when reading).

The structure fields correspond to the same data in the 512-byte file entry
packets from the bulk listing, but at different offsets and with explicit
byte-swapping. Key identified fields:

```
Struct   Wire
offset   action    Description
0x00     htons     Unknown uint16
0x02-06  raw       5 bytes, unknown (byte at 0x07 skipped)
0x08-0a  raw       3 bytes, unknown (byte at 0x0b skipped)
0x0c     htons     Unknown uint16 (possibly codec/bitrate, 0x3318 observed)
0x0e-12  raw       5 bytes, unknown
0x18     64-bit    Hypothesized: file size or flash address (8 bytes, byte-swapped)
0x22     htons     Year (e.g. 0x07ea = 2026)
0x24     htons     Month/day packed
0x26     htons     Hour/minute/second packed
0x28     string    Filename (max 258 bytes, null-terminated)
0x12a    htons     Unknown uint16
0x12c    string    Device name (max 104 bytes, e.g. "SONY ICD-PX")
0x198    64-bit    Hypothesized: file size or flash address (8 bytes, byte-swapped)
0x1a0    htonl     Unknown uint32
0x1a4    htons     Unknown uint16
0x1a6    raw       1 byte, unknown
0x1a8    memcpy    64 bytes raw data, unknown purpose
```

### Upload sequence (hypothesized)

Based on the `AddMessageST` control flow:

```
1. SendCmd(command, 0x200 bytes)     — file metadata
2. ReceiveCmd                        — interim acknowledgment
3. BulkComm(audio_data, data_size)   — bulk transfer of MP3 data to device
4. ReceiveCmd                        — final acceptance
```

The `BulkComm` call uses the OUT bulk endpoint (EP 0x02) which is present on
the device but not used during download sessions.

### What would be needed to implement upload

- A USB capture of Digital Voice Editor uploading a file, to confirm the exact
  byte values for the unknown fields and verify the command sequence
- Understanding of which metadata fields the device validates vs ignores
- The constant fields (codec info, device name) can likely be copied from any
  existing file entry captured during a listing

## Audio Format

- Raw CBR MP3 (no ID3 tags, no Xing/LAME/VBRI headers)
- 192 kbps, 44100 Hz, stereo, MPEG1 Layer 3
- MP3 sync word: 0xfffb (or 0xfff3)
- Frame size alternates between 626-627 bytes (padding bit varies)
- Digital Voice Editor adds ID3v2 tags (TIT2=filename, TENC="SONY IC RECORDER MP3 1.0.1") when saving

## Sony Software Architecture

```
Digital Voice Editor
  └── PXVoice.dll        — PX-series device handler, adds ID3 tags on save
      └── IcdNStor3.dll   — Storage abstraction, file size calculation
          └── IcdComm4.dll — USB protocol layer (SendCmd, ReceiveCmd, BulkComm, GetReceiveStatus)
              └── ICDUSB3.sys — Kernel driver (WDF, vendor USB class)
```

IcdComm4.dll uses WS2_32.dll byte-swap functions (htons/htonl/ntohs/ntohl)
for endian conversion — the protocol is big-endian on the wire.

## Files in dev/

- `usbcap-sonyicdpx720.pcapng` — Full USB capture of Digital Voice Editor session
- `sony-protocol-session.txt` — Annotated protocol decode of the capture
- `sony-replay.py` — Replays the exact capture sequence (used to verify protocol)
- `extract-from-pcap.py` — Extracts MP3 files directly from the pcap
- `dump-toc.py` — Dumps TOC table, flash address table, and file entries from device
- `IcdNStor3.dll.txt` — Ghidra disassembly of storage layer
- `IcdComm4.dll.txt` — Ghidra disassembly of USB protocol layer
- `PXVoice.dll.txt` — Ghidra disassembly of PX-series device handler (COM server)
- `DVEdit.exe.txt` — Ghidra disassembly of Digital Voice Editor main application
- `IcdPCons.dll.txt` — Ghidra disassembly of ICD PC Console
- `shared/Sony/` — Original Sony software from DVE3 installer
- `shared/IcdComm4.dll` — Extracted from DVESetup_EN.exe via unshield
- `shared/001_A_*.mp3` — Reference files saved by Digital Voice Editor (with ID3 tags)

## Open Questions

1. **Byte at 0x1ff in file entries**: Values (e.g. 0xa0, 0x2e, 0xeb) and the
   data at bulk offset 0xa00 appear related to an internal mapping scheme, but
   the device maintains flash table entries in the same order as the file
   listing. The DLL's `SetTocMemory` overwrites the 0xa00 data with the flash
   address table from 0xe00 during initialization. The TOC table at offset
   0x000 maps file indices sequentially (0,1,2...) even after deletions.
   These byte values may be device-internal flash block identifiers.

2. **Byte at 0x1c0**: Purpose unknown. Values don't correlate with file size,
   duration, or recording quality. Not needed for extraction.

3. **File 090101_008**: Present in file listing but has no flash table entry.
   May be in a different folder or use a different storage format. The byte
   at 0x1ff for this file was 0x95 vs 0xa0/0x2e/0xeb for the three working files.

4. **Multiple folders**: Only tested with folder "F" (all files). Selecting
   individual folders (A-E) likely requires changing the folder byte (offset
   0x25) in the SET_PREFERENCE packet.

5. **Write support**: The protocol likely supports uploading/deleting recordings
   but this has not been explored.
