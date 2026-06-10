#!/usr/bin/env python3
"""
Watchman UF2 Flasher
====================
GUI tool to generate and flash Watchman Radio firmware onto an nRF52840 dev
board (e.g. the Valve Index dongle or Seeed XIAO nRF52840) via UF2 file
transfer.

Based on:  https://github.com/yourname/watchman-uf2
Requires:  Python 3.8+, tkinter (stdlib)
"""

import os
import sys
import shutil
import struct
import zipfile
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext  # ttk used for Notebook tabs
from pathlib import Path
from ctypes import windll


# Allow high-res window
windll.shcore.SetProcessDpiAwareness(1)

# UF2 constants
UF2_MAGIC_START0 = 0x0A324655  # "UF2\n"
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
FAMILY_NRF52840 = 0xADA52840
FLAG_FAMILYID = 0x00002000

# Firmware patch constants
# "Watchman Radio" in bytes
DEVICE_NAME_BYTES = bytes([
    0x57, 0x61, 0x74, 0x63, 0x68, 0x6D, 0x61, 0x6E,
    0x20, 0x52, 0x61, 0x64, 0x69, 0x6F
])
DEVICE_NAME_OFFSET = 0x162E0

BOOT_MODE_DYX_PATCH = (0x18391, 0xB9)  # DYX is always used

FW_APP_START = 0x26000  # firmware application start address
SD_APP_START = 0x01000  # softdevice start address
FW_ZIP_REL = r"drivers\indexhmd\resources\firmware\radio\gd_1558748372_dfu.zip"
FW_BIN_IN_ZIP = "temp_app_stamped.bin"
SD_BIN_IN_ZIP = "s140_nrf52_6.1.1_softdevice.bin"

DEFAULT_STEAMVR = r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR"
# When frozen with PyInstaller, sys.executable points to the .exe; otherwise use this script's location
SCRIPT_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "Viva Dongle Firmware"

# Configurable device labels for the dropdown
SUPPORTED_DRIVES = [
    "NICENANO",
    "NRF52BOOT",
    "XIAO SENSE",
    "XIAO NRF52",
    "FLOW_UF2"
]


# Core logic

def find_flow_uf2_drive(target_label: str) -> Path | None:
    # Return the path of the selected mass-storage drive, or None.
    if sys.platform == "win32":
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                letter = chr(ord('A') + i) + ":\\"
                try:
                    vol_buf = ctypes.create_unicode_buffer(256)
                    ctypes.windll.kernel32.GetVolumeInformationW(
                        letter, vol_buf, 256,
                        None, None, None, None, 0
                    )
                    if vol_buf.value == target_label:
                        return Path(letter)
                except Exception:
                    pass
    elif sys.platform == "darwin":
        p = Path("/Volumes") / target_label
        if p.exists():
            return p
    else:  # Linux
        for mp in [Path("/media") / os.environ.get("USER", "user"),
                   Path("/mnt"), Path("/media")]:
            candidate = mp / target_label
            if candidate.exists():
                return candidate
    return None


def bin_to_uf2(bin_data: bytes, app_start: int) -> bytes:
    # Convert raw binary firmware bytes to UF2 format.
    num_blocks = (len(bin_data) + 0xFF) >> 8
    out = bytearray()
    for blockno in range(num_blocks):
        ptr = 0x100 * blockno
        chunk = bin_data[ptr: ptr + 0x100]
        # Pad chunk to 256 bytes
        chunk = chunk.ljust(256, b'\x00')
        flags = FLAG_FAMILYID
        header = struct.pack(
            "<IIIIIIII",
            UF2_MAGIC_START0,
            UF2_MAGIC_START1,
            flags,
            ptr + app_start,
            256,
            blockno,
            num_blocks,
            FAMILY_NRF52840,
        )
        padding_len = 0x200 - 32 - 256 - 4
        block = header + chunk + (b'\x00' * padding_len) + struct.pack("<I", UF2_MAGIC_END)
        assert len(block) == 512, f"Block size mismatch: {len(block)}"
        out += block
    return bytes(out)


def generate_firmware(steamvr_dir: str, out_dir: str,
                      log: callable) -> Path:
    steamvr_path = Path(steamvr_dir)
    zip_path = steamvr_path / FW_ZIP_REL
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    log(f"SteamVR dir : {steamvr_path}")
    log(f"Firmware zip: {zip_path}")

    if not zip_path.exists():
        raise FileNotFoundError(f"Firmware zip not found:\n{zip_path}")

    # Extract the application binary
    log("Extracting firmware binary from zip…")
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        # The binary may live at the root or inside a subdir
        bin_member = next((m for m in members if m.endswith(FW_BIN_IN_ZIP)), None)
        if bin_member is None:
            raise FileNotFoundError(
                f"'{FW_BIN_IN_ZIP}' not found inside zip.\nContents: {members}"
            )
        raw = zf.read(bin_member)

    log(f"Extracted {len(raw):,} bytes")

    # Convert to mutable bytearray for patching
    data = bytearray(raw)

    # Patch 1: device name → "Watchman Radio"
    log("Patching device name…")
    data[DEVICE_NAME_OFFSET: DEVICE_NAME_OFFSET + len(DEVICE_NAME_BYTES)] = DEVICE_NAME_BYTES

    # Patch 2: boot mode (always DYX)
    offset, value = BOOT_MODE_DYX_PATCH
    log(f"Patching boot mode DYX (offset 0x{offset:X} ← 0x{value:02X})…")
    data[offset] = value

    # Convert to UF2
    log("Converting .bin → .uf2…")
    uf2_data = bin_to_uf2(bytes(data), FW_APP_START)
    log(f"{len(uf2_data) // 512:,} UF2 blocks generated")

    uf2_out = out_path / "temp_app_stamped_dyx.uf2"
    bin_out = out_path / "temp_app_stamped_dyx.bin"

    uf2_out.write_bytes(uf2_data)
    bin_out.write_bytes(bytes(data))

    log(f"Saved: {uf2_out}")
    log(f"Saved: {bin_out}")
    return uf2_out


def generate_softdevice(steamvr_dir: str, out_dir: str, log: callable) -> Path:
    """Extract the softdevice binary and convert to UF2."""
    steamvr_path = Path(steamvr_dir)
    zip_path = steamvr_path / FW_ZIP_REL
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    log(f"Firmware zip: {zip_path}")

    if not zip_path.exists():
        raise FileNotFoundError(f"Firmware zip not found:\n{zip_path}")

    log("Extracting softdevice binary…")
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        sd_member = next((m for m in members if m.endswith(SD_BIN_IN_ZIP)), None)
        if sd_member is None:
            raise FileNotFoundError(
                f"'{SD_BIN_IN_ZIP}' not found inside zip.\nContents: {members}"
            )
        raw = zf.read(sd_member)

    log(f"Extracted {len(raw):,} bytes")
    log("Converting .bin → .uf2…")
    uf2_data = bin_to_uf2(raw, SD_APP_START)
    log(f"{len(uf2_data) // 512:,} UF2 blocks generated")

    uf2_out = out_path / "s140_nrf52_6.1.1_softdevice.uf2"
    uf2_out.write_bytes(uf2_data)
    log(f"Saved: {uf2_out}")
    return uf2_out


def flash_uf2(uf2_path: Path, drive: Path, log: callable):
    # Copy the .uf2 file to the target drive.
    dest = drive / uf2_path.name
    log(f"Flashing {uf2_path.name} → {drive}…")
    shutil.copy2(uf2_path, dest)
    log("Flash complete! Unplug and reconnect the dongle to reboot into Watchman firmware.")


# GUI

DARK_BG = "#000000"
PANEL_BG = "#2a2a3e"
ACCENT = "#925cff"
ACCENT2 = "#ff7d8e"
SUCCESS = "#a6e3a1"
WARN = "#f38ba8"
TEXT = "#cdd6f4"
SUBTEXT = "#6c7086"
BORDER = "#45475a"
BUTTON_FG = "#1e1e2e"
FONT_BODY = ("Segoe UI", 10)
FONT_MONO = ("Cascadia Code", 9) if sys.platform == "win32" else ("Menlo", 9)
FONT_HEAD = ("Segoe UI", 13, "bold")
FONT_LABEL = ("Segoe UI", 9)


class WatchmanFlasher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Viva Dongle Flasher")
        self.configure(bg=DARK_BG)
        self.resizable(False, False)
        self.minsize(620, 680)

        self._uf2_path: Path | None = None
        self._drive: Path | None = None

        # Add tracking for the currently selected device label
        self.device_var = tk.StringVar(value=SUPPORTED_DRIVES[0])

        self._set_icon()
        self._build_ui()
        self._poll_drive()

    # Set window icon
    def _set_icon(self):
        ico = Path(__file__).resolve().parent / "VivaDongleFlasher.ico"
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

    # UI construction

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=DARK_BG, pady=14)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="Viva Dongle Flasher",
                 font=FONT_HEAD, bg=DARK_BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="v1.0  |  XR Studios ©2026",
                 font=FONT_LABEL, bg=DARK_BG, fg=SUBTEXT).pack(side="right", padx=12)

        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x", padx=20)

        body = tk.Frame(self, bg=DARK_BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)

        # Notebook
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TNotebook", background=DARK_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL_BG, foreground=SUBTEXT,
                        padding=[14, 6], font=FONT_BODY)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", BUTTON_FG)])

        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)

        fw_tab = tk.Frame(nb, bg=DARK_BG)
        sd_tab = tk.Frame(nb, bg=DARK_BG)
        nb.add(fw_tab, text="  Firmware  ")
        nb.add(sd_tab, text="  Softdevice  ")

        self._build_fw_tab(fw_tab)
        self._build_sd_tab(sd_tab)

        # Log
        log_frame = tk.LabelFrame(body, text=" Log ", bg=DARK_BG, fg=SUBTEXT,
                                  font=FONT_LABEL, bd=1, relief="flat",
                                  highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_box = scrolledtext.ScrolledText(
            log_frame, height=10, font=FONT_MONO,
            bg="#11111b", fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, padx=8, pady=6,
            state="disabled", wrap="word"
        )
        self.log_box.pack(fill="both", expand=True)

        # Status bar
        bar = tk.Frame(self, bg=PANEL_BG, pady=6)
        bar.pack(fill="x")

        # Drive selector dropdown
        device_menu = ttk.Combobox(
            bar,
            textvariable=self.device_var,
            values=SUPPORTED_DRIVES,
            state="readonly",
            width=14,
            font=FONT_LABEL
        )
        device_menu.pack(side="left", padx=(16, 8))
        device_menu.bind("<<ComboboxSelected>>", lambda e: self._refresh_drive())

        self.drive_label = tk.Label(bar, text="● Drive not detected",
                                    font=FONT_LABEL, bg=PANEL_BG, fg=WARN)
        self.drive_label.pack(side="left", padx=8)

        tk.Button(bar, text="🔍 Refresh", font=FONT_LABEL,
                  bg=BORDER, fg=TEXT, relief="flat", bd=0,
                  activebackground=ACCENT, activeforeground=BUTTON_FG,
                  command=self._refresh_drive, cursor="hand2",
                  padx=8, pady=2).pack(side="right", padx=12)

    def _build_fw_tab(self, parent):
        pad = dict(padx=16, pady=6)
        frm = tk.Frame(parent, bg=DARK_BG)
        frm.pack(fill="both", expand=True, **pad)

        # SteamVR dir
        tk.Label(frm, text="SteamVR directory", font=FONT_LABEL,
                 bg=DARK_BG, fg=SUBTEXT).grid(row=0, column=0, sticky="w", pady=(8, 2))
        row1 = tk.Frame(frm, bg=DARK_BG)
        row1.grid(row=1, column=0, sticky="ew")
        frm.columnconfigure(0, weight=1)
        self.steamvr_var = tk.StringVar(value=DEFAULT_STEAMVR)
        self._entry(row1, self.steamvr_var).pack(side="left", fill="x", expand=True)
        self._btn(row1, "Browse", self._browse_steamvr).pack(side="left", padx=(6, 0))

        # Output dir
        tk.Label(frm, text="Output directory", font=FONT_LABEL,
                 bg=DARK_BG, fg=SUBTEXT).grid(row=2, column=0, sticky="w", pady=(10, 2))
        row2 = tk.Frame(frm, bg=DARK_BG)
        row2.grid(row=3, column=0, sticky="ew")
        self.outdir_var = tk.StringVar(value=str(OUTPUT_DIR))
        self._entry(row2, self.outdir_var).pack(side="left", fill="x", expand=True)
        self._btn(row2, "Browse", self._browse_outdir).pack(side="left", padx=(6, 0))

        # Buttons
        btn_row = tk.Frame(frm, bg=DARK_BG)
        btn_row.grid(row=6, column=0, sticky="ew", pady=(14, 4))
        self._primary_btn(btn_row, "Generate UF2",
                          self._run_generate_fw).pack(side="left")
        self.fw_flash_btn = self._primary_btn(
            btn_row, "⚡  Flash to Device",
            self._run_flash_fw, accent=ACCENT2
        )
        self.fw_flash_btn.pack(side="left", padx=(10, 0))
        self.fw_flash_btn.configure(state="disabled")

    def _build_sd_tab(self, parent):
        pad = dict(padx=16, pady=6)
        frm = tk.Frame(parent, bg=DARK_BG)
        frm.pack(fill="both", expand=True, **pad)

        tk.Label(frm, text=(
            "Flash s140 v6.1.1 SoftDevice\n"
            "Required for boards shipping with newer SoftDevices\n"
            "(e.g. Seeed XIAO nRF52840 with v7.3.0)"
        ), font=FONT_BODY, bg=DARK_BG, fg=SUBTEXT, justify="left"
                 ).grid(row=0, column=0, sticky="w", pady=(10, 10))

        tk.Label(frm, text="SteamVR directory", font=FONT_LABEL,
                 bg=DARK_BG, fg=SUBTEXT).grid(row=1, column=0, sticky="w", pady=(4, 2))
        row1 = tk.Frame(frm, bg=DARK_BG)
        row1.grid(row=2, column=0, sticky="ew")
        frm.columnconfigure(0, weight=1)
        self.sd_steamvr_var = tk.StringVar(value=DEFAULT_STEAMVR)
        self._entry(row1, self.sd_steamvr_var).pack(side="left", fill="x", expand=True)
        self._btn(row1, "Browse", lambda: self._browse_dir(self.sd_steamvr_var)
                  ).pack(side="left", padx=(6, 0))

        tk.Label(frm, text="Output directory", font=FONT_LABEL,
                 bg=DARK_BG, fg=SUBTEXT).grid(row=3, column=0, sticky="w", pady=(10, 2))
        row2 = tk.Frame(frm, bg=DARK_BG)
        row2.grid(row=4, column=0, sticky="ew")
        self.sd_outdir_var = tk.StringVar(value=str(OUTPUT_DIR))
        self._entry(row2, self.sd_outdir_var).pack(side="left", fill="x", expand=True)
        self._btn(row2, "Browse", lambda: self._browse_dir(self.sd_outdir_var)
                  ).pack(side="left", padx=(6, 0))

        btn_row = tk.Frame(frm, bg=DARK_BG)
        btn_row.grid(row=5, column=0, sticky="ew", pady=(16, 4))
        self._primary_btn(btn_row, "Generate SoftDevice UF2",
                          self._run_generate_sd).pack(side="left")
        self.sd_flash_btn = self._primary_btn(
            btn_row, "⚡  Flash to Device",
            self._run_flash_sd, accent=ACCENT2
        )
        self.sd_flash_btn.pack(side="left", padx=(10, 0))
        self.sd_flash_btn.configure(state="disabled")

    # Widget helpers

    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, font=FONT_BODY,
                        bg=PANEL_BG, fg=TEXT, insertbackground=TEXT,
                        relief="flat", bd=0, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=ACCENT)

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, font=FONT_LABEL,
                         bg=BORDER, fg=TEXT, relief="flat", bd=0,
                         activebackground=ACCENT, activeforeground=BUTTON_FG,
                         command=cmd, cursor="hand2", padx=8, pady=4)

    def _primary_btn(self, parent, text, cmd, accent=ACCENT):
        return tk.Button(parent, text=text, font=("Segoe UI", 10, "bold"),
                         bg=accent, fg=BUTTON_FG, relief="flat", bd=0,
                         activebackground=TEXT, activeforeground=BUTTON_FG,
                         command=cmd, cursor="hand2", padx=14, pady=6)

    # Logging

    def log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.update_idletasks()

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # Drive detection

    def _poll_drive(self):
        self._refresh_drive()
        self.after(3000, self._poll_drive)

    def _refresh_drive(self):
        target = self.device_var.get()
        self._drive = find_flow_uf2_drive(target)
        if self._drive:
            self.drive_label.configure(
                text=f"● {target} detected at {self._drive}",
                fg=SUCCESS
            )
        else:
            self.drive_label.configure(
                text=f"● {target} not detected",
                fg=WARN
            )
        # Enable/disable flash buttons
        self._update_flash_buttons()

    def _update_flash_buttons(self):
        can_flash_fw = self._uf2_path is not None and self._drive is not None
        can_flash_sd = hasattr(self, "_sd_uf2_path") and \
                       self._sd_uf2_path is not None and self._drive is not None
        self.fw_flash_btn.configure(state="normal" if can_flash_fw else "disabled")
        self.sd_flash_btn.configure(state="normal" if can_flash_sd else "disabled")

    # Browse helpers

    def _browse_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or "/")
        if d:
            var.set(d)

    def _browse_steamvr(self):
        self._browse_dir(self.steamvr_var)

    def _browse_outdir(self):
        self._browse_dir(self.outdir_var)

    # Firmware workflow

    def _run_generate_fw(self):
        self._clear_log()
        self._uf2_path = None
        self._update_flash_buttons()

        def worker():
            try:
                path = generate_firmware(
                    steamvr_dir=self.steamvr_var.get(),
                    out_dir=self.outdir_var.get(),
                    log=self.log,
                )
                self._uf2_path = path
                self.log(f"\nGeneration successful!\n{path}")
            except Exception as e:
                self.log(f"\n❌  Error: {e}")
            finally:
                self.after(0, self._update_flash_buttons)

        threading.Thread(target=worker, daemon=True).start()

    def _run_flash_fw(self):
        if not self._uf2_path or not self._drive:
            return
        if not messagebox.askyesno("Confirm Flash",
                                   f"Flash '{self._uf2_path.name}' to {self._drive}?\n\n"
                                   "Once flash is complete, unplug and reconnect device to boot into Viva Dongle firmware."):
            return

        def worker():
            try:
                flash_uf2(self._uf2_path, self._drive, self.log)
            except Exception as e:
                self.log(f"\n❌  Flash error: {e}")
            finally:
                self.after(0, self._refresh_drive)

        threading.Thread(target=worker, daemon=True).start()

    # Softdevice workflow

    def _run_generate_sd(self):
        self._clear_log()
        self._sd_uf2_path = None
        self._update_flash_buttons()

        def worker():
            try:
                path = generate_softdevice(
                    steamvr_dir=self.sd_steamvr_var.get(),
                    out_dir=self.sd_outdir_var.get(),
                    log=self.log,
                )
                self._sd_uf2_path = path
                self.log(f"\nSoftdevice UF2 ready!\n{path}")
            except Exception as e:
                self.log(f"\n❌  Error: {e}")
            finally:
                self.after(0, self._update_flash_buttons)

        threading.Thread(target=worker, daemon=True).start()

    def _run_flash_sd(self):
        if not self._sd_uf2_path or not self._drive:
            return
        if not messagebox.askyesno("Confirm Flash",
                                   f"Flash SoftDevice '{self._sd_uf2_path.name}' to {self._drive}?\n\n"
                                   "Flash the SoftDevice BEFORE flashing the application firmware."):
            return

        def worker():
            try:
                flash_uf2(self._sd_uf2_path, self._drive, self.log)
            except Exception as e:
                self.log(f"\n❌  Flash error: {e}")
            finally:
                self.after(0, self._refresh_drive)

        threading.Thread(target=worker, daemon=True).start()


#

if __name__ == "__main__":
    app = WatchmanFlasher()
    app.mainloop()