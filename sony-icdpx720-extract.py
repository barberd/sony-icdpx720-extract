#!/usr/bin/env python3
"""
Sony ICD-PX720 Voice Recorder - File Extractor
Copyright (C) 2026 Don Barber <don@dgb3.net>
Extracts MP3 recordings directly via USB without Sony software.

Requirements:
  - Python 3.6+  |  pip install pyusb
  - Windows: Install WinUSB driver via Zadig (https://zadig.akeo.ie)
  - Linux: No extra setup needed
"""
import usb.core, usb.util, struct, time, sys, math
from mutagen.id3 import ID3, TIT2, TPE1, TDRC, ID3NoHeaderError

VID, PID = 0x054c, 0x0387
EP_IN = 0x81
T = 10000
M = b'\x00\xe0\x00\x08\x00\x46\xab\xab\x00\x00\x00\x00'
CHUNK = 1000

def send(dev, d):
    if isinstance(d, str): d = bytes.fromhex(d)
    dev.ctrl_transfer(0x41, 128, 0xabab, 0, d, T)

def poll(dev):
    return bytes(dev.ctrl_transfer(0xc1, 1, 0xabab, 0, 4, T))

def poll_wait(dev):
    for _ in range(200):
        r = poll(dev)
        if r[1] & 0x80: return r
        time.sleep(0.05)

def read(dev, n):
    return bytes(dev.ctrl_transfer(0xc1, 129, 0xabab, 0, n, T))

def get_folders(dev):
    """Send GetFolderCount and parse response to discover folders."""
    send(dev, M+b'\x09\x20\x00\xff'+b'\x00'*8)
    poll_wait(dev); resp = read(dev, 356); poll(dev)
    count = struct.unpack_from('>H', resp, 0x20)[0]
    folders = []
    for i in range(count):
        off = 0x24 + i * 64
        fc = struct.unpack_from('>H', resp, off)[0]
        name = resp[off+2:off+64].split(b'\x00')[0].decode('ascii', errors='replace')
        folders.append((i + 1, name, fc))  # (1-based index, name, file_count)
    return folders

def list_folder(dev, folder_idx):
    """List files in a folder by index (1-based). Returns (files, sizes, exact)."""
    # GetFolderCount then GetMessageInfoSizeST with folder index
    send(dev, M+b'\x09\x20\x00\xff'+b'\x00'*8); poll_wait(dev); read(dev, 356); poll(dev)
    send(dev, M+b'\x09\x10'+bytes([folder_idx])+b'\xff'+b'\x00'*8)
    poll_wait(dev); read(dev, 24)

    files, flash_table = [], None
    file_idx = 0
    for _ in range(500):
        try: pkt = bytes(dev.read(EP_IN, 512, T))
        except usb.core.USBTimeoutError: break
        if pkt[:4] == b'\xff\xff\x90\x00':
            name = pkt[4:20].split(b'\x00')[0].decode('ascii', errors='replace')
            file_idx += 1
            if name and not name.startswith('Z'):
                year = struct.unpack_from('>H', pkt, 0x1c4)[0]
                month, day = pkt[0x1c6], pkt[0x1c7]
                hour, minute, sec = pkt[0x1c8], pkt[0x1c9], pkt[0x1ca]
                friendly = f"{name}_{year}_{month:02d}_{day:02d}"
                timestamp = f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{sec:02d}"
                files.append((friendly, file_idx, '', '', timestamp))
        elif pkt[:4] == b'\xff\xff\x03\x00' and files and files[-1][1] == file_idx:
            raw_title = pkt[4:262].split(b'\x00')[0]
            raw_artist = pkt[0x114:0x17c].split(b'\x00')[0]
            try:
                title = raw_title.decode('ascii')
                artist = raw_artist.decode('ascii')
                friendly, idx, _, _, ts = files[-1]
                files[-1] = (friendly, idx, title, artist, ts)
            except UnicodeDecodeError:
                pass
        elif flash_table is None and len(pkt) >= 12 and pkt[8:12] == b'\x80\x00\x00\x00':
            flash_table = pkt
    poll_wait(dev); read(dev, 24); poll(dev)

    sizes, exact = {}, {}
    if flash_table:
        n, total = 0, 0
        for i in range(0, len(flash_table)-15, 16):
            v = struct.unpack('>IIII', flash_table[i:i+16])
            if v[0] == 0xffffffff: break
            s = (v[0]<<32)|v[1]; e = ((v[2]&0x7fffffff)<<32)|v[3]
            total += e - s + 1
            if v[2] & 0x80000000:
                n += 1
                sizes[n] = math.ceil((total - 1) / 1024)
                exact[n] = total
                total = 0
    return files, sizes, exact

def relist_folder(dev, folder_idx):
    """Re-list a folder before download (GetFolderCount + GetMessageInfoSizeST + drain bulk).
    DVE does this before every file download to set up device state."""
    send(dev, M+b'\x09\x20\x00\xff'+b'\x00'*8); poll_wait(dev); read(dev, 356); poll(dev)
    send(dev, M+b'\x09\x10'+bytes([folder_idx])+b'\xff'+b'\x00'*8)
    poll_wait(dev); read(dev, 24)
    for _ in range(500):
        try: dev.read(EP_IN, 512, T)
        except usb.core.USBTimeoutError: break
    poll_wait(dev); read(dev, 24); poll(dev)

def download(dev, fname, name, folder_idx, file_idx, blocks, exact_size, title='', artist='', timestamp=''):
    """Download a single file by folder and file index."""
    print(f"Downloading {name}...", flush=True)
    data = bytearray()
    offset, first = 1, True
    while offset < blocks:
        end = min(offset + CHUNK - (1 if first else 0), blocks)
        payload = struct.pack('>13H', folder_idx, file_idx, 0, 0, offset, 0, end,
            0xffff, 0xffff, 0, 0, 0xf, 0xa000 if first else 0xa400)
        send(dev, M+b'\x11\xff'+payload)
        poll_wait(dev); read(dev, 40)
        block = bytearray()
        for p in range((end-offset+1)*2 + 10):
            try: block.extend(dev.read(EP_IN, 512, 2000))
            except usb.core.USBTimeoutError: break
        poll_wait(dev); read(dev, 40); poll(dev)
        data.extend(block if first else block[1024:])
        first = False
        offset = end
        sys.stdout.write(f"\r  {len(data)*100//(blocks*1024)}%"); sys.stdout.flush()
    sz = exact_size if exact_size else len(data)
    path = f"{fname}_{name}.mp3"
    with open(path, 'wb') as f:
        f.write(data[:sz])
    try:
        tags = ID3()
        if title: tags.add(TIT2(encoding=3, text=[title]))
        if artist: tags.add(TPE1(encoding=3, text=[artist]))
        if timestamp: tags.add(TDRC(encoding=3, text=[timestamp]))
        if len(tags): tags.save(path, v1=2)
    except Exception:
        pass
    print(f"\r  Saved {path} ({sz:,} bytes)")

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("Sony ICD-PX720 not found. Is it plugged in?"); sys.exit(1)
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (usb.core.USBError, NotImplementedError):
        pass
    dev.set_configuration()

    # Init
    dev.ctrl_transfer(0x80, 0, 0, 0, 2, T); poll(dev)
    send(dev, M+b'\x09\x00\x01\xff'+b'\x00'*8); poll_wait(dev)
    info = read(dev, 116); poll(dev)
    for i in range(len(info)-3):
        if info[i:i+3] == b'ICD':
            print(f"Device: {info[i:i+16].split(b'\x00')[0].decode()}")
            break
    send(dev, M+b'\x09\x00\x03\xff'+b'\x00'*8); poll_wait(dev); read(dev, 48); poll(dev)
    send(dev, M+b'\x09\x00\x04\xff'+b'\x00'*8); poll_wait(dev); read(dev, 82); poll(dev)

    # Discover folders
    folders = get_folders(dev)

    print(f"Folders: {', '.join(f'{name} ({fc} files)' for _, name, fc in folders)}")

    # Enumerate and download files per folder
    all_files = []
    for fidx, fname, fc in folders:
        if fc == 0:
            continue
        files, sizes, exact = list_folder(dev, fidx)
        folder_files = []
        for friendly, idx, title, artist, timestamp in files:
            blocks = sizes.get(idx)
            sz = exact.get(idx, 0)
            folder_files.append((friendly, idx, blocks, sz, title, artist, timestamp))
            all_files.append((fname, friendly, sz))

        # Download this folder's files immediately while device state is correct
        for friendly, idx, blocks, sz, title, artist, timestamp in folder_files:
            if not blocks:
                print(f"Skipping {friendly} (no size info)"); continue
            relist_folder(dev, fidx)
            download(dev, fname, friendly, fidx, idx, blocks, sz, title, artist, timestamp)

    if not all_files:
        print("No recordings found."); return

    usb.util.dispose_resources(dev)
    print("Done!")

if __name__ == '__main__':
    main()
