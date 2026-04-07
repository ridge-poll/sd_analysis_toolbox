"""
sync_controller.py
------------------
Master clock that drives EphysPanel and TiffPanel in sync.

Responsibilities:
  - Own the single source-of-truth current time (ephys timeline, seconds)
  - Apply the fixed offset when calling TiffPanel:
        t_tiff = t_ephys - tiff_offset
    where tiff_offset = "seconds into the ephys recording when the first
    TIFF frame was captured". Positive means TIFFs started after ephys.
  - Run the playback loop via Tkinter's after() — no threads
  - Expose seek / play / pause / stop to main_gui

Tick rate: ~30 ms (~33 fps). The ephys panel redraws every tick.
The TIFF panel only redraws when the frame index changes (handled internally).

Public API (called by main_gui):
    ctrl.play()
    ctrl.pause()
    ctrl.seek(t)
    ctrl.set_offset(seconds)
    ctrl.set_speed(multiplier)
    ctrl.register_on_tick(callback)   # main_gui uses this to update the slider
"""

import time
import tkinter as tk

TICK_MS     = 30       # playback timer interval in milliseconds
TICK_SEC    = TICK_MS / 1000.0
DEFAULT_SPEED = 1.0


class SyncController:
    """
    Parameters
    ----------
    root : tk.Tk
        The Tkinter root — needed only for after() scheduling.
    ephys_panel : EphysPanel
        Must implement show_at_time(t: float).
    tiff_panel : TiffPanel
        Must implement show_at_time(t: float).
    tiff_offset : float
        Seconds into the ephys recording when the first TIFF frame was
        captured. Set once at startup; adjustable via set_offset().
    """

    def __init__(self, root: tk.Tk, ephys_panel, tiff_panel,
                 tiff_offset: float = 0.0):
        self._root        = root
        self._ephys       = ephys_panel
        self._tiff        = tiff_panel
        self._offset      = tiff_offset

        self._t:          float = 0.0           # current time, ephys timeline (s)
        self._playing:    bool  = False
        self._speed:      float = DEFAULT_SPEED
        self._after_id:   str | None = None

        # wall-clock reference for drift-corrected playback
        self._play_start_wall:  float = 0.0
        self._play_start_t:     float = 0.0

        # optional tick callback — main_gui registers this to update slider
        self._on_tick_cb = None

        # max time is set when a file is loaded
        self._max_t: float = 0.0

    # =========================================================================
    # Public API
    # =========================================================================

    def play(self):
        if self._playing:
            return
        self._playing          = True
        self._play_start_wall  = time.monotonic()
        self._play_start_t     = self._t
        self._schedule_tick()

    def pause(self):
        self._playing = False
        if self._after_id:
            self._root.after_cancel(self._after_id)
            self._after_id = None

    def seek(self, t: float):
        """Jump to time t (ephys seconds). Works during playback or pause."""
        self._t = max(0.0, min(t, self._max_t))
        # reset drift reference so playback continues smoothly from new position
        self._play_start_wall = time.monotonic()
        self._play_start_t    = self._t
        self._update_panels()

    def set_offset(self, offset: float):
        """Update the TIFF offset (seconds). Takes effect immediately."""
        self._offset = offset
        self._update_panels()

    def set_speed(self, multiplier: float):
        """Set playback speed (1.0 = real time). Resets drift reference."""
        self._speed           = max(0.1, multiplier)
        self._play_start_wall = time.monotonic()
        self._play_start_t    = self._t

    def set_max_time(self, max_t: float):
        """Called by main_gui when ephys file is loaded."""
        self._max_t = max(0.0, max_t)

    def register_on_tick(self, callback):
        """
        Register a callback invoked on every tick with the current time (float).
        main_gui uses this to keep the shared scrubber in sync.
        """
        self._on_tick_cb = callback

    @property
    def current_time(self) -> float:
        return self._t

    @property
    def is_playing(self) -> bool:
        return self._playing

    # =========================================================================
    # Internal playback loop
    # =========================================================================

    def _schedule_tick(self):
        self._after_id = self._root.after(TICK_MS, self._tick)

    def _tick(self):
        if not self._playing:
            return

        # drift-corrected time: use wall clock rather than accumulated after() delays
        elapsed   = (time.monotonic() - self._play_start_wall) * self._speed
        new_t     = self._play_start_t + elapsed

        if new_t >= self._max_t:
            new_t         = self._max_t
            self._t       = new_t
            self._update_panels()
            self.pause()
            return

        self._t = new_t
        self._update_panels()
        self._schedule_tick()

    def _update_panels(self):
        """Push current time to both panels and fire the tick callback."""
        self._ephys.show_at_time(self._t)

        t_tiff = self._t - self._offset
        self._tiff.show_at_time(t_tiff)

        if self._on_tick_cb:
            self._on_tick_cb(self._t)
