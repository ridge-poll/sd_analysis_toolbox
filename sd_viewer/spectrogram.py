"""
spectrogram.py
--------------
Standalone spectrogram computation for LFP ephys data.
No GUI dependencies — pure signal processing.

Primary entry point:
    result = compute_spectrogram(signal, sample_rate)
    # result.freqs, result.times, result.power_db

All parameters have sensible LFP defaults but are fully exposed for tuning.
"""

import numpy as np
from dataclasses import dataclass
from scipy.signal import spectrogram as scipy_spectrogram


# ── tuneable defaults ─────────────────────────────────────────────────────────
DEFAULT_NPERSEG  = 1024   # FFT window length (samples). At 10kHz → 102.4 ms per window
DEFAULT_NOVERLAP = 768    # 75% overlap — smooth time axis without excess cost
DEFAULT_FREQ_MAX = 300.0  # Hz — LFP range ceiling
DEFAULT_FREQ_MIN = 0.0    # Hz
EPSILON          = 1e-12  # prevents log(0)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SpectrogramResult:
    """
    Output of compute_spectrogram().

    Attributes
    ----------
    freqs     : 1-D array of frequency bin centres (Hz)
    times     : 1-D array of time bin centres (s), relative to the start of
                the signal slice that was passed in
    power_db  : 2-D array (freqs × times) of power in dB
    t_start   : ephys timeline time (s) of the first sample in the slice —
                add to `times` to get absolute ephys timestamps
    nperseg   : FFT window length used
    noverlap  : overlap used
    freq_min  : lower frequency bound applied
    freq_max  : upper frequency bound applied
    """
    freqs:    np.ndarray
    times:    np.ndarray
    power_db: np.ndarray
    t_start:  float
    nperseg:  int
    noverlap: int
    freq_min: float
    freq_max: float


def compute_spectrogram(
    signal:      np.ndarray,
    sample_rate: float,
    t_start:     float = 0.0,
    nperseg:     int   = DEFAULT_NPERSEG,
    noverlap:    int   = DEFAULT_NOVERLAP,
    freq_min:    float = DEFAULT_FREQ_MIN,
    freq_max:    float = DEFAULT_FREQ_MAX,
) -> SpectrogramResult:
    """
    Compute a power spectrogram (dB) from a 1-D signal array.

    Parameters
    ----------
    signal      : 1-D float array — the raw (scaled) voltage trace
    sample_rate : samples per second
    t_start     : ephys time (s) of signal[0], stored in result for axis labelling
    nperseg     : FFT window length in samples (Hann window)
    noverlap    : number of samples overlapping between windows
    freq_min    : lower frequency bound to retain (Hz)
    freq_max    : upper frequency bound to retain (Hz)

    Returns
    -------
    SpectrogramResult
    """
    if noverlap >= nperseg:
        noverlap = nperseg - 1

    freqs, times, Sxx = scipy_spectrogram(
        signal,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        scaling="density",
        mode="psd",
    )

    # crop to requested frequency range
    mask   = (freqs >= freq_min) & (freqs <= freq_max)
    freqs  = freqs[mask]
    Sxx    = Sxx[mask, :]

    power_db = 10.0 * np.log10(Sxx + EPSILON)

    return SpectrogramResult(
        freqs    = freqs,
        times    = times,
        power_db = power_db,
        t_start  = t_start,
        nperseg  = nperseg,
        noverlap = noverlap,
        freq_min = freq_min,
        freq_max = freq_max,
    )


def ac_channel_index(n_channels: int) -> int:
    """
    Return the index of the AC channel.
    By convention the AC (unamplified LFP) channel is always plotted on the
    bottom, which corresponds to the last active channel (index -1 / n-1).
    """
    return n_channels - 1