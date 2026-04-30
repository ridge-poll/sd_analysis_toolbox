"""
tiff_panel.py
-------------
Self-contained tk.Frame that displays a TIFF image stack.

Key design points:
  - No playback logic — driven externally via show_at_time(t)
  - Lazy loading + LRU cache (downsampled frames only)
  - Thread-pool prefetcher: N workers load frames in parallel so disk I/O and
    CPU decompression are fully pipelined. Lookahead scales with playback speed.
  - frame_rate: how many TIFF frames per second of real time (default 1.0)
  - Only redraws when the frame index actually changes (no wasted work)

Public API (called by SyncController / main_gui):
    panel.load_folder(path)
    panel.show_at_time(t_seconds)
    panel.set_speed(multiplier)
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, Future

from PIL import ImageTk

from utils import LRUCache, CACHE_SIZE, MAX_DISPLAY_PX, natural_sort_key, load_and_downsample

# Number of parallel loader threads. Diminishing returns above ~4 for local
# SSDs; HDD users may benefit from 2. More threads = more RAM in flight.
WORKER_THREADS = 4

# How many frames ahead to buffer at 1x. Scales linearly with speed.
BASE_PREFETCH  = 16
MAX_PREFETCH   = 128


class TiffPanel(tk.Frame):
    """
    Embeddable TIFF stack viewer.

    Parameters
    ----------
    parent : tk.Widget
    frame_rate : float
        TIFF frames per second of real time.
    on_folder_loaded : callable, optional
        Called with (n_frames, frame_rate) after a folder is loaded.
    """

    def __init__(self, parent, frame_rate: float = 1.0,
                 on_folder_loaded=None, **kwargs):
        super().__init__(parent, **kwargs)

        self._paths:       list[str]                 = []
        self._cache:       LRUCache                  = LRUCache(CACHE_SIZE)
        self._tk_image:    ImageTk.PhotoImage | None = None
        self._frame_idx:   int                       = -1
        self._frame_rate:  float                     = max(frame_rate, 1e-6)
        self._speed:       float                     = 1.0
        self._canvas_item: int | None                = None

        self._on_folder_loaded = on_folder_loaded

        # ── thread pool ───────────────────────────────────────────────────
        # Shared executor — workers are reused across folders.
        self._executor = ThreadPoolExecutor(
            max_workers=WORKER_THREADS, thread_name_prefix="tiff-load"
        )

        # Tracks in-flight futures so we don't submit the same index twice.
        # Maps frame index → Future. Guarded by _futures_lock.
        self._futures:      dict[int, Future] = {}
        self._futures_lock: threading.Lock    = threading.Lock()

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

        # Cancel in-flight work for the old folder.
        with self._futures_lock:
            for fut in self._futures.values():
                fut.cancel()
            self._futures.clear()

        self._paths     = paths
        self._cache     = LRUCache(CACHE_SIZE)
        self._frame_idx = -1

        n = len(paths)
        self._info_var.set(
            f"{os.path.basename(path)}  |  {n} frames  |  {self._frame_rate:.3g} fps"
        )

        if self._on_folder_loaded:
            self._on_folder_loaded(n, self._frame_rate)

        self._show_frame(0)
        self._submit_prefetch(0)

    def show_at_time(self, t: float):
        """Display the frame for time t. Only redraws on index change."""
        if not self._paths:
            return
        idx = int(t * self._frame_rate)
        idx = max(0, min(idx, len(self._paths) - 1))
        if idx != self._frame_idx:
            self._show_frame(idx)
            self._submit_prefetch(idx)

    def set_speed(self, multiplier: float):
        """
        Notify of current playback speed so prefetch depth scales accordingly.
        Called by SyncController whenever speed changes.
        """
        self._speed = max(1.0, multiplier)
        if self._frame_idx >= 0:
            self._submit_prefetch(self._frame_idx)

    @property
    def n_frames(self) -> int:
        return len(self._paths)

    @property
    def duration(self) -> float:
        if not self._paths:
            return 0.0
        return len(self._paths) / self._frame_rate

    # =========================================================================
    # UI
    # =========================================================================

    def _build_ui(self):
        tb = tk.Frame(self, bd=1, relief=tk.RAISED)
        tb.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)

        tk.Button(tb, text="Open TIFFs…", command=self._open_folder
                  ).pack(side=tk.LEFT, padx=2)

        self._info_var = tk.StringVar(value="No folder loaded.")
        tk.Label(tb, textvariable=self._info_var, fg="gray"
                 ).pack(side=tk.LEFT, padx=8)

        self._counter_var = tk.StringVar(value="–")
        tk.Label(tb, textvariable=self._counter_var
                 ).pack(side=tk.RIGHT, padx=6)

        self._canvas = tk.Canvas(self, bg="black", width=MAX_DISPLAY_PX)
        self._canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # =========================================================================
    # Frame display
    # =========================================================================

    def _get_frame(self, idx: int):
        """
        Return the PIL image for frame idx.

        Checks the cache first. If there's an in-flight future for this index,
        blocks briefly to collect it (it's probably nearly done). Otherwise
        falls back to a synchronous load on the main thread.
        """
        img = self._cache.get(idx)
        if img is not None:
            return img

        # Check if a worker is already loading this frame.
        with self._futures_lock:
            fut = self._futures.get(idx)

        if fut is not None:
            try:
                img = fut.result(timeout=2.0)   # wait up to 2s for the worker
                return img
            except Exception:
                pass   # worker failed — fall through to synchronous load

        # Last resort: load synchronously (should be rare after warm-up).
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
        x, y = cw // 2, ch // 2

        if self._canvas_item is None:
            self._canvas_item = self._canvas.create_image(
                x, y, anchor=tk.CENTER, image=self._tk_image
            )
        else:
            self._canvas.itemconfig(self._canvas_item, image=self._tk_image)
            self._canvas.coords(self._canvas_item, x, y)

        self._counter_var.set(f"Frame {idx + 1} / {len(self._paths)}")

    # =========================================================================
    # Thread-pool prefetch
    # =========================================================================

    def _load_frame_worker(self, idx: int):
        """
        Worker function: load one frame and store it in the cache.
        Runs inside the ThreadPoolExecutor — never on the main thread.
        """
        # Re-check cache here too — another worker may have beat us to it.
        if self._cache.get(idx) is not None:
            return self._cache.get(idx)
        img = load_and_downsample(self._paths[idx])
        self._cache.put(idx, img)
        # Clean up the futures dict once done.
        with self._futures_lock:
            self._futures.pop(idx, None)
        return img

    def _submit_prefetch(self, current_idx: int):
        """
        Submit load jobs for the next LOOKAHEAD frames, skipping any that are
        already cached or already being loaded.
        """
        lookahead = min(int(BASE_PREFETCH * self._speed), MAX_PREFETCH)
        n = len(self._paths)

        with self._futures_lock:
            for offset in range(1, lookahead + 1):
                nxt = current_idx + offset
                if nxt >= n:
                    break
                # Skip if cached or already in flight.
                if self._cache.get(nxt) is not None:
                    continue
                if nxt in self._futures:
                    continue
                fut = self._executor.submit(self._load_frame_worker, nxt)
                self._futures[nxt] = fut

    # =========================================================================
    # Event handlers
    # =========================================================================

    def _open_folder(self):
        folder = filedialog.askdirectory(title="Select folder with TIFF files")
        if folder:
            self.load_folder(folder)
