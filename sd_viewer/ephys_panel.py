"""
ephys_panel.py
--------------
Self-contained tk.Frame that displays ephys traces (and optionally a
spectrogram) from an EphysFile.

Layout (always two channels: DC on top, AC below):
  ┌─────────────────────────────────────────────┐
  │  toolbar: [Open Ephys…] [Window] [Decimate] │
  │  [Cache]  [↕ Spectrogram]                   │
  ├─────────────────────────────────────────────┤
  │  subplot 0 — DC channel                     │
  ├─────────────────────────────────────────────┤
  │  subplot 1 — AC channel                     │
  ├─────────────────────────────────────────────┤  ← only when enabled
  │  subplot 2 — Spectrogram (AC channel)       │
  ├─────────────────────────────────────────────┤
  │  Y-range controls (one row per trace ch)    │
  └─────────────────────────────────────────────┘

Public API (called by SyncController / main_gui):
    panel.load_file(path)
    panel.show_at_time(t_seconds)
"""

import os
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from utils import LRUCache, CACHE_SIZE
from ephys_file import EphysFile
from spectrogram import compute_spectrogram, ac_channel_index, SpectrogramResult

# ── tuneable defaults ─────────────────────────────────────────────────────────
DEFAULT_WINDOW_SEC = 10
DEFAULT_DECIMATE   = 10
MIN_DECIMATE       = 1
MAX_DECIMATE       = 200

# Height ratios: [DC, AC, spectrogram]
TRACE_HEIGHT_RATIO = [1, 1]
SPEC_HEIGHT_RATIO  = [1, 1, 1.4]   # spectrogram slightly taller
# ─────────────────────────────────────────────────────────────────────────────


class EphysPanel(tk.Frame):
    """
    Embeddable ephys viewer.  Renders 2 trace subplots (DC + AC) and,
    when enabled, a third spectrogram subplot below them — all on the same
    shared time axis.

    Parameters
    ----------
    parent : tk.Widget
    on_file_loaded : callable(EphysFile), optional
    kwargs : passed to tk.Frame
    """

    def __init__(self, parent, on_file_loaded=None, **kwargs):
        super().__init__(parent, **kwargs)

        self._ef:            EphysFile | None       = None
        self._cache:         LRUCache               = LRUCache(CACHE_SIZE)
        self._t_offset:      float                  = 0.0
        self._ylimits:       list                   = []

        # subplot handles — rebuilt whenever mode changes
        self._axes:          list                   = []   # [dc_ax, ac_ax] or [..., spec_ax]
        self._trace_lines:   list                   = []   # Line2D for DC and AC
        self._spec_ax:       object | None          = None # Axes for spectrogram

        # spectrogram state
        self._spec_enabled:  bool                   = False
        self._spec_result:   SpectrogramResult | None = None
        self._spec_start:    int                    = -1
        self._spec_stop:     int                    = -1

        self._on_file_loaded = on_file_loaded

        # tkinter variables
        self._window_var     = tk.DoubleVar(value=DEFAULT_WINDOW_SEC)
        self._decimate_var   = tk.IntVar(value=DEFAULT_DECIMATE)
        self._cache_size_var = tk.IntVar(value=CACHE_SIZE)

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
        self._invalidate_spec_cache()

        self._info_var.set(
            f"{os.path.basename(path)}  |  "
            f"{self._ef.sample_rate:.0f} Hz  |  "
            f"{self._ef.duration:.1f} s  |  "
            f"{self._ef.n_channels} ch"
        )

        # Use the first (and typically only) sweep automatically.
        self._current_sweep = self._ef.sweeps[0]
        self._ylimits = self._ef.scan_ylimits(self._current_sweep)

        self._rebuild_axes()
        self._build_yaxis_panel()

        if self._on_file_loaded:
            self._on_file_loaded(self._ef)

    def show_at_time(self, t: float):
        """
        Scroll the panel so that t (seconds, ephys timeline) is the left edge
        of the display window. Called by SyncController on every tick.
        """
        if not self._ef:
            return
        win = self._window_var.get()
        t   = max(0.0, min(t, self._ef.duration - win))
        self._t_offset = t
        self._redraw()

    def set_spectrogram_enabled(self, enabled: bool):
        """
        Show or hide the spectrogram subplot.
        When disabled no FFT work is done on any tick.
        Called by main_gui when the user presses the Spectrogram button.
        """
        if enabled == self._spec_enabled:
            return
        self._spec_enabled = enabled
        if not enabled:
            self._invalidate_spec_cache()
        self._rebuild_axes()
        self._redraw()

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

        self._spec_btn = tk.Button(
            tb, text="＋ Spectrogram", command=self._toggle_spectrogram)
        self._spec_btn.pack(side=tk.RIGHT, padx=6)

        self._info_var = tk.StringVar(value="No file loaded.")
        tk.Label(tb, textvariable=self._info_var, fg="gray"
                 ).pack(side=tk.RIGHT, padx=6)

        # ── matplotlib canvas ───────────────────────────────────────────────
        self._fig = Figure(tight_layout=True)
        self._mpl_canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._mpl_canvas.get_tk_widget().pack(
            side=tk.TOP, fill=tk.BOTH, expand=True)

        # ── y-axis panel (packed below canvas) ─────────────────────────────
        self._yframe = tk.Frame(self)
        self._yframe.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=2)
        self._yentries: list[tuple[tk.Entry, tk.Entry]] = []

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
                    if i < len(self._axes) - (1 if self._spec_enabled else 0):
                        self._axes[i].set_ylim(lo, hi)
            except ValueError:
                pass
        self._mpl_canvas.draw_idle()

    # =========================================================================
    # Axes — rebuilt when mode changes
    # =========================================================================

    def _rebuild_axes(self):
        """
        Tear down and recreate all subplots.

        Modes:
          traces only  → 2 subplots [DC, AC]
          with spec    → 3 subplots [DC, AC, Spectrogram]
        """
        self._fig.clear()
        self._axes.clear()
        self._trace_lines.clear()
        self._spec_ax = None

        if not self._ef:
            self._mpl_canvas.draw_idle()
            return

        n_trace = self._ef.n_channels          # should be 2: DC + AC
        n_plots = n_trace + (1 if self._spec_enabled else 0)
        ratios  = SPEC_HEIGHT_RATIO if self._spec_enabled else TRACE_HEIGHT_RATIO

        for i in range(n_plots):
            ax = self._fig.add_subplot(
                n_plots, 1, i + 1,
                # share x across all subplots for zoom coherence
                sharex=self._axes[0] if i > 0 else None,
            )
            self._axes.append(ax)

        # ── style the trace axes ───────────────────────────────────────────
        for i, (name, unit, (lo, hi)) in enumerate(
                zip(self._ef.ch_names, self._ef.ch_units, self._ylimits)):
            ax = self._axes[i]
            ax.set_ylabel(f"{name}\n({unit})", fontsize=8)
            ax.set_ylim(lo, hi)
            ax.grid(True, lw=0.3, alpha=0.4)
            ax.tick_params(labelsize=7)
            # Hide x tick labels on all but the bottom plot
            if i < n_plots - 1:
                ax.tick_params(labelbottom=False)
            line, = ax.plot([], [], lw=0.6, color="steelblue")
            self._trace_lines.append(line)

        # ── style the spectrogram axis (if present) ────────────────────────
        if self._spec_enabled:
            self._spec_ax = self._axes[-1]
            self._spec_ax.set_ylabel("Freq (Hz)", fontsize=8)
            self._spec_ax.set_xlabel("Time (s)", fontsize=8)
            self._spec_ax.tick_params(labelsize=7)
        else:
            # Bottom trace axis gets the x label
            self._axes[-1].set_xlabel("Time (s)", fontsize=8)

        # Apply height ratios via gridspec
        self._fig.subplots_adjust(hspace=0.08)
        self._apply_height_ratios(ratios, n_plots)

        self._mpl_canvas.draw_idle()

    def _apply_height_ratios(self, ratios: list, n_plots: int):
        """
        Reposition subplot axes according to height ratios.
        GridSpec is the cleanest way but requires rebuilding via subplots;
        we instead manually set axes positions after creation.
        """
        if len(ratios) != n_plots:
            return

        total   = sum(ratios)
        pad_top = 0.97
        pad_bot = 0.08 if self._spec_enabled else 0.06
        hspace  = 0.03          # gap between subplots (figure fraction)
        usable  = pad_top - pad_bot - hspace * (n_plots - 1)

        bottoms = []
        current = pad_bot
        for r in reversed(ratios):
            bottoms.insert(0, current)
            current += r / total * usable + hspace

        left, right = 0.10, 0.98
        for ax, bot, r in zip(self._axes, bottoms, ratios):
            h = r / total * usable
            ax.set_position([left, bot, right - left, h])

    # =========================================================================
    # Drawing
    # =========================================================================

    def _redraw(self):
        if not self._ef:
            return
        self._redraw_traces()
        if self._spec_enabled:
            self._redraw_spectrogram()
        self._mpl_canvas.draw_idle()

    def _redraw_traces(self):
        sweep  = self._current_sweep
        dec    = max(1, self._decimate_var.get())
        sr     = self._ef.sample_rate
        win    = self._window_var.get()
        t0     = self._t_offset
        start  = int(t0 * sr)
        stop   = min(int((t0 + win) * sr), self._ef.n_samples)

        for i, (ax, line) in enumerate(zip(self._axes, self._trace_lines)):
            sig     = self._get_chunk(sweep, i, start, stop)
            sig_dec = sig[::dec]
            t_dec   = t0 + np.arange(len(sig_dec)) * (dec / sr)
            line.set_data(t_dec, sig_dec)
            ax.set_xlim(t0, t0 + win)

    def _redraw_spectrogram(self):
        """
        Recompute the spectrogram only when the sample window has changed.
        Otherwise the existing pcolormesh is already correct — draw_idle()
        in _redraw() will repaint it without any extra FFT work.

        To avoid a trailing blank strip at the right edge (caused by scipy
        needing at least nperseg samples to form the final FFT window), we
        fetch an extra nperseg samples beyond the visible window for the FFT
        input only.  The display xlim is still clipped to [t0, t0+win] so
        the user never sees the overhang.
        """
        if not self._spec_ax:
            return

        from spectrogram import DEFAULT_NPERSEG

        sweep  = self._current_sweep
        sr     = self._ef.sample_rate
        win    = self._window_var.get()
        t0     = self._t_offset
        start  = int(t0 * sr)
        stop   = min(int((t0 + win) * sr), self._ef.n_samples)

        if start == self._spec_start and stop == self._spec_stop:
            return  # cache still valid

        # Fetch extra samples so the FFT can fill the trailing edge.
        # The overhang is invisible — xlim clips it out below.
        stop_fft = min(stop + DEFAULT_NPERSEG, self._ef.n_samples)

        ac_idx = ac_channel_index(self._ef.n_channels)
        signal = self._get_chunk(sweep, ac_idx, start, stop_fft)
        r      = compute_spectrogram(signal, sr, t_start=t0)

        self._spec_result = r
        self._spec_start  = start
        self._spec_stop   = stop          # keyed on the *display* window

        # Redraw the spectrogram axes in place (no figure.clear() —
        # that would destroy the trace axes too).
        self._spec_ax.cla()
        t_abs = r.t_start + r.times
        self._spec_ax.pcolormesh(
            t_abs, r.freqs, r.power_db,
            shading="auto", cmap="inferno",
        )
        self._spec_ax.set_ylabel("Freq (Hz)", fontsize=8)
        self._spec_ax.set_xlabel("Time (s)",  fontsize=8)
        self._spec_ax.set_ylim(r.freq_min, r.freq_max)
        # Clip to the visible window — hides any FFT overhang on both edges.
        self._spec_ax.set_xlim(t0, t0 + win)
        self._spec_ax.tick_params(labelsize=7)

    # =========================================================================
    # Spectrogram cache invalidation
    # =========================================================================

    def _invalidate_spec_cache(self):
        self._spec_result = None
        self._spec_start  = -1
        self._spec_stop   = -1

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
    # Toolbar toggle
    # =========================================================================

    def _toggle_spectrogram(self):
        enabled = not self._spec_enabled
        self._spec_btn.config(text="- Spectrogram" if enabled else "+ Spectrogram")
        self.set_spectrogram_enabled(enabled)

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
            self._invalidate_spec_cache()
            self._redraw()

    def _on_cache_resize(self, *_):
        self._cache.resize(max(2, self._cache_size_var.get()))