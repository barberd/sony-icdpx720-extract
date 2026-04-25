# Sony ICD-PX720 File Extractor

Extract MP3 recordings from a Sony ICD-PX720 voice recorder directly via USB — no Sony software required.

Sony never released drivers for this device beyond Windows 7. This tool reverse-engineers the proprietary USB protocol to download recordings on a modern OS.

## Requirements

- Python 3.6+
- [pyusb](https://pypi.org/project/pyusb/)

```
pip install pyusb
```

### Windows

The device needs a generic USB driver since Sony never made one for modern Windows:

1. Download [Zadig](https://zadig.akeo.ie)
2. Plug in the recorder
3. Run Zadig, select **Sony IC Recorder (PX)** from the dropdown
4. Choose **WinUSB** as the driver
5. Click **Install Driver** (one-time setup)

### Linux

No extra setup needed. If you get permission errors, add a udev rule:

```
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="054c", ATTR{idProduct}=="0387", MODE="0666"' | sudo tee /etc/udev/rules.d/99-sony-icdpx720.rules
sudo udevadm control --reload-rules
```

Then unplug and replug the recorder.

### macOS

Install libusb via Homebrew:

```
brew install libusb
pip install pyusb
```

## Usage

Plug in the recorder and run:

```
python sony-icdpx720-extract.py
```

MP3 files are saved to the current directory.

## How it works

The ICD-PX720 uses a vendor-specific USB protocol (device class 0xFF) instead of USB Mass Storage. The protocol was reverse-engineered by capturing USB traffic between the recorder and Sony's Digital Voice Editor software running in a Windows XP VM.

The protocol uses vendor control transfers on endpoint 0 for commands and bulk transfers on endpoint 0x81 for data:

1. **Init** — query device info and capabilities
2. **Select folder** — select the "all files" view
3. **List files** — enumerate recordings and read a flash address table containing file sizes
4. **Download** — read audio data in 1000-block (1MB) chunks with overlap handling

Each recording is stored as raw MP3 on the device's internal flash.

## Known quirks

- File `090101_008` (a system-generated entry with no matching flash table record) is skipped during extraction.

## Compatibility

Tested with the Sony ICD-PX720. May work with other ICD-PX series recorders that use the same USB protocol. Maybe try adjusting the code to work with the USB vendor and device ID for your recorder.

## GenAI Usage

Protocol reverse-engineering and code developed with assistance from [Kiro](https://kiro.dev).

## License

GPLv3
