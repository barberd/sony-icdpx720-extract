# Sony ICD-PX720 USB Protocol & Data Format Notes

Reverse-engineered from USB capture (Wireshark usbmon) of Sony Digital Voice
Editor communicating with the device, plus disassembly of
IcdNStor3.dll, IcdComm4.dll, and PXVoice.dll from the DVE3 installer.

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

For commands that trigger bulk data (GetMessageInfoSizeST, GetVoiceDataST):

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

### Command Codes (from CIcdComm4 disassembly)

| Cmd  | Sub (wire) | DLL Function | Description |
|------|-----------|--------------|-------------|
| 0x09 | 00 01 | GetTargetIdentifier | Device info (model string, 116-byte response) |
| 0x09 | 00 03 | GetTargetStatusST | Device status (48-byte response, may need 2+ polls) |
| 0x09 | 00 04 | GetPreferenceInfo | Read preferences from device (82-byte response) |
| 0x09 | 20 00 | GetFolderCount | Enumerate folders (response contains count + folder entries) |
| 0x09 | 10 01 | GetMessageInfoSizeST | End listing, triggers bulk file entry dump |
| 0x0a | — | SetPreferenceMenu | Write preferences to device (selects folder view) |
| 0x11 | ff | GetVoiceDataST | Download file data (26-byte payload) |
| 0x10 | — | AddMessageST | Upload file data (not tested) |
| 0x12 | — | DeleteMessage | Delete recording (not tested) |

Command 0x09 is a generic query command. The sub-command at offset 0x0d is a
big-endian uint16 (htons-swapped). For example, GetFolderCount pushes
`htons(0x2000)` which produces wire bytes `0x20 0x00`.

### GetTargetIdentifier Response (116 bytes)

Contains device model string "ICD-PX720" at approximately offset 0x24.

### GetFolderCount Response

Returns folder count and folder entries in a single response. The response
size depends on the number of folders (e.g. 356 bytes for 5 folders).

```
Offset  Size  Description
0x20    2     Folder count (big-endian uint16, ntohs to get host value)
0x24    N×64  Folder entries (_ENTRYTYPE2000 structs)
```

Each folder entry (`_ENTRYTYPE2000`) is 64 bytes:

```
Offset  Size  Description
0x00    2     File count in this folder (big-endian uint16)
0x02    62    Folder name (null-terminated ASCII string, e.g. "A", "B")
```

For folder index `i` (0-based), the entry starts at response offset
`0x24 + i × 64`. The folder name character (e.g. 'A') is used as the
folder ID in SetPreferenceMenu.

The same command is sent 3× during DVE initialization. All three responses
appear identical; only the first is needed.

### SetPreferenceMenu

Command 0x0a. This is `CIcdComm4::SetPreferenceMenu`, which writes a 26-byte
`_SETPREFERENCEMENUFORVOCE` struct to the device. Sony's software
(PXVoice.dll) reads the current preferences via GetPreferenceInfo, updates
the timestamp with the current local time, and writes them back. The folder
ID at struct offset 0x0c controls which folder subsequent
GetMessageInfoSizeST commands enumerate.

Packet structure (50 bytes total):

```
Offset  Size  Description
0x00    12    Magic header
0x0c    1     Command = 0x0a
0x0d    2     htons(sub-command): 0x0004=by-index, 0x0001=by-name
0x0f    1     Constant 0x50 ('P')
0x10    4     htonl(0x1a) — payload size (26 bytes)
0x14    4     htonl(0x00)
0x18    26    _SETPREFERENCEMENUFORVOCE struct
```

The struct is bulk-copied to packet offset 0x18, then the uint16 at struct
offset 0x0c is byte-swapped via htons() and written to packet offset 0x24,
overwriting the raw-copied bytes at that position.

Key fields in the 26-byte `_SETPREFERENCEMENUFORVOCE` struct:

```
Struct   Packet   Wire
offset   offset   bytes    Description
0x0c     0x24     2        Folder ID (uint16, htons-swapped). Use the folder_id
                           value from GetFolderCount/GetFolderInfo responses.
0x11     0x29     1        Timestamp valid flag (0x01=valid, 0x00=invalid)
0x12     0x2a     2        Timestamp: year (big-endian uint16, e.g. 0x07ea=2026)
0x14     0x2c     1        Timestamp: month
0x15     0x2d     1        Timestamp: day
0x16     0x2e     1        Timestamp: hour
0x17     0x2f     1        Timestamp: minute
0x18     0x30     1        Timestamp: second
0x19     0x31     1        Timestamp: day of week
```

To select a folder, set the uint16 at struct offset 0x0c to the ASCII value
of the folder name character from the GetFolderCount response. For example,
folder "A" uses value 0x0041. On a little-endian host, this is stored in
memory as bytes `0x41 0x00`. SetPreferenceMenu reads this as host uint16
0x0041, applies htons() to get 0x4100, and stores it at packet offset 0x24
as little-endian bytes `0x00 0x41` — which is big-endian 0x0041 on the wire.

### GetVoiceDataST Payload (26 bytes = 13 big-endian uint16)

```
[0]  folder_idx  1-based folder index (from GetFolderCount)
[1]  file_idx    1-based file index (within the folder's listing)
[2]  0x0000
[3]  0x0000
[4]  offset      start block (1-based, inclusive)
[5]  0x0000
[6]  end         end block (inclusive)
[7]  0xffff
[8]  0xffff
[9]  0x0000
[10] 0x0000
[11] flags1      0x000f for download, 0x0001 for preview
[12] flags2      0xa000 first chunk, 0xa400 continuation
```

Block size = 1024 bytes. Each block = 2 bulk packets (512 bytes each).

## Init Sequence

```
1. GET_STATUS → POLL
2. GetTargetIdentifier → POLL_WAIT → READ(116) → POLL
3. GetTargetStatusST → POLL_WAIT → READ(48) → POLL    (may need 2+ polls)
4. GetPreferenceInfo → POLL_WAIT → READ(82) → POLL
5. SetPreferenceMenu(folder) → POLL_WAIT → READ(24) → POLL
6. GetFolderCount ×3 → (each: POLL_WAIT → READ(356) → POLL)
7. GetMessageInfoSizeST → POLL_WAIT → READ(24)
8. [bulk file listing packets]
9. POLL_WAIT → READ(24) → POLL
```

## Folder Enumeration

Folders are dynamically enumerated from the device, not hardcoded per model.
The `GetFolderCount` command (0x09 sub 0x2000) returns both the folder count
and folder entry data. `IcdNStor3.dll::GetFolderNStor` implements the
enumeration loop:

1. Call `GetFolderCount` → get count from response offset 0x20 (ntohs)
2. For each folder index 1..count:
   - Call `GetFolderInfo(index)` → read folder ID and name from response
   - Store folder ID in per-folder array

Folder selection for file listing is done via the **GetMessageInfoSizeST
sub-command**, not via SetPreferenceMenu. The sub-command byte at wire offset
0x0e encodes the 1-based folder index: `0x01` = folder A, `0x02` = folder B,
etc. SetPreferenceMenu is sent once during initialization and does not need
to change per folder.

To list files in a specific folder:

1. Send `GetFolderCount` (1×)
2. Send `GetMessageInfoSizeST` with sub-command `0x10 0x0N` where N = folder index
3. Read bulk packets → file entries and flash address table

**Important**: The device requires a `GetFolderCount` + `GetMessageInfoSizeST`
+ bulk drain cycle before each file download, even when downloading multiple
files from the same folder. DVE re-lists the folder before every download
operation. Omitting this causes pipe errors on subsequent downloads or when
switching folders.

## Bulk File Listing Format

After GetMessageInfoSizeST + READ(24), the device sends ~115 bulk packets
(512 bytes each) containing the complete file inventory for the selected
folder. No explicit trigger needed — device starts sending after the
GetMessageInfoSizeST response.

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

**File Entry** (2 packets each, starting ~packet index 15):

Each file entry consists of two consecutive 512-byte bulk packets. The first
is the main entry, the second contains extended metadata (title, artist).

*Main entry packet* — identified by first 4 bytes = `ff ff 90 00`:

```
Offset  Size  Description
0x000   2     Marker: ff ff
0x002   1     Type: 0x90 (main entry)
0x003   1     Flags: 0x00
0x004   16    Filename (null-terminated ASCII, e.g. "250411_001")
0x106   10    ff padding
0x110   2     Marker: ff ff
0x112   1     Type: 0x03
0x113   1     Flags: 0x00
0x114   92    User name (null-terminated ASCII, e.g. "User")
0x17c   4     ff ff ff ff
0x182   2     Codec/bitrate (big-endian, e.g. 0x3318 = 192kbps MP3)
0x1c4   2     Year (big-endian, e.g. 0x07e9 = 2025)
0x1c6   1     Month
0x1c7   1     Day
0x1c8   1     Hour
0x1c9   1     Minute
0x1ca   1     Second
0x1cc   11    Device name (null-terminated, "SONY ICD-PX")
```

*Extended metadata packet* — identified by first 4 bytes = `ff ff 03 00`:

Same layout as the main entry, with different fields populated:

```
Offset  Size  Description
0x000   2     Marker: ff ff
0x002   1     Type: 0x03 (extended metadata)
0x003   1     Flags: 0x00
0x004   258   Title (null-terminated ASCII, from ID3 TIT2 tag if copied MP3)
0x110   2     Marker: ff ff
0x112   1     Type: 0x03
0x113   1     Flags: 0x00
0x114   92    Artist (null-terminated ASCII, from ID3 TPE1 tag if copied MP3)
0x182   2     Codec/bitrate (may differ from main entry for copied files)
```

For native recordings, the extended packet may contain user-entered text or
uninitialized data. For MP3 files copied to the device, the title and artist
fields contain metadata extracted from the file's ID3 tags by the device
firmware.

Files starting with "Z" (e.g. Z0000040) are system/empty slots, not recordings.

### Byte at 0x1ff

Purpose not fully understood. Values observed (0xa0, 0x2e, 0xeb, 0x95, 0xda)
don't correlate with file listing order or flash table position. May be a
device-internal flash block identifier. Not needed for extraction.

## Download Protocol

Each file is downloaded in chunks of 1000 blocks (1,024,000 bytes).

### Chunk Sequence

```
SEND(GetVoiceDataST) → POLL_WAIT → READ(40)    # pre-bulk
[bulk IN packets]                                # audio data
POLL_WAIT → READ(40) → POLL                     # post-bulk completion
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
   Likely a recording in a different folder that appears as a ghost entry
   when listing folder A. The byte at 0x1ff for this file was 0x95 vs
   0xa0/0x2e/0xeb for the three working files.

4. **Write support**: The protocol likely supports uploading/deleting recordings
   but this has not been explored.
