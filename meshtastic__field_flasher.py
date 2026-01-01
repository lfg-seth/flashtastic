import json
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font
import ctypes
from pathlib import Path


# -----------------------------
# Constants / Persistence
# -----------------------------
SETURL_VALUE = r"https://meshtastic.org/e/#CjESIIBUPqT60FhSuxvl2JP2kE7uVHX5ygCrMJ8y8pLb5vXOGgVTTk9SUigBMAE6AgggEhgIARj6ASALKAU4AUAFSAFQHlgjaAHIBgE"

APP_DIR = Path(__file__).parent.resolve()
STATE_PATH = APP_DIR / "state.json"

KEYPAD_WIDTH = 640  # 1/3 of 1920px


# -----------------------------
# Modes
# -----------------------------
MODE_DEFS = {
    "RAK4631": {
        "label": "RAK4631 Router (UF2 Drive)",
        "flash_method": "uf2_drive",
        "firmware_default": "rak4631.uf2",
        "gps_mode": "fixed",   # fixed position lat/lon required
        "role": "ROUTER",
    },
    "Heltec V3": {
        "label": "Heltec V3 Client (No GPS)",
        "flash_method": "esptool",
        "firmware_default": "heltec_v3_client.bin",
        "gps_mode": "none",
        "role": "CLIENT",
    },
    "Heltec V4": {
        "label": "Heltec V4 Client (GPS)",
        "flash_method": "esptool",
        "firmware_default": "heltec_v4_gps.bin",
        "gps_mode": "gps",
        "role": "CLIENT",
    },
}

MODE_KEYS = list(MODE_DEFS.keys())


# -----------------------------
# UF2 Drive detection (Windows)
# -----------------------------
DRIVE_REMOVABLE = 2
GetDriveTypeW = ctypes.windll.kernel32.GetDriveTypeW


def list_removable_drives_windows():
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            drive = f"{chr(65 + i)}:\\"
            dtype = GetDriveTypeW(ctypes.c_wchar_p(drive))
            if dtype == DRIVE_REMOVABLE:
                drives.append(drive)
    return drives


def detect_uf2_drives():
    drives = []
    for d in list_removable_drives_windows():
        try:
            if (
                os.path.exists(os.path.join(d, "INFO_UF2.TXT"))
                or os.path.exists(os.path.join(d, "UF2INFO.TXT"))
            ):
                drives.append(d)
        except Exception:
            pass
    return drives


# -----------------------------
# Helpers
# -----------------------------
def run_cmd_stream(cmd, log_fn):
    """
    Stream subprocess output in near-real-time, including carriage-return progress
    (common for esptool progress bars).
    """
    if isinstance(cmd, list):
        log_fn(f"$ {' '.join(cmd)}\n")
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    else:
        log_fn(f"$ {cmd}\n")
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            shell=True
        )

    # Read raw bytes so we can handle \r progress updates
    out = p.stdout
    if out is None:
        rc = p.wait()
        if rc != 0:
            raise RuntimeError(f"Command failed (exit {rc})")
        return

    while True:
        b = out.read(1)
        if not b:
            break
        try:
            ch = b.decode("utf-8", errors="replace")
        except Exception:
            ch = str(b)

        if ch == "\r":
            # esptool uses carriage return to update progress on same line;
            # in a Text widget we can't "overwrite", so just start a new line.
            log_fn("\n")
        else:
            log_fn(ch)

    rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"Command failed (exit {rc})")


def copy_uf2_to_drive(uf2_path, drive_root, log_fn):
    if not os.path.isfile(uf2_path):
        raise RuntimeError("Firmware file not found.")
    if not uf2_path.lower().endswith(".uf2"):
        raise RuntimeError("UF2 flashing requires a .uf2 file.")
    if not os.path.isdir(drive_root):
        raise RuntimeError(f"Drive not found: {drive_root}")

    dest = os.path.join(drive_root, os.path.basename(uf2_path))
    log_fn(f"Copying UF2 to {dest}\n")
    shutil.copyfile(uf2_path, dest)
    log_fn("Copy complete. Device may reboot.\n")


def wait_seconds(sec, log_fn):
    for i in range(sec, 0, -1):
        log_fn(f"Waiting for reboot... {i}s\n")
        time.sleep(1)
    log_fn("\n")


# -----------------------------
# App
# -----------------------------
class App:
    def __init__(self, root):
        self.root = root

        # Fonts
        self.touch_font = font.Font(family="Segoe UI", size=18)
        self.touch_font_bold = font.Font(family="Segoe UI", size=18, weight="bold")
        self.log_font = font.Font(family="Consolas", size=12)

        # Mode button colors
        self.mode_bg_normal = "#2b2b2b"
        self.mode_fg_normal = "#ffffff"
        self.mode_bg_selected = "#2e7d32"   # green
        self.mode_fg_selected = "#ffffff"

        root.title("Meshtastic Field Flasher")
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))

        # Styles
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Touch.TButton", font=self.touch_font_bold, padding=(8, 16))
        style.configure("Keypad.TButton", font=self.touch_font_bold, padding=(10, 18))

        # Vars
        self.mode = tk.StringVar(value=MODE_KEYS[0])

        self.firmware_path = tk.StringVar(value="")
        self.uf2_drive = tk.StringVar(value="")

        self.owner_letters = tk.StringVar(value="SNORR TEST")
        self.owner_num = tk.StringVar(value="01")
        self.owner_short_letters = tk.StringVar(value="MT")
        self.owner_short_num = tk.StringVar(value="01")

        self.lat = tk.StringVar(value="37.8651")
        self.lon = tk.StringVar(value="-119.5383")

        # Focus/tab + persistence
        self.active_widget = None
        self.field_widgets = []
        self._save_after_id = None
        self._state = self._load_state()

        # Build UI
        self._build_ui()
        self._wire_traces()

        # Restore last mode and mode state
        last_mode = self._state.get("last_mode")
        if last_mode in MODE_DEFS:
            self.mode.set(last_mode)

        self.apply_mode()

    # -----------------------------
    # Persistence
    # -----------------------------
    def _load_state(self):
        if STATE_PATH.exists():
            try:
                return json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _schedule_save(self):
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.root.after(300, self._save_state)

    def _save_state(self):
        self._save_after_id = None
        st = self._state if isinstance(self._state, dict) else {}
        mode = self.mode.get()
        st["last_mode"] = mode
        st.setdefault("modes", {})
        st["modes"][mode] = {
            "firmware_path": self.firmware_path.get(),
            "uf2_drive": self.uf2_drive.get(),
            "owner_letters": self.owner_letters.get(),
            "owner_num": self.owner_num.get(),
            "owner_short_letters": self.owner_short_letters.get(),
            "owner_short_num": self.owner_short_num.get(),
            "lat": self.lat.get(),
            "lon": self.lon.get(),
        }
        try:
            STATE_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")
        except Exception:
            pass

    # -----------------------------
    # Focus / tab helpers
    # -----------------------------
    def _on_widget_focus(self, w):
        self.active_widget = w
        try:
            if isinstance(w, tk.Entry):
                w.icursor("end")
        except Exception:
            pass

    def _register_field(self, w):
        self.field_widgets.append(w)
        try:
            w.bind("<FocusIn>", lambda ev, ww=w: self._on_widget_focus(ww))
        except Exception:
            pass
        return w

    def _focus_next(self):
        if not self.field_widgets:
            return
        w = self.root.focus_get()
        try:
            idx = self.field_widgets.index(w)
        except Exception:
            idx = -1
        self.field_widgets[(idx + 1) % len(self.field_widgets)].focus_set()

    def _focus_prev(self):
        if not self.field_widgets:
            return
        w = self.root.focus_get()
        try:
            idx = self.field_widgets.index(w)
        except Exception:
            idx = 0
        self.field_widgets[(idx - 1) % len(self.field_widgets)].focus_set()

    # -----------------------------
    # Keypad
    # -----------------------------
    def _keypad_insert(self, ch):
        w = self.active_widget
        if w is None or not isinstance(w, tk.Entry):
            return

        if ch == "⌫":
            try:
                sel_first = w.index("sel.first")
                sel_last = w.index("sel.last")
                w.delete(sel_first, sel_last)
            except Exception:
                idx = w.index(tk.INSERT)
                if idx > 0:
                    w.delete(idx - 1, idx)
            return

        if ch == "CLR":
            w.delete(0, "end")
            return

        if ch == "±":
            s = w.get()
            if s.startswith("-"):
                w.delete(0, 1)
            else:
                w.insert(0, "-")
            return

        w.insert(tk.INSERT, ch)

    def _increment_owner_number(self):
        def inc(var: tk.StringVar):
            s = var.get().strip()
            width = len(s) if s.isdigit() and len(s) > 0 else 0
            try:
                n = int(s) if s else 0
            except ValueError:
                n = 0
                width = 0
            n += 1
            var.set(str(n).zfill(width) if width > 0 else str(n))

        inc(self.owner_num)
        inc(self.owner_short_num)
        self._schedule_save()

    # -----------------------------
    # UI construction
    # -----------------------------
    def touch_entry(self, parent, textvariable, width):
        e = tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            font=self.touch_font,
            relief="solid",
            bd=1
        )
        e.bind("<Button-1>", lambda ev: e.focus_set())
        e.bind("<FocusIn>", lambda ev, w=e: self._on_widget_focus(w))
        return e

    def _build_keypad(self, parent):
        kp = ttk.Frame(parent, padding=(10, 8))

        keys = [
            ["7", "8", "9"],
            ["4", "5", "6"],
            ["1", "2", "3"],
            ["0", ".", "-"],
            ["⌫", "CLR", "±"],
        ]

        for r, row in enumerate(keys):
            for c, k in enumerate(row):
                ttk.Button(
                    kp,
                    text=k,
                    style="Keypad.TButton",
                    command=lambda kk=k: self._keypad_insert(kk),
                    width=3
                ).grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

        nav = ttk.Frame(kp)
        nav.grid(row=len(keys), column=0, columnspan=3, pady=(10, 0), sticky="we")
        nav.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(nav, text="Prev", style="Keypad.TButton", command=self._focus_prev).grid(
            row=0, column=0, padx=6, pady=6, sticky="we"
        )
        ttk.Button(nav, text="Next", style="Keypad.TButton", command=self._focus_next).grid(
            row=0, column=1, padx=6, pady=6, sticky="we"
        )
        ttk.Button(nav, text="++", style="Keypad.TButton", command=self._increment_owner_number).grid(
            row=0, column=2, padx=6, pady=6, sticky="we"
        )
        ttk.Button(nav, text="Exit", style="Keypad.TButton", command=self._exit_app).grid(
            row=1, column=0, columnspan=3, padx=6, pady=(6, 0), sticky="we"
        )

        for c in range(3):
            kp.columnconfigure(c, weight=1)
        for r in range(len(keys) + 2):
            kp.rowconfigure(r, weight=1)

        return kp

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=2)   # left 2/3
        outer.columnconfigure(1, weight=1)   # right 1/3
        outer.rowconfigure(0, weight=1)

        # Left panel
        self.left = ttk.Frame(outer)
        self.left.grid(row=0, column=0, sticky="nsew")
        self.left.columnconfigure(1, weight=1)

        # Right panel (keypad)
        self.keypad = self._build_keypad(outer)
        self.keypad.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self.keypad.configure(width=KEYPAD_WIDTH)
        self.keypad.grid_propagate(False)

        r = 0

        # Mode buttons row
        ttk.Label(self.left, text="Mode:", font=self.touch_font).grid(row=r, column=0, sticky="w")

        mode_row = ttk.Frame(self.left)
        mode_row.grid(row=r, column=1, columnspan=2, sticky="w", padx=10)
        mode_row.columnconfigure((0, 1, 2), weight=1)

        self.mode_buttons = {}

        def set_mode(m):
            self.mode.set(m)
            self.apply_mode()

        for i, key in enumerate(MODE_KEYS):
            b = tk.Button(
                mode_row,
                text=key,
                font=self.touch_font_bold,
                width=10,
                relief="raised",
                bd=2,
                command=lambda kk=key: set_mode(kk),
                padx=12,
                pady=12,
            )
            b.grid(row=0, column=i, padx=8, pady=4, sticky="we")
            self.mode_buttons[key] = b

        # Firmware row
        r += 1
        ttk.Label(self.left, text="Firmware:", font=self.touch_font).grid(row=r, column=0, sticky="w")
        self._register_field(self.touch_entry(self.left, self.firmware_path, width=55)).grid(
            row=r, column=1, sticky="we", padx=10
        )
        ttk.Button(self.left, text="Browse…", command=self.pick_firmware, style="Touch.TButton", width=10).grid(
            row=r, column=2, sticky="we"
        )

        # UF2 row frame (shown for UF2 modes)
        r += 1
        self.uf2_row = ttk.Frame(self.left)
        self.uf2_row.grid(row=r, column=0, columnspan=3, sticky="we", pady=(0, 0))
        self.uf2_row.columnconfigure(1, weight=1)

        ttk.Label(self.uf2_row, text="UF2 Drive:", font=self.touch_font).grid(row=0, column=0, sticky="w")
        self.drive_combo = ttk.Combobox(self.uf2_row, textvariable=self.uf2_drive, values=[], font=self.touch_font, width=10)
        self.drive_combo.grid(row=0, column=1, sticky="w", padx=10)
        self._register_field(self.drive_combo)
        ttk.Button(self.uf2_row, text="Refresh", command=self.refresh_drives, style="Touch.TButton", width=10).grid(
            row=0, column=2, sticky="we"
        )

        # Owner split
        r += 1
        ttk.Label(self.left, text="Owner:", font=self.touch_font).grid(row=r, column=0, sticky="w")
        owner_row = ttk.Frame(self.left)
        owner_row.grid(row=r, column=1, sticky="w", padx=10)
        self._register_field(self.touch_entry(owner_row, self.owner_letters, width=18)).pack(side="left", padx=(0, 10))
        ttk.Label(owner_row, text="#", font=self.touch_font).pack(side="left", padx=(0, 6))
        self._register_field(self.touch_entry(owner_row, self.owner_num, width=6)).pack(side="left")

        ttk.Label(self.left, text="Owner Short:", font=self.touch_font).grid(row=r, column=1, sticky="e")
        short_row = ttk.Frame(self.left)
        short_row.grid(row=r, column=2, sticky="w")
        self._register_field(self.touch_entry(short_row, self.owner_short_letters, width=6)).pack(side="left", padx=(0, 10))
        ttk.Label(short_row, text="#", font=self.touch_font).pack(side="left", padx=(0, 6))
        self._register_field(self.touch_entry(short_row, self.owner_short_num, width=6)).pack(side="left")

        # Lat/Lon
        r += 1
        self.lat_lbl = ttk.Label(self.left, text="Latitude:", font=self.touch_font)
        self.lat_lbl.grid(row=r, column=0, sticky="w")
        self.lat_entry = self._register_field(self.touch_entry(self.left, self.lat, width=16))
        self.lat_entry.grid(row=r, column=1, sticky="w", padx=10)

        self.lon_lbl = ttk.Label(self.left, text="Longitude:", font=self.touch_font)
        self.lon_lbl.grid(row=r, column=1, sticky="e")
        self.lon_entry = self._register_field(self.touch_entry(self.left, self.lon, width=16))
        self.lon_entry.grid(row=r, column=2, sticky="w")

        # Action buttons
        r += 1
        self.btns = ttk.Frame(self.left)
        self.btns.grid(row=r, column=0, columnspan=3, sticky="we", pady=(12, 8))

        self.flash_btn = ttk.Button(self.btns, text="Flash", command=self.flash_only, style="Touch.TButton", width=14)
        self.flash_btn.pack(side="left", padx=10, pady=6)

        self.configure_btn = ttk.Button(self.btns, text="Configure", command=self.configure_only, style="Touch.TButton", width=16)
        self.configure_btn.pack(side="left", padx=10, pady=6)

        self.erase_btn = ttk.Button(self.btns, text="Erase Flash", command=self.erase_flash, style="Touch.TButton", width=16)
        self.erase_btn.pack(side="left", padx=10, pady=6)

        self.clear_btn = ttk.Button(self.btns, text="Clear Log", command=self.clear_log, style="Touch.TButton", width=12)
        self.clear_btn.pack(side="left", padx=10, pady=6)

        # Log
        r += 1
        self.log = tk.Text(self.left, height=18, wrap="word", font=self.log_font)
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        self.left.rowconfigure(r, weight=1)

        # make rows breathe
        for i in range(r):
            self.left.rowconfigure(i, pad=10)

    def _wire_traces(self):
        for v in [
            self.firmware_path, self.uf2_drive,
            self.owner_letters, self.owner_num,
            self.owner_short_letters, self.owner_short_num,
            self.lat, self.lon
        ]:
            v.trace_add("write", lambda *_: self._schedule_save())

    # -----------------------------
    # Mode logic
    # -----------------------------
    def _update_mode_button_styles(self):
        selected = self.mode.get()
        for key, btn in self.mode_buttons.items():
            if key == selected:
                btn.configure(
                    bg=self.mode_bg_selected,
                    fg=self.mode_fg_selected,
                    activebackground=self.mode_bg_selected,
                    activeforeground=self.mode_fg_selected,
                    relief="sunken",
                )
            else:
                btn.configure(
                    bg=self.mode_bg_normal,
                    fg=self.mode_fg_normal,
                    activebackground=self.mode_bg_normal,
                    activeforeground=self.mode_fg_normal,
                    relief="raised",
                )

    def apply_mode(self):
        mode = self.mode.get()
        if mode not in MODE_DEFS:
            return
        d = MODE_DEFS[mode]

        self._update_mode_button_styles()

        saved = (self._state.get("modes") or {}).get(mode, {})

        self.firmware_path.set(saved.get("firmware_path") or d["firmware_default"])
        self.uf2_drive.set(saved.get("uf2_drive") or "")

        self.owner_letters.set(saved.get("owner_letters") or self.owner_letters.get())
        self.owner_num.set(saved.get("owner_num") or self.owner_num.get())
        self.owner_short_letters.set(saved.get("owner_short_letters") or self.owner_short_letters.get())
        self.owner_short_num.set(saved.get("owner_short_num") or self.owner_short_num.get())

        self.lat.set(saved.get("lat") or self.lat.get())
        self.lon.set(saved.get("lon") or self.lon.get())

        # show correct flashing row
        if d["flash_method"] == "uf2_drive":
            self.uf2_row.grid()
            self.refresh_drives()
        else:
            self.uf2_row.grid_remove()

        # gps behavior
        if d["gps_mode"] == "fixed":
            self.lat_entry.configure(state="normal")
            self.lon_entry.configure(state="normal")
        else:
            self.lat_entry.configure(state="disabled")
            self.lon_entry.configure(state="disabled")

        # show Erase only for Heltecs
        if d["flash_method"] == "esptool":
            if not self.erase_btn.winfo_ismapped():
                self.erase_btn.pack(side="left", padx=10, pady=6)
        else:
            if self.erase_btn.winfo_ismapped():
                self.erase_btn.pack_forget()

        self._schedule_save()

    # -----------------------------
    # Validation + Meshtastic config
    # -----------------------------
    def _build_owner_strings(self):
        owner = f"{self.owner_letters.get().strip()}{self.owner_num.get().strip()}"
        owner_short = f"{self.owner_short_letters.get().strip()}{self.owner_short_num.get().strip()}"
        return owner, owner_short

    def _validate_common(self):
        mode = self.mode.get()
        d = MODE_DEFS[mode]

        fw = self.firmware_path.get().strip()
        if not fw or not os.path.isfile(fw):
            raise RuntimeError("Firmware file not set or not found.")

        if d["flash_method"] == "uf2_drive":
            if not self.uf2_drive.get().strip():
                raise RuntimeError("Select/detect the UF2 drive.")

        if not self.owner_letters.get().strip():
            raise RuntimeError("Owner letters required.")
        if not self.owner_num.get().strip().isdigit():
            raise RuntimeError("Owner number must be digits (e.g. 01).")
        if not self.owner_short_letters.get().strip():
            raise RuntimeError("Owner short letters required.")
        if not self.owner_short_num.get().strip().isdigit():
            raise RuntimeError("Owner short number must be digits (e.g. 01).")

        if d["gps_mode"] == "fixed":
            try:
                float(self.lat.get().strip())
                float(self.lon.get().strip())
            except ValueError:
                raise RuntimeError("Latitude/Longitude must be valid numbers for fixed position mode.")

    def _meshtastic_config_cmd(self):
        mode = self.mode.get()
        d = MODE_DEFS[mode]
        owner, owner_short = self._build_owner_strings()

        cmd = ["meshtastic"] + [
            "--seturl", SETURL_VALUE,
            "--set-owner", owner,
            "--set-owner-short", owner_short,

            "--set", "neighbor_info.update_interval", "120",
            "--set", "neighbor_info.transmit_over_lora", "true",
            "--set", "neighbor_info.enabled", "true",

            "--set", "device.role", d["role"],
            "--set", "device.rebroadcast_mode", "ALL",
            "--set", "lora.config_ok_to_mqtt", "true",
        ]

        if d["gps_mode"] == "fixed":
            cmd += [
                "--set", "position.fixed_position", "true",
                "--setlat", self.lat.get().strip(),
                "--setlon", self.lon.get().strip(),
            ]
        else:
            cmd += ["--set", "position.fixed_position", "false"]

        return cmd

    # -----------------------------
    # Flashing implementations
    # -----------------------------
    def _do_flash(self):
        mode = self.mode.get()
        d = MODE_DEFS[mode]
        fw = self.firmware_path.get().strip()

        if d["flash_method"] == "uf2_drive":
            self.log_write("Flashing (UF2 copy)...\n")
            copy_uf2_to_drive(fw, self.uf2_drive.get().strip(), self.log_write)
            wait_seconds(8, self.log_write)
            return

        # Heltec modes: esptool auto-detect, show streaming progress
        self.log_write("Flashing (esptool)...\n")
        cmd = ["esptool", "--baud", "115200", "write-flash", "0x00", fw]
        run_cmd_stream(cmd, self.log_write)
        self.log_write("\nFlash done.\n")

    def _do_configure(self):
        self.log_write("Configuring via Meshtastic CLI...\n")
        run_cmd_stream(self._meshtastic_config_cmd(), self.log_write)
        self.log_write("\nConfiguration complete.\n")

    def _do_erase(self):
        self.log_write("Erasing flash (esptool erase-flash)...\n")
        run_cmd_stream(["esptool", "erase-flash"], self.log_write)
        self.log_write("\nErase complete.\n")

    # -----------------------------
    # Buttons (threaded)
    # -----------------------------
    def flash_only(self):
        def worker():
            try:
                self._validate_common()
                self.log_write(f"Mode: {MODE_DEFS[self.mode.get()]['label']}\n")
                self._do_flash()
            except Exception as e:
                self.log_write(f"\nERROR: {e}\n")
                messagebox.showerror("Error", str(e))
        threading.Thread(target=worker, daemon=True).start()

    def configure_only(self):
        def worker():
            try:
                self._validate_common()
                self.log_write(f"Mode: {MODE_DEFS[self.mode.get()]['label']}\n")
                self._do_configure()
            except Exception as e:
                self.log_write(f"\nERROR: {e}\n")
                messagebox.showerror("Error", str(e))
        threading.Thread(target=worker, daemon=True).start()

    def erase_flash(self):
        def worker():
            try:
                mode = self.mode.get()
                if MODE_DEFS[mode]["flash_method"] != "esptool":
                    raise RuntimeError("Erase is only available for Heltec (esptool) modes.")
                self._validate_common()
                self.log_write(f"Mode: {MODE_DEFS[self.mode.get()]['label']}\n")
                self._do_erase()
            except Exception as e:
                self.log_write(f"\nERROR: {e}\n")
                messagebox.showerror("Error", str(e))
        threading.Thread(target=worker, daemon=True).start()

    # -----------------------------
    # Misc UI actions
    # -----------------------------
    def pick_firmware(self):
        p = filedialog.askopenfilename(filetypes=[("Firmware", "*.*"), ("All files", "*.*")])
        if p:
            self.firmware_path.set(p)

    def refresh_drives(self):
        drives = detect_uf2_drives()
        self.drive_combo["values"] = drives
        if drives and not self.uf2_drive.get():
            self.uf2_drive.set(drives[0])
        if not drives:
            self.uf2_drive.set("")
            self.log_write("No UF2 drives detected. Put device in UF2 boot mode and click Refresh.\n")

    def log_write(self, s):
        self.log.insert("end", s)
        self.log.see("end")
        self.root.update_idletasks()

    def clear_log(self):
        self.log.delete("1.0", "end")

    def _exit_app(self):
        self._save_state()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
