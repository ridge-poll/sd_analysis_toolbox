"""
tiff_panel.py
-------------
Self-contained tk.Frame that displays a TIFF image stack.

Key design points:
  - No playback logic — driven externally via show_at_time(t)
  - Lazy loading + LRU cache (downsampled frames only)
  - Thread-pool prefetcher: N workers load frames in parallel
  - ROI crop: user draws a rubber-band rectangle on the canvas; only that
    region is extracted from each raw frame before downsampling. Stored as
    normalised [0,1] coordinates so it survives image-size changes.
    Press "Select ROI" to pick a new region; "Clear ROI" to show full frame.
  - Optional percentile contrast normalization

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

from utils import (
    LRUCache, CACHE_SIZE, MAX_DISPLAY_PX,
    natural_sort_key, load_and_downsample,
    normalize_percentile,
)

WORKER_THREADS = 4
BASE_PREFETCH  = 16
MAX_PREFETCH   = 128

NORM_LOW_PCT  = 1.0
NORM_HIGH_PCT = 99.0

# Visual style for the rubber-band rectangle while drawing
ROI_RECT_STYLE = dict(outline="#00e5ff", width=2, dash=(4, 4))


class TiffPanel(tk.Frame):
    """
    Embeddable TIFF stack viewer with optional ROI crop.

    Parameters
    ----------
    parent : tk.Widget
    frame_rate : float
        TIFF frames per second of real time.
    on_folder_loaded : callable, optional
        Called with (n_frames, frame_rate) after a folder is loaded.
    normalize : bool
        Start with percentile contrast normalization enabled.
    """

    def __init__(self, parent, frame_rate: float = 1.0,
                 on_folder_loaded=None, on_roi_select=None,
                 normalize: bool = True, **kwargs):
        super().__init__(parent, **kwargs)

        self._paths:       list[str]                 = []
        self._cache:       LRUCache                  = LRUCache(CACHE_SIZE)
        self._tk_image:    ImageTk.PhotoImage | None = None
        self._frame_idx:   int                       = -1
        self._frame_rate:  float                     = max(frame_rate, 1e-6)
        self._speed:       float                     = 1.0
        self._canvas_item: int | None                = None

        # ── ROI state ──────────────────────────────────────────────────────
        # _roi_norm: (x0n, y0n, x1n, y1n) in [0,1] image coords, or None
        # _roi_raw : the raw PIL image size we last computed coords against
        self._roi_norm:    tuple | None              = None
        self._roi_raw_size: tuple | None             = None   # (w, h) of raw frame
        # Rubber-band drag state (canvas pixel coords)
        self._drag_start:  tuple | None              = None
        self._drag_rect:   int | None                = None   # canvas rect item id
        self._selecting:   bool                      = False  # selection mode active

        self._norm_var = tk.BooleanVar(value=normalize)
        self._on_folder_loaded = on_folder_loaded
        self._on_roi_select    = on_roi_select

        self._executor = ThreadPoolExecutor(
            max_workers=WORKER_THREADS, thread_name_prefix="tiff-load"
        )
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

        with self._futures_lock:
            for fut in self._futures.values():
                fut.cancel()
            self._futures.clear()

        self._paths     = paths
        self._cache     = LRUCache(CACHE_SIZE)
        self._frame_idx = -1

        if self._on_folder_loaded:
            self._on_folder_loaded(len(paths), self._frame_rate)

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

        tk.Checkbutton(
            tb, text="Normalize",
            variable=self._norm_var,
            command=self._on_norm_toggled,
        ).pack(side=tk.LEFT, padx=(8, 2))

        # ROI controls — right side of toolbar
        tk.Button(tb, text="Clear ROI",
                  command=self._clear_roi).pack(side=tk.RIGHT, padx=2)
        self._roi_btn = tk.Button(tb, text="Select ROI",
                                  command=self._start_roi_selection)
        self._roi_btn.pack(side=tk.RIGHT, padx=2)

        # Canvas — no overlaid text labels; frame counter lives in toolbar
        self._counter_var = tk.StringVar(value="")
        tk.Label(tb, textvariable=self._counter_var, fg="gray"
                 ).pack(side=tk.RIGHT, padx=8)

        self._canvas = tk.Canvas(self, bg="black", width=MAX_DISPLAY_PX)
        self._canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Rubber-band bindings (only active when _selecting is True)
        self._canvas.bind("<ButtonPress-1>",   self._on_drag_start)
        self._canvas.bind("<B1-Motion>",        self._on_drag_move)
        self._canvas.bind("<ButtonRelease-1>",  self._on_drag_end)

    # =========================================================================
    # ROI selection
    # =========================================================================

    def _start_roi_selection(self):
        """
        Enter ROI-drawing mode.
        Fires on_roi_select() first so main_gui can pause playback before
        the user starts drawing — prevents the .99 s timeout issue where
        the playback loop interferes with mouse events.
        """
        if self._on_roi_select:
            self._on_roi_select()
        self._selecting = True
        self._canvas.config(cursor="crosshair")
        self._roi_btn.config(relief=tk.SUNKEN, text="Drawing…")

    def _clear_roi(self):
        """Remove any active ROI and show the full frame."""
        self._roi_norm     = None
        self._roi_raw_size = None
        self._selecting    = False
        self._canvas.config(cursor="")
        self._roi_btn.config(relief=tk.RAISED, text="Select ROI")
        self._flush_cache_and_redisplay()

    def _on_drag_start(self, event):
        if not self._selecting:
            return
        self._drag_start = (event.x, event.y)
        if self._drag_rect is not None:
            self._canvas.delete(self._drag_rect)
            self._drag_rect = None

    def _on_drag_move(self, event):
        if not self._selecting or self._drag_start is None:
            return
        x0, y0 = self._drag_start
        if self._drag_rect is not None:
            self._canvas.delete(self._drag_rect)
        self._drag_rect = self._canvas.create_rectangle(
            x0, y0, event.x, event.y, **ROI_RECT_STYLE)

    def _on_drag_end(self, event):
        if not self._selecting or self._drag_start is None:
            return

        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        self._drag_start = None

        # Clean up the rubber band
        if self._drag_rect is not None:
            self._canvas.delete(self._drag_rect)
            self._drag_rect = None

        # Reject degenerate rectangles
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            self._selecting = False
            self._canvas.config(cursor="")
            self._roi_btn.config(relief=tk.RAISED, text="Select ROI")
            return

        # Convert canvas pixel coords → normalised image coords.
        # The displayed image is centred on the canvas; recover its top-left.
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()

        if self._tk_image is None:
            self._selecting = False
            self._canvas.config(cursor="")
            self._roi_btn.config(relief=tk.RAISED, text="Select ROI")
            return

        iw = self._tk_image.width()
        ih = self._tk_image.height()
        img_x0 = (cw - iw) / 2
        img_y0 = (ch - ih) / 2

        # Clamp to image bounds
        def clamp_x(v): return max(0.0, min(1.0, (v - img_x0) / iw))
        def clamp_y(v): return max(0.0, min(1.0, (v - img_y0) / ih))

        nx0, nx1 = sorted([clamp_x(x0), clamp_x(x1)])
        ny0, ny1 = sorted([clamp_y(y0), clamp_y(y1)])

        self._roi_norm = (nx0, ny0, nx1, ny1)
        self._selecting = False
        self._canvas.config(cursor="")
        self._roi_btn.config(relief=tk.RAISED, text="Select ROI")

        self._flush_cache_and_redisplay()

    # =========================================================================
    # Frame display
    # =========================================================================

    def _get_frame(self, idx: int):
        img = self._cache.get(idx)
        if img is not None:
            return img

        with self._futures_lock:
            fut = self._futures.get(idx)

        if fut is not None:
            try:
                img = fut.result(timeout=2.0)
                return img
            except Exception:
                pass

        img = self._process_frame(self._paths[idx])
        self._cache.put(idx, img)
        return img

    def _process_frame(self, path: str):
        """
        Load one frame, apply ROI crop (if set), downsample, and optionally
        normalise. Called from worker threads and (rarely) the main thread.

        ROI crop is applied to the raw full-resolution image before
        downsampling so the cropped region fills the display at maximum detail.
        """
        from PIL import Image as PilImage
        img = PilImage.open(path).convert("RGB")

        if self._roi_norm is not None:
            w, h = img.size
            nx0, ny0, nx1, ny1 = self._roi_norm
            left   = int(nx0 * w)
            upper  = int(ny0 * h)
            right  = int(nx1 * w)
            lower  = int(ny1 * h)
            # Guard against zero-area boxes from rounding
            right  = max(right,  left + 1)
            lower  = max(lower,  upper + 1)
            img    = img.crop((left, upper, right, lower))

        # Downsample to display size
        img = _downsample(img)

        if self._norm_var.get():
            img = normalize_percentile(img, NORM_LOW_PCT, NORM_HIGH_PCT)

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
                x, y, anchor=tk.CENTER, image=self._tk_image)
        else:
            self._canvas.itemconfig(self._canvas_item, image=self._tk_image)
            self._canvas.coords(self._canvas_item, x, y)

        self._counter_var.set(f"{idx + 1} / {len(self._paths)}")

    # =========================================================================
    # Thread-pool prefetch
    # =========================================================================

    def _load_frame_worker(self, idx: int):
        if self._cache.get(idx) is not None:
            return self._cache.get(idx)
        img = self._process_frame(self._paths[idx])
        self._cache.put(idx, img)
        with self._futures_lock:
            self._futures.pop(idx, None)
        return img

    def _submit_prefetch(self, current_idx: int):
        lookahead = min(int(BASE_PREFETCH * self._speed), MAX_PREFETCH)
        n = len(self._paths)
        with self._futures_lock:
            for offset in range(1, lookahead + 1):
                nxt = current_idx + offset
                if nxt >= n:
                    break
                if self._cache.get(nxt) is not None:
                    continue
                if nxt in self._futures:
                    continue
                fut = self._executor.submit(self._load_frame_worker, nxt)
                self._futures[nxt] = fut

    # =========================================================================
    # Cache helpers
    # =========================================================================

    def _flush_cache_and_redisplay(self):
        """Flush cache and cancel prefetch so frames re-render with new settings."""
        with self._futures_lock:
            for fut in self._futures.values():
                fut.cancel()
            self._futures.clear()
        self._cache = LRUCache(CACHE_SIZE)
        current = max(0, self._frame_idx)
        self._frame_idx = -1
        if self._paths:
            self._show_frame(current)
            self._submit_prefetch(current)

    # =========================================================================
    # Normalization toggle
    # =========================================================================

    def _on_norm_toggled(self):
        if self._paths:
            self._flush_cache_and_redisplay()

    # =========================================================================
    # Event handlers
    # =========================================================================

    def _open_folder(self):
        folder = filedialog.askdirectory(title="Select folder with TIFF files")
        if folder:
            self.load_folder(folder)


# ── module-level helper (avoids importing utils internals in worker threads) ──

def _downsample(img, max_px: int = MAX_DISPLAY_PX):
    """Resize image so its longest side ≤ max_px. Never enlarges."""
    w, h  = img.size
    scale = max_px / max(w, h)
    if scale < 1.0:
        from PIL import Image as PilImage
        img = img.resize((int(w * scale), int(h * scale)), PilImage.LANCZOS)
    return img