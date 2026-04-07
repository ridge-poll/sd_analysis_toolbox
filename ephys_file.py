"""
ephys_file.py
-------------
Data-access layer for WaveSurfer HDF5 ephys recordings.
No GUI dependencies — pure data.

Keeps the HDF5 file handle open for fast random access.
"""

import numpy as np
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
