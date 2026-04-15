"""
ephys_panel.py
--------------
Self-contained tk.Frame that displays ephys traces from an EphysFile.

Key design points:
  - No playback logic here — driven externally via show_at_time(t)
  - display_mode: "traces" | "spectrogram"
  - Traces: fast redraws via set_data() on pre-existing Line2D objects
  - Spectrogram: computed fresh on every redraw (AC channel, 0-300 Hz, dB)
  - LRU chunk cache keyed by (sweep, ch_idx, start, stop)

Public API (called by SyncController / main_gui):
    panel.load_file(path)
    panel.show_at_time(t_seconds)
    panel.set_sweep(sweep_name)
"""

import os
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from sd_viewer.utils import LRUCache, CACHE_SIZE
from sd_viewer.ephys_file import EphysFile
from sd_viewer.spectrogram import compute_spectrogram, ac_channel_index

# ── tuneable defaults ─────────────────────────────────────────────────────────
DEFAULT_WINDOW_SEC = 10
DEFAULT_DECIMATE   = 10
MIN_DECIMATE       = 1
MAX_DECIMATE       = 200
# ─────────────────────────────────────────────────────────────────────────────


class EphysPanel(tk.Frame):
    """
    Embeddable ephys trace viewer. Parent can be any Tk container.

    Parameters
    ----------
    parent : tk.Widget
        Tkinter parent widget.
    on_file_loaded : callable, optional
        Called with (EphysFile) after a new file is successfully loaded.
        Main GUI uses this to update the shared timeline/slider range.
    kwargs
        Passed to tk.Frame.
    """

    def __init__(self, parent, on_file_loaded=None, **kwargs):
        super().__init__(parent, **kwargs)

        self._ef:           EphysFile | None = None
        self._cache:        LRUCache         = LRUCache(CACHE_SIZE)
        self._t_offset:     float            = 0.0
        self._ylimits:      list             = []
        self._axes:         list             = []
        self._lines:        list             = []
        self._display_mode: str              = "traces"

        self._on_file_loaded = on_file_loaded

        # ── tkinter variables ──────────────────────────────────────────────
        self._window_var     = tk.DoubleVar(value=DEFAULT_WINDOW_SEC)
        self._decimate_var   = tk.IntVar(value=DEFAULT_DECIMATE)
        self._cache_size_var = tk.IntVar(value=CACHE_SIZE)
        self._sweep_var      = tk.StringVar()

        self._build_ui()

    # =========================================================================
    # Public API
    # =========================================================================

    def load_file(self, path: str):
        """Load an HDF5 ephys file. Safe to call multiple times."""
        if self._ef:
            self._ef.close()
        self._cache    = LRUCache(self._cache_size_var.get())
        self._ef       = EphysFile(path)
        self._t_offset = 0.0

        self._info_var.set(
            f"{os.path.basename(path)}  |  "
            f"{self._ef.sample_rate:.0f} Hz  |  "
            f"{self._ef.duration:.1f} s  |  "
            f"{self._ef.n_channels} ch"
        )

        self._sweep_menu["values"] = self._ef.sweeps
        self._sweep_var.set(self._ef.sweeps[0])

        self._ylimits = self._ef.scan_ylimits(self._ef.sweeps[0])
        self._rebuild_axes()
        self._build_yaxis_panel()

        if self._on_file_loaded:
            self._on_file_loaded(self._ef)

    def show_at_time(self, t: float):
        """
        Scroll the trace window so that t (seconds, ephys timeline) is the
        left edge of the display. Called by SyncController on every tick.
        """
        if not self._ef:
            return
        win = self._window_var.get()
        t   = max(0.0, min(t, self._ef.duration - win))
        self._t_offset = t
        self._redraw()

    def set_sweep(self, sweep_name: str):
        """Switch to a different sweep and reset the view."""
        if not self._ef or sweep_name not in self._ef.sweeps:
            return
        self._sweep_var.set(sweep_name)
        self._on_sweep_change()

    @property
    def window_sec(self) -> float:
        return self._window_var.get()

    @property
    def current_time(self) -> float:
        return self._t_offset

    # =========================================================================
    # UI construction
    # =========================================================================

    def _build_ui(self):
        # ── toolbar ────────────────────────────────────────────────────────
        tb = tk.Frame(self, bd=1, relief=tk.RAISED)
        tb.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)

        tk.Button(tb, text="Open Ephys…", command=self._open_file
                  ).pack(side=tk.LEFT, padx=2)

        tk.Label(tb, text="Sweep:").pack(side=tk.LEFT, padx=(8, 1))
        self._sweep_menu = ttk.Combobox(
            tb, textvariable=self._sweep_var, state="readonly", width=12)
        self._sweep_menu.pack(side=tk.LEFT)
        self._sweep_menu.bind("<<ComboboxSelected>>", self._on_sweep_change)

        tk.Label(tb, text="  Window (s):").pack(side=tk.LEFT)
        tk.Spinbox(tb, from_=1, to=600, width=5,
                   textvariable=self._window_var,
                   command=self._on_settings_change).pack(side=tk.LEFT)

        tk.Label(tb, text="  Decimate:").pack(side=tk.LEFT)
        tk.Spinbox(tb, from_=MIN_DECIMATE, to=MAX_DECIMATE, width=5,
                   textvariable=self._decimate_var,
                   command=self._on_settings_change).pack(side=tk.LEFT)

        tk.Label(tb, text="  Cache:").pack(side=tk.LEFT)
        tk.Spinbox(tb, from_=2, to=200, width=5,
                   textvariable=self._cache_size_var,
                   command=self._on_cache_resize).pack(side=tk.LEFT)

        # display mode toggle
        self._mode_btn = tk.Button(
            tb, text="Spectrogram", command=self._toggle_display_mode)
        self._mode_btn.pack(side=tk.RIGHT, padx=6)

        self._info_var = tk.StringVar(value="No file loaded.")
        tk.Label(tb, textvariable=self._info_var, fg="gray"
                 ).pack(side=tk.RIGHT, padx=6)

        # ── y-axis panel (rebuilt per file) ────────────────────────────────
        self._yframe = tk.Frame(self)
        self._yframe.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=1)
        self._yentries: list[tuple[tk.Entry, tk.Entry]] = []

        # ── matplotlib canvas ───────────────────────────────────────────────
        self._fig = Figure(figsize=(10, 4), tight_layout=True)
        self._mpl_canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._mpl_canvas.get_tk_widget().pack(
            side=tk.TOP, fill=tk.BOTH, expand=True)

    # ── y-axis panel ──────────────────────────────────────────────────────

    def _build_yaxis_panel(self):
        for w in self._yframe.winfo_children():
            w.destroy()
        self._yentries.clear()

        if not self._ef:
            return

        tk.Label(self._yframe, text="Y range:",
                 font=("TkDefaultFont", 8)).grid(row=0, column=0, padx=(0, 4))

        for i, (ch, (lo, hi)) in enumerate(
                zip(self._ef.ch_names, self._ylimits)):
            col = 1 + i * 4
            tk.Label(self._yframe, text=f"{ch}:",
                     font=("TkDefaultFont", 8)).grid(row=0, column=col, padx=(6, 1))
            e_lo = tk.Entry(self._yframe, width=8)
            e_lo.insert(0, f"{lo:.4g}")
            e_lo.grid(row=0, column=col + 1, padx=1)
            tk.Label(self._yframe, text="to",
                     font=("TkDefaultFont", 8)).grid(row=0, column=col + 2)
            e_hi = tk.Entry(self._yframe, width=8)
            e_hi.insert(0, f"{hi:.4g}")
            e_hi.grid(row=0, column=col + 3, padx=1)
            self._yentries.append((e_lo, e_hi))

        tk.Button(self._yframe, text="Apply",
                  font=("TkDefaultFont", 8),
                  command=self._apply_ylimits
                  ).grid(row=0, column=1 + len(self._ef.ch_names) * 4, padx=6)

    def _apply_ylimits(self):
        for i, (e_lo, e_hi) in enumerate(self._yentries):
            try:
                lo, hi = float(e_lo.get()), float(e_hi.get())
                if lo < hi:
                    self._ylimits[i] = (lo, hi)
                    self._axes[i].set_ylim(lo, hi)
            except ValueError:
                pass
        self._mpl_canvas.draw_idle()

    # =========================================================================
    # Axes
    # =========================================================================

    def _rebuild_axes(self):
        self._fig.clear()
        self._axes.clear()
        self._lines.clear()

        if not self._ef:
            return

        n = self._ef.n_channels
        for i, (name, unit, (lo, hi)) in enumerate(
                zip(self._ef.ch_names, self._ef.ch_units, self._ylimits)):
            ax = self._fig.add_subplot(n, 1, i + 1)
            ax.set_ylabel(f"{name}\n({unit})", fontsize=8)
            ax.set_ylim(lo, hi)
            ax.grid(True, lw=0.3, alpha=0.5)
            if i < n - 1:
                ax.tick_params(labelbottom=False)
            line, = ax.plot([], [], lw=0.5, color="steelblue")
            self._axes.append(ax)
            self._lines.append(line)

        if self._axes:
            self._axes[-1].set_xlabel("Time (s)", fontsize=9)

    # =========================================================================
    # Drawing
    # =========================================================================

    def _redraw(self):
        if not self._ef:
            return
        if self._display_mode == "traces":
            self._redraw_traces()
        else:
            self._redraw_spectrogram()

    def _redraw_traces(self):
        sweep  = self._sweep_var.get()
        dec    = max(1, self._decimate_var.get())
        sr     = self._ef.sample_rate
        win    = self._window_var.get()
        t0     = self._t_offset
        start  = int(t0 * sr)
        stop   = min(int((t0 + win) * sr), self._ef.n_samples)

        for i, (ax, line) in enumerate(zip(self._axes, self._lines)):
            sig     = self._get_chunk(sweep, i, start, stop)
            sig_dec = sig[::dec]
            t_dec   = t0 + np.arange(len(sig_dec)) * (dec / sr)
            line.set_data(t_dec, sig_dec)
            ax.set_xlim(t0, t0 + win)

        self._mpl_canvas.draw_idle()

    def _redraw_spectrogram(self):
        """
        Compute and render the spectrogram for the current window.
        Computed fresh on every call — fast enough for typical LFP windows.
        Uses the AC channel (last active channel by convention).
        """
        sweep  = self._sweep_var.get()
        sr     = self._ef.sample_rate
        win    = self._window_var.get()
        t0     = self._t_offset
        start  = int(t0 * sr)
        stop   = min(int((t0 + win) * sr), self._ef.n_samples)

        ac_idx = ac_channel_index(self._ef.n_channels)
        signal = self._get_chunk(sweep, ac_idx, start, stop)
        r      = compute_spectrogram(signal, sr, t_start=t0)

        self._fig.clear()
        ax = self._fig.add_subplot(1, 1, 1)

        t_abs = r.t_start + r.times
        ax.pcolormesh(t_abs, r.freqs, r.power_db, shading="auto", cmap="inferno")
        ax.set_ylabel("Frequency (Hz)", fontsize=9)
        ax.set_xlabel("Time (s)", fontsize=9)
        ax.set_ylim(r.freq_min, r.freq_max)
        ax.set_title(
            f"AC channel  |  {r.freq_min:.0f}–{r.freq_max:.0f} Hz  |  "
            f"window={r.nperseg} samp  overlap={r.noverlap} samp",
            fontsize=8)

        self._mpl_canvas.draw_idle()

    # =========================================================================
    # Data retrieval
    # =========================================================================

    def _get_chunk(self, sweep: str, ch_idx: int,
                   start: int, stop: int) -> np.ndarray:
        key    = (sweep, ch_idx, start, stop)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = self._ef.read_chunk(sweep, ch_idx, start, stop)
        self._cache.put(key, data)
        return data

    # =========================================================================
    # Event handlers / settings
    # =========================================================================

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open HDF5 ephys file",
            filetypes=[("HDF5 files", "*.h5 *.hdf5"), ("All files", "*.*")])
        if path:
            self.load_file(path)

    def _on_settings_change(self, *_):
        if self._ef:
            self._redraw()

    def _on_cache_resize(self, *_):
        self._cache.resize(max(2, self._cache_size_var.get()))

    def _on_sweep_change(self, *_):
        if not self._ef:
            return
        self._cache.clear()
        self._ylimits  = self._ef.scan_ylimits(self._sweep_var.get())
        self._t_offset = 0.0
        self._rebuild_axes()
        self._build_yaxis_panel()
        self._redraw()

    def _toggle_display_mode(self):
        if self._display_mode == "traces":
            self._display_mode = "spectrogram"
            self._mode_btn.config(text="Traces")
            self._yframe.pack_forget()
        else:
            self._display_mode = "traces"
            self._mode_btn.config(text="Spectrogram")
            self._yframe.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=1)
            self._rebuild_axes()
        self._redraw()