"""
tiff_panel.py
-------------
Self-contained tk.Frame that displays a TIFF image stack.

Key design points:
  - No playback logic — driven externally via show_at_time(t)
  - Lazy loading + LRU cache (downsampled frames only)
  - frame_rate: how many TIFF frames per second of real time (default 1.0)
  - Only redraws when the frame index actually changes (no wasted work)

Public API (called by SyncController / main_gui):
    panel.load_folder(path)
    panel.show_at_time(t_seconds)
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import ImageTk

from sd_viewer.utils import LRUCache, CACHE_SIZE, MAX_DISPLAY_PX, natural_sort_key, load_and_downsample


class TiffPanel(tk.Frame):
    """
    Embeddable TIFF stack viewer. Parent can be any Tk container.

    Parameters
    ----------
    parent : tk.Widget
        Tkinter parent widget.
    frame_rate : float
        TIFF frames per second of real time. Default 1.0.
    on_folder_loaded : callable, optional
        Called with (n_frames, frame_rate) after a folder is loaded.
    kwargs
        Passed to tk.Frame.
    """

    def __init__(self, parent, frame_rate: float = 1.0,
                 on_folder_loaded=None, **kwargs):
        super().__init__(parent, **kwargs)

        self._paths:      list[str]              = []
        self._cache:      LRUCache               = LRUCache(CACHE_SIZE)
        self._tk_image:   ImageTk.PhotoImage | None = None
        self._frame_idx:  int                    = -1   # -1 = nothing shown yet
        self._frame_rate: float                  = max(frame_rate, 1e-6)

        self._on_folder_loaded = on_folder_loaded

        self._build_ui()

    # =========================================================================
    # Public API
    # =========================================================================

    def load_folder(self, path: str):
        """Load all TIFFs from a folder. Safe to call multiple times."""
        paths = [
            os.path.join(path, f)
            for f in os.listdir(path)
            if f.lower().endswith((".tif", ".tiff"))
        ]
        if not paths:
            messagebox.showwarning("No files",
                                   "No .tif / .tiff files found in that folder.")
            return

        paths.sort(key=natural_sort_key)

        self._paths     = paths
        self._cache     = LRUCache(CACHE_SIZE)
        self._frame_idx = -1   # force redraw on next show_at_time call

        n = len(paths)
        self._info_var.set(f"{os.path.basename(path)}  |  {n} frames  |  {self._frame_rate:.3g} fps")

        if self._on_folder_loaded:
            self._on_folder_loaded(n, self._frame_rate)

        self._show_frame(0)

    def show_at_time(self, t: float):
        """
        Display the frame that corresponds to time t (seconds, TIFF timeline).
        Called by SyncController on every tick.
        Only redraws if the frame index has changed.
        """
        if not self._paths:
            return
        idx = int(t * self._frame_rate)
        idx = max(0, min(idx, len(self._paths) - 1))
        if idx != self._frame_idx:
            self._show_frame(idx)

    @property
    def n_frames(self) -> int:
        return len(self._paths)

    @property
    def duration(self) -> float:
        """Total duration of the TIFF stack in seconds."""
        if not self._paths:
            return 0.0
        return len(self._paths) / self._frame_rate

    # =========================================================================
    # UI construction
    # =========================================================================

    def _build_ui(self):
        # ── toolbar ────────────────────────────────────────────────────────
        tb = tk.Frame(self, bd=1, relief=tk.RAISED)
        tb.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)

        tk.Button(tb, text="Open TIFFs…", command=self._open_folder
                  ).pack(side=tk.LEFT, padx=2)

        self._info_var = tk.StringVar(value="No folder loaded.")
        tk.Label(tb, textvariable=self._info_var, fg="gray"
                 ).pack(side=tk.LEFT, padx=8)

        # frame counter (right side of toolbar)
        self._counter_var = tk.StringVar(value="–")
        tk.Label(tb, textvariable=self._counter_var
                 ).pack(side=tk.RIGHT, padx=6)

        # ── canvas ─────────────────────────────────────────────────────────
        self._canvas = tk.Canvas(self, bg="black", width=MAX_DISPLAY_PX)
        self._canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # =========================================================================
    # Frame display
    # =========================================================================

    def _get_frame(self, idx: int):
        """Return the downsampled PIL image for frame idx, using the LRU cache."""
        img = self._cache.get(idx)
        if img is None:
            img = load_and_downsample(self._paths[idx])
            self._cache.put(idx, img)
        return img

    def _show_frame(self, idx: int):
        if not self._paths:
            return
        idx = max(0, min(idx, len(self._paths) - 1))
        self._frame_idx = idx

        pil_img = self._get_frame(idx)
        self._tk_image = ImageTk.PhotoImage(pil_img)

        cw = self._canvas.winfo_width()  or MAX_DISPLAY_PX
        ch = self._canvas.winfo_height() or MAX_DISPLAY_PX
        self._canvas.delete("all")
        self._canvas.create_image(cw // 2, ch // 2,
                                  anchor=tk.CENTER, image=self._tk_image)

        self._counter_var.set(f"Frame {idx + 1} / {len(self._paths)}")

    # =========================================================================
    # Event handlers
    # =========================================================================

    def _open_folder(self):
        folder = filedialog.askdirectory(title="Select folder with TIFF files")
        if folder:
            self.load_folder(folder)
