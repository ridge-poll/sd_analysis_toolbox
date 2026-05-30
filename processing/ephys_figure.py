#!/usr/bin/env python3
"""
ephys_figure.py
---------------
Generates a clean ephys figure (DC + AC traces) with vertical lines
marking the given event timestamps.

Usage (standalone):
    python ephys_figure.py <h5_file> <start_time> <stop_time> <ts1> [ts2 ...] [--save path]

    start_time / stop_time : seconds (float) defining the display window
    ts1, ts2, ...          : integer event timestamps (seconds) to mark

Called programmatically by master.py via plot_ephys_figure().
"""

# ── PARAMETERS ────────────────────────────────────────────────────────────────
DECIMATE        = 10          # keep every Nth sample for plotting speed
LINE_COLORS = [
    "#e6194b",  # red
    "#f58231",  # orange
    "#ffe119",  # yellow
    "#3cb44b",  # green
    "#4363d8",  # blue
    "#911eb4",  # violet
    "#f032e6",  # magenta
    "#ffffff",  # white
]
LINE_ALPHA      = 0.85
LINE_WIDTH      = 1.2
TRACE_COLOR     = "steelblue"
TRACE_LW        = 0.7
FIG_SIZE        = (12, 5)     # inches
BG_COLOR        = "#1a1a2e"
AXES_COLOR      = "#16213e"
GRID_COLOR      = "#2a2a4a"
TEXT_COLOR      = "#e0e0e0"

LINE_ALPHA      = 0.85
LINE_WIDTH      = 1.2
TRACE_COLOR     = "steelblue"
TRACE_LW        = 0.7
FIG_SIZE        = (12, 5)
BG_COLOR        = "#ffffff"
AXES_COLOR      = "#ffffff"
GRID_COLOR      = "#e0e0e0"
TEXT_COLOR      = "#000000"
# ──────────────────────────────────────────────────────────────────────────────

import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import h5py


class EphysFile:
    """
    Wraps a WaveSurfer HDF5 file and exposes:
      - metadata  (sample_rate, channel names/units/scales, sweeps, duration)
      - read_chunk(sweep, ch_idx, start, stop) -> float32 array (scaled)
      - scan_ylimits(sweep)                    -> [(lo, hi), ...]
    
    ch_idx is always a 0-based index into the *active* channels only.
    """

    def __init__(self, path: str):
        self.path = path
        self._f   = h5py.File(path, "r")

        hdr = self._f["header"]

        self.sample_rate: float = float(hdr["AcquisitionSampleRate"][0, 0])

        raw_scales  = hdr["AIChannelScales"][:, 0].astype(np.float32)
        all_names   = [n.decode() for n in hdr["AIChannelNames"][:]]
        all_units   = [u.decode() for u in hdr["AIChannelUnits"][:]]
        active_mask = hdr["IsAIChannelActive"][:, 0].astype(bool)

        # active_idx  : original channel indices that are active
        # data_rows   : 0-based row index in analogScans for each active channel
        self.active_idx: np.ndarray = np.where(active_mask)[0]
        self.ch_names:   list[str]  = [all_names[i] for i in self.active_idx]
        self.ch_units:   list[str]  = [all_units[i] for i in self.active_idx]
        self.ch_scales:  np.ndarray = raw_scales[self.active_idx]
        self.data_rows:  list[int]  = list(range(len(self.active_idx)))
        self.n_channels: int        = len(self.active_idx)

        self.sweeps: list[str] = sorted(
            k for k in self._f.keys() if k.startswith("sweep_")
        )
        self.n_samples: int = self._f[f"{self.sweeps[0]}/analogScans"].shape[1]
        self.duration:  float = self.n_samples / self.sample_rate

    # ── public API ────────────────────────────────────────────────────────

    def read_chunk(self, sweep: str, ch_idx: int,
                   start: int, stop: int) -> np.ndarray:
        """
        Return a float32 array of scaled voltage values for channel ch_idx
        (0-based active-channel index) between sample indices [start, stop).
        """
        row   = self.data_rows[ch_idx]
        scale = self.ch_scales[ch_idx]
        raw   = self._f[f"{sweep}/analogScans"][row, start:stop]
        return raw.astype(np.float32) * scale

    def scan_ylimits(self, sweep: str,
                     n_probe: int = 2000) -> list[tuple[float, float]]:
        """
        Sparse probe across the recording to estimate per-channel y-limits.
        Returns [(lo, hi), ...] for each active channel.
        """
        n   = self._f[f"{sweep}/analogScans"].shape[1]
        idx = np.linspace(0, n - 1, min(n_probe, n), dtype=int)
        limits = []
        for row, scale in zip(self.data_rows, self.ch_scales):
            vals   = self._f[f"{sweep}/analogScans"][row, :][idx].astype(np.float32) * scale
            mn, mx = float(vals.min()), float(vals.max())
            margin = max((mx - mn) * 0.1, 0.05)
            limits.append((mn - margin, mx + margin))
        return limits

    def close(self):
        self._f.close()

    # ── context manager support ───────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()



def plot_ephys_figure(h5_path: str,
                      start_time: float,
                      stop_time: float,
                      timestamps: list[int],
                      save_path: str | None = None):
    """
    Parameters
    ----------
    h5_path    : path to WaveSurfer HDF5 file
    start_time : left edge of display window (seconds)
    stop_time  : right edge of display window (seconds)
    timestamps : list of integer event times (seconds) to draw as vlines
    save_path  : if given, save figure there; otherwise call plt.show()
    """
    ef = EphysFile(h5_path)
    sweep = ef.sweeps[0]
    sr    = ef.sample_rate

    start_samp = int(start_time * sr)
    stop_samp  = min(int(stop_time  * sr), ef.n_samples)

    # ── build figure ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=FIG_SIZE, facecolor=BG_COLOR)
    gs  = gridspec.GridSpec(ef.n_channels, 1, figure=fig,
                            hspace=0.08, top=0.92, bottom=0.10,
                            left=0.09, right=0.98)
    axes = [fig.add_subplot(gs[i]) for i in range(ef.n_channels)]

    for i, ax in enumerate(axes):
        raw  = ef.read_chunk(sweep, i, start_samp, stop_samp)
        dec  = max(1, DECIMATE)
        sig  = raw[::dec]
        t    = start_time + np.arange(len(sig)) * (dec / sr)

        ax.set_facecolor(AXES_COLOR)
        ax.plot(t, sig, lw=TRACE_LW, color=TRACE_COLOR)
        ax.set_xlim(start_time, stop_time)
        ax.grid(True, color=GRID_COLOR, lw=0.4, alpha=0.6)
        ax.tick_params(colors=TEXT_COLOR, labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)
        ax.set_ylabel(f"{ef.ch_names[i]}\n({ef.ch_units[i]})",
                      color=TEXT_COLOR, fontsize=8)

        # vertical event lines
        for j, ts in enumerate(timestamps):
            if start_time <= ts <= stop_time:
                color = LINE_COLORS[j % len(LINE_COLORS)]
                ax.axvline(x=ts, color=color, lw=LINE_WIDTH,
                           alpha=LINE_ALPHA, zorder=5)

        if i < ef.n_channels - 1:
            ax.tick_params(labelbottom=False)

    axes[-1].set_xlabel("Time (s)", color=TEXT_COLOR, fontsize=9)
    fig.suptitle("Electrophysiology traces", color=TEXT_COLOR,
                 fontsize=10, y=0.97)

    ef.close()

    if save_path:
        fig.savefig(save_path, dpi=150, facecolor=BG_COLOR)
        print(f"Ephys figure saved -> {save_path}")
        plt.close(fig)
    else:
        plt.show()


# ── CLI entry point ───────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    save_path = None
    if "--save" in args:
        idx = args.index("--save")
        if idx + 1 >= len(args):
            print("Error: --save requires a path argument")
            sys.exit(1)
        save_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if len(args) < 3:
        print("Usage: python ephys_figure.py <h5_file> <start_time> <stop_time> "
              "[ts1 ts2 ...] [--save path]")
        sys.exit(1)

    h5_path    = args[0]
    start_time = float(args[1])
    stop_time  = float(args[2])
    timestamps = []
    for a in args[3:]:
        try:
            timestamps.append(int(a))
        except ValueError:
            print(f"Error: timestamp '{a}' is not an integer.")
            sys.exit(1)

    plot_ephys_figure(h5_path, start_time, stop_time, timestamps, save_path)

if __name__ == "__main__":
    main()