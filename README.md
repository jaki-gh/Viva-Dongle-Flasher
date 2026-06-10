<img width="100" height="100" alt="VivaDongleFlasher" src="https://github.com/user-attachments/assets/6bb09567-c0e1-4ca0-b34e-fd119bff12d5" />



# About
**This GUI tool written in Python allows you to easily make your own $5 SteamVR Watchman dongles!! Turn cheap NRF52840 devices into fully working SteamVR dongles with an easy to use GUI app for Windows and Python!**

### Official guide for making your own Viva Dongles available at https://www.xrstudios.me/guides/vivadongle

## Features

- Firmware and Softdevice flashing supported
- Automatic directory filling
- Supports multiple types of NRF52840 devices
- Organized json file management
- Clean and easy to use GUI.

## How to use

1. Download either the .exe or the .py app.
2. Make sure you have SteamVR installed on your PC.
3. Plug in your NRF52840 device to your PC. You should see it appear in file explorer as a USB drive (if not, short the GND and Reset pins twice quickly).
4. Open the downloaded Viva Dongle Flasher and select either Firmware or Softdevice for your device.
5. At the bottom of the app there is a dropdown menu that lets you select your device type; make sure that the device name in file explorer matches this.
6. Click "**Generate UF2**".
7. Once UF2 firmware is generated, click "**Flash to Device**" and wait for the firmware to copy to your NRF52840.
8. Unplug and reconnect your NRF52840 to your laptop, and it should now work as a SteamVR Watchman dongle!

If you want to control what controller or tracker is connected to which dongle, I recommend you check out the watchman-pairing-assistant over on GitHub: https://github.com/EinDev/watchman-pairing-assistant

## Credit

This code is based off of ugokutennp's firmware editing tool (https://github.com/ugokutennp/watchman-uf2).
Ugokutennp's work made this project possible so huge thanks to them! Thay also made the watchman-pairing-assistant linked above.

## Donation page for poorness reasons
 [![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/Q5Q6TOTSN)
