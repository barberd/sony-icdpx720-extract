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

    # Set preference menu — selects folder "F" (all files view)
    # This is CIcdComm4::SetPreferenceMenu with a _SETPREFERENCEMENUFORVOCE struct.
    # The struct is normally read from the device, timestamp updated, and written back.
    # Byte at offset 0x25 (0x46='F') controls the folder view.
    send(dev, '00e000080046abab000000000a0004500000001a000000000001000000000001ff00ff0000460000010107ea0413101e0900')
    poll_wait(dev); read(dev, 24); poll(dev)

    # List files
    for _ in range(3):
        send(dev, M+b'\x09\x20\x00\xff'+b'\x00'*8); poll_wait(dev); read(dev, 356); poll(dev)
    send(dev, M+b'\x09\x10\x01\xff'+b'\x00'*8); poll_wait(dev); read(dev, 24)

    files, flash_table = [], None
    file_idx = 0
    for _ in range(500):
        try: pkt = bytes(dev.read(EP_IN, 512, T))
        except usb.core.USBTimeoutError: break
        if pkt[:4] == b'\xff\xff\x90\x00':
            name = pkt[4:20].split(b'\x00')[0].decode('ascii', errors='replace')
            file_idx += 1
            if name and not name.startswith('Z'):
                files.append((name, file_idx))
        elif flash_table is None and len(pkt) >= 12 and pkt[8:12] == b'\x80\x00\x00\x00':
            flash_table = pkt
    poll_wait(dev); read(dev, 24); poll(dev)

    # Parse flash table — entries map 1:1 with file listing order
    # Multi-segment files use consecutive entries; last segment has bit 31 set in end_high
    sizes, exact = {}, {}
    if flash_table:
        n, total = 0, 0
        for i in range(0, len(flash_table)-15, 16):
            v = struct.unpack('>IIII', flash_table[i:i+16])
            if v[0] == 0xffffffff: break
            s = (v[0]<<32)|v[1]; e = ((v[2]&0x7fffffff)<<32)|v[3]
            total += e - s + 1
            if v[2] & 0x80000000:  # last segment
                n += 1
                sizes[n] = math.ceil(total / 1024)
                exact[n] = total
                total = 0

    if not files:
        print("No recordings found."); return

    print(f"Found {len(files)} recording(s):")
    for name, idx in files:
        sz = exact.get(idx, 0)
        print(f"  {name} ({sz:,} bytes)" if sz else f"  {name}")

    # Download
    for name, idx in files:
        blocks = sizes.get(idx)
        if not blocks:
            print(f"Skipping {name} (no size info)"); continue

        print(f"Downloading {name}...", flush=True)
        data = bytearray()
        offset, first = 1, True

        while offset < blocks:
            end = min(offset + CHUNK - (1 if first else 0), blocks)
            payload = struct.pack('>13H', 1, idx, 0, 0, offset, 0, end,
                0xffff, 0xffff, 0, 0, 0xf, 0xa000 if first else 0xa400)
            send(dev, M+b'\x11\xff'+payload)
            poll_wait(dev); read(dev, 40)

            block = bytearray()
            for p in range((end-offset+1)*2):
                try: block.extend(dev.read(EP_IN, 512, 2000))
                except usb.core.USBTimeoutError: break
            poll_wait(dev); read(dev, 40); poll(dev)

            data.extend(block if first else block[1024:])
            first = False
            offset = end
            sys.stdout.write(f"\r  {len(data)*100//(blocks*1024)}%"); sys.stdout.flush()

        sz = exact.get(idx, len(data))
        with open(f"{name}.mp3", 'wb') as f:
            f.write(data[:sz])
        print(f"\r  Saved {name}.mp3 ({sz:,} bytes)")

    usb.util.dispose_resources(dev)
    print("Done!")

if __name__ == '__main__':
    main()
