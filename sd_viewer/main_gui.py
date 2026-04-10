"""
main_gui.py
-----------
Top-level Tkinter application. Assembles EphysPanel, TiffPanel, and
SyncController into a single window.

Layout:
  ┌─────────────────────────────────────────────────────┐
  ├──────────────────────┬──────────────────────────────┤
  │  [Open TIFFs…]       │  [Open Ephys…]               │  panel toolbars
  │    TIFF Panel        │      Ephys Panel             │
  │    (image)           │      (traces)                │
  │                      │                              │
  ├──────────────────────┴──────────────────────────────┤
  │  ◀◀  ▶ Play  ▶▶  Speed:[1x]  Offset:[__]s  0.00 s  │  sync controls
  └─────────────────────────────────────────────────────┘

Usage:
    python main_gui.py
    python main_gui.py path/to/file.h5 path/to/tiff_folder
"""

import sys
import tkinter as tk
from tkinter import ttk

from ephys_panel import EphysPanel
from tiff_panel import TiffPanel
from sync_controller import SyncController

# ── tuneable defaults ─────────────────────────────────────────────────────────
DEFAULT_OFFSET   = 0.0    # seconds (ephys t=0 → TIFF t=0 by default)
DEFAULT_SPEED    = 1.0
SPEED_OPTIONS    = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
WIN_WIDTH        = 1400
WIN_HEIGHT       = 800
# ─────────────────────────────────────────────────────────────────────────────


class MainApp(tk.Tk):

    def __init__(self, ephys_path=None, tiff_folder=None):
        super().__init__()
        self.title("Ephys + TIFF Sync Viewer")
        self.geometry(f"{WIN_WIDTH}x{WIN_HEIGHT}")
        self.resizable(True, True)

        self._build_ui()
        self._wire_controller()

        # load files passed on the command line
        if ephys_path:
            self._ephys_panel.load_file(ephys_path)
        if tiff_folder:
            self._tiff_panel.load_folder(tiff_folder)

    # =========================================================================
    # UI construction
    # =========================================================================

    def _build_ui(self):
        # ── main paned area ────────────────────────────────────────────────
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED,
                               sashwidth=5)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tiff_panel = TiffPanel(
            paned,
            frame_rate=1.0,
            on_folder_loaded=self._on_tiff_loaded,
            bd=1, relief=tk.SUNKEN)
        paned.add(self._tiff_panel, minsize=200)

        self._ephys_panel = EphysPanel(
            paned,
            on_file_loaded=self._on_ephys_loaded,
            bd=1, relief=tk.SUNKEN)
        paned.add(self._ephys_panel, minsize=300)

        # give ephys panel more initial space
        self.update_idletasks()
        paned.sash_place(0, int(WIN_WIDTH * 0.38), 0)

        # ── bottom sync controls ───────────────────────────────────────────
        ctrl = tk.Frame(self, bd=1, relief=tk.RAISED)
        ctrl.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=2)

        tk.Button(ctrl, text="◀◀", width=3,
                  command=self._step_back).pack(side=tk.LEFT, padx=2)

        self._play_btn = tk.Button(ctrl, text="▶  Play", width=8,
                                   command=self._toggle_play)
        self._play_btn.pack(side=tk.LEFT, padx=2)

        tk.Button(ctrl, text="▶▶", width=3,
                  command=self._step_fwd).pack(side=tk.LEFT, padx=2)

        # speed selector
        tk.Label(ctrl, text="  Speed:").pack(side=tk.LEFT, padx=(8, 2))
        self._speed_var = tk.StringVar(value="1x")
        speed_menu = ttk.Combobox(ctrl, textvariable=self._speed_var,
                                  values=[f"{s}x" for s in SPEED_OPTIONS],
                                  state="readonly", width=5)
        speed_menu.pack(side=tk.LEFT)
        speed_menu.bind("<<ComboboxSelected>>", self._on_speed_change)

        # TIFF offset
        tk.Label(ctrl, text="  TIFF offset (s):").pack(side=tk.LEFT, padx=(12, 2))
        self._offset_var = tk.StringVar(value=str(DEFAULT_OFFSET))
        offset_entry = tk.Entry(ctrl, textvariable=self._offset_var, width=8)
        offset_entry.pack(side=tk.LEFT)
        offset_entry.bind("<Return>",   self._apply_offset)
        offset_entry.bind("<FocusOut>", self._apply_offset)

        # current-time label (right side)
        self._time_var = tk.StringVar(value="0.00 s")
        tk.Label(ctrl, textvariable=self._time_var, width=12
                 ).pack(side=tk.RIGHT, padx=6)

        # shared scrubber
        self._slider_var = tk.DoubleVar(value=0.0)
        self._slider = ttk.Scale(self, from_=0.0, to=1.0,
                                 orient=tk.HORIZONTAL,
                                 variable=self._slider_var,
                                 command=self._on_slider)
        self._slider.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 2))

    # =========================================================================
    # Controller wiring
    # =========================================================================

    def _wire_controller(self):
        self._ctrl = SyncController(
            root=self,
            ephys_panel=self._ephys_panel,
            tiff_panel=self._tiff_panel,
            tiff_offset=DEFAULT_OFFSET,
        )
        self._ctrl.register_on_tick(self._on_tick)

    # =========================================================================
    # File loading callbacks
    # =========================================================================

    def _on_ephys_loaded(self, ef):
        """Called by EphysPanel after a file loads. Update shared timeline."""
        self._ctrl.set_max_time(ef.duration)
        self._slider.configure(to=ef.duration)
        self._slider_var.set(0.0)
        self._time_var.set("0.00 s")

    def _on_tiff_loaded(self, n_frames, frame_rate):
        """Called by TiffPanel after a folder loads."""
        # Nothing to do here currently — timeline is driven by ephys duration.
        # Could extend to warn if TIFF duration + offset exceeds ephys duration.
        pass

    # =========================================================================
    # Sync controls
    # =========================================================================

    def _toggle_play(self):
        if self._ctrl.is_playing:
            self._ctrl.pause()
            self._play_btn.config(text="▶  Play")
        else:
            self._ctrl.play()
            self._play_btn.config(text="⏸  Pause")

    def _step_back(self):
        self._ctrl.pause()
        self._play_btn.config(text="▶  Play")
        step = self._ephys_panel.window_sec * 0.5
        self._ctrl.seek(self._ctrl.current_time - step)

    def _step_fwd(self):
        self._ctrl.pause()
        self._play_btn.config(text="▶  Play")
        step = self._ephys_panel.window_sec * 0.5
        self._ctrl.seek(self._ctrl.current_time + step)

    def _on_slider(self, value):
        t = float(value)
        # avoid feedback loop: only seek if meaningfully different from current
        if abs(t - self._ctrl.current_time) > 0.01:
            self._ctrl.seek(t)

    def _on_tick(self, t: float):
        """Called by SyncController on every playback tick."""
        self._slider_var.set(t)
        self._time_var.set(f"{t:.2f} s")
        # if playback reached the end, reset button label
        if not self._ctrl.is_playing:
            self._play_btn.config(text="▶  Play")

    def _on_speed_change(self, _=None):
        try:
            val = float(self._speed_var.get().rstrip("x"))
            self._ctrl.set_speed(val)
        except ValueError:
            pass

    def _apply_offset(self, _=None):
        try:
            offset = float(self._offset_var.get())
            self._ctrl.set_offset(offset)
        except ValueError:
            pass


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ephys_path  = sys.argv[1] if len(sys.argv) > 1 else None
    tiff_folder = sys.argv[2] if len(sys.argv) > 2 else None
    MainApp(ephys_path, tiff_folder).mainloop()