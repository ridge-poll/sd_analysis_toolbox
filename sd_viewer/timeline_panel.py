"""
timeline_panel.py
-----------------
A horizontal matplotlib strip that shows the full recording duration,
a moving playhead cursor, and event overlays.

Sits between the ephys/TIFF panels and the bottom sync controls in the
main layout. Driven by SyncController via the existing on_tick mechanism.

Design rules
------------
- Reads from session.events and session.suggested_events — never mutates
  them directly. All mutations go through session CRUD methods so there
  is one place to hook observers later.
- Mouse interactions only work when a recording is loaded (max_time > 0).
- Left-click          → create point event at clicked time
- Shift + left-click  → close the last open (point) event by setting its
                        end_time; if no open event exists, creates a new
                        point event instead
- Right-click         → open label-edit dialog for the nearest event
                        within a tolerance window
- The panel redraws on every tick during playback and on every annotation
  change. Redraws are cheap: only two artists are updated (cursor line +
  event patches); the axes and background are not rebuilt.

Public API (called by main_gui)
-------------------------------
    panel.set_max_time(t)           — called when a file loads
    panel.update_cursor(t)          — called on every tick
    panel.refresh()                 — called after any session mutation
    panel.set_session(session)      — called once at startup
"""

from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import matplotlib.lines as mlines

from models.session import Session
from models.event import Event

# ── visual constants ──────────────────────────────────────────────────────────
PANEL_HEIGHT_IN   = 1.1      # figure height in inches
CURSOR_COLOR      = "#e74c3c"
MANUAL_COLOR      = "#2ecc71"
SUGGESTED_COLOR   = "#f39c12"
POINT_LINEWIDTH   = 1.8
INTERVAL_ALPHA    = 0.35
LABEL_FONTSIZE    = 7
CLICK_TOLERANCE_S = 2.0      # seconds — max distance for right-click hit test
# ─────────────────────────────────────────────────────────────────────────────


class TimelinePanel(tk.Frame):
    """
    Horizontal timeline strip with event overlays and mouse annotation.

    Parameters
    ----------
    parent : tk.Widget
    session : Session
    on_event_added : callable, optional
        Called with (Event,) after a new event is created.
    on_event_removed : callable, optional
        Called with (Event,) after an event is deleted.
    on_event_updated : callable, optional
        Called with (Event,) after a label or time is changed.
    kwargs : passed to tk.Frame
    """

    def __init__(
        self,
        parent,
        session: Session,
        on_event_added=None,
        on_event_removed=None,
        on_event_updated=None,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)

        self._session          = session
        self._max_time: float  = 0.0
        self._current_t: float = 0.0

        self._on_event_added   = on_event_added
        self._on_event_removed = on_event_removed
        self._on_event_updated = on_event_updated

        # artist handles — rebuilt in refresh(), mutated in update_cursor()
        self._cursor_line      = None
        self._event_artists    = []   # list of (artist, event) tuples

        self._build_ui()
        self._build_axes()

    # =========================================================================
    # Public API
    # =========================================================================

    def set_session(self, session: Session) -> None:
        """Swap the session (used if session is replaced at runtime)."""
        self._session = session
        self.refresh()

    def set_max_time(self, t: float) -> None:
        """Called when a recording is loaded. Resets the timeline axis."""
        self._max_time = max(0.0, t)
        self._current_t = 0.0
        self._ax.set_xlim(0.0, self._max_time if self._max_time > 0 else 1.0)
        self.refresh()

    def update_cursor(self, t: float) -> None:
        """
        Move the playhead to time t. Called on every SyncController tick.
        Fast path: only updates the cursor line, does not rebuild event artists.
        """
        self._current_t = t
        if self._cursor_line is not None:
            self._cursor_line.set_xdata([t, t])
            self._mpl_canvas.draw_idle()

    def refresh(self) -> None:
        """
        Rebuild all event artists from scratch and redraw.
        Call this after any session.events mutation.
        """
        self._draw_events()
        self._mpl_canvas.draw_idle()

    # =========================================================================
    # UI + axes construction
    # =========================================================================

    def _build_ui(self):
        self._fig = Figure(figsize=(10, PANEL_HEIGHT_IN), tight_layout=True)
        self._mpl_canvas = FigureCanvasTkAgg(self._fig, master=self)
        widget = self._mpl_canvas.get_tk_widget()
        widget.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        widget.bind("<Button-1>",         self._on_left_click)
        widget.bind("<Shift-Button-1>",   self._on_shift_click)
        widget.bind("<Button-2>",         self._on_right_click)   # macOS
        widget.bind("<Button-3>",         self._on_right_click)   # Windows/Linux

    def _build_axes(self):
        self._fig.clear()
        self._ax = self._fig.add_subplot(1, 1, 1)
        self._ax.set_xlim(0.0, max(self._max_time, 1.0))
        self._ax.set_ylim(0.0, 1.0)
        self._ax.set_yticks([])
        self._ax.set_xlabel("Time (s)", fontsize=8)
        self._ax.tick_params(axis="x", labelsize=7)
        self._ax.set_facecolor("#1a1a2e")
        self._fig.patch.set_facecolor("#1a1a2e")
        for spine in self._ax.spines.values():
            spine.set_edgecolor("#444466")

        # cursor line — drawn once, updated cheaply on every tick
        self._cursor_line = self._ax.axvline(
            x=self._current_t,
            color=CURSOR_COLOR,
            linewidth=1.5,
            zorder=10,
        )
        self._event_artists = []

    # =========================================================================
    # Event drawing
    # =========================================================================

    def _draw_events(self):
        """Remove old event artists and redraw all events from session."""
        for artist, _ in self._event_artists:
            artist.remove()
        self._event_artists = []

        # draw suggested events first (underneath manual ones)
        for ev in self._session.suggested_events:
            self._draw_single_event(ev, color=SUGGESTED_COLOR)

        for ev in self._session.events:
            self._draw_single_event(ev, color=MANUAL_COLOR)

        # re-draw cursor on top
        if self._cursor_line is not None:
            self._cursor_line.set_zorder(10)

    def _draw_single_event(self, ev: Event, color: str):
        if ev.is_interval:
            # filled rectangle spanning [start, end]
            rect = Rectangle(
                (ev.start_time, 0.05),
                ev.end_time - ev.start_time,
                0.9,
                linewidth=1.2,
                edgecolor=color,
                facecolor=color,
                alpha=INTERVAL_ALPHA,
                zorder=3,
            )
            self._ax.add_patch(rect)
            self._event_artists.append((rect, ev))

            # start edge line
            line = mlines.Line2D(
                [ev.start_time, ev.start_time], [0.05, 0.95],
                color=color, linewidth=POINT_LINEWIDTH, zorder=4,
            )
            self._ax.add_line(line)
            self._event_artists.append((line, ev))

            # end edge line
            line2 = mlines.Line2D(
                [ev.end_time, ev.end_time], [0.05, 0.95],
                color=color, linewidth=POINT_LINEWIDTH, zorder=4,
            )
            self._ax.add_line(line2)
            self._event_artists.append((line2, ev))

            # label centred in the interval, near the top
            if ev.label:
                mid = (ev.start_time + ev.end_time) / 2.0
                txt = self._ax.text(
                    mid, 0.78, ev.label,
                    ha="center", va="bottom",
                    fontsize=LABEL_FONTSIZE,
                    color=color, zorder=5,
                    clip_on=True,
                )
                self._event_artists.append((txt, ev))
        else:
            # point event — single vertical line
            line = mlines.Line2D(
                [ev.start_time, ev.start_time], [0.05, 0.95],
                color=color, linewidth=POINT_LINEWIDTH, zorder=4,
            )
            self._ax.add_line(line)
            self._event_artists.append((line, ev))

            if ev.label:
                txt = self._ax.text(
                    ev.start_time, 0.78, ev.label,
                    ha="center", va="bottom",
                    fontsize=LABEL_FONTSIZE,
                    color=color, zorder=5,
                    clip_on=True,
                )
                self._event_artists.append((txt, ev))

    # =========================================================================
    # Coordinate conversion
    # =========================================================================

    def _pixel_to_time(self, x_pixel: int) -> float | None:
        """
        Convert a canvas pixel x-coordinate to a time in seconds.
        Returns None if the axes are not yet sized or no recording is loaded.
        """
        if self._max_time <= 0:
            return None
        try:
            # get axes bounding box in display (pixel) coords
            bbox = self._ax.get_window_extent(
                renderer=self._fig.canvas.get_renderer()
            )
        except Exception:
            return None

        if bbox.width == 0:
            return None

        # matplotlib pixel origin is bottom-left; tk is top-left
        fig_h    = self._fig.get_figheight() * self._fig.dpi
        y_flip   = fig_h - x_pixel          # we only need x, name is misleading
        # correct: just use x directly
        frac     = (x_pixel - bbox.x0) / bbox.width
        t        = self._ax.get_xlim()[0] + frac * (
            self._ax.get_xlim()[1] - self._ax.get_xlim()[0]
        )
        return max(0.0, min(float(t), self._max_time))

    # =========================================================================
    # Mouse interactions
    # =========================================================================

    def _on_left_click(self, event):
        """Left-click: create a point event at the clicked time."""
        t = self._pixel_to_time(event.x)
        if t is None:
            return
        ev = self._session.add_event(start_time=t, end_time=None, label="")
        self.refresh()
        if self._on_event_added:
            self._on_event_added(ev)

    def _on_shift_click(self, event):
        """
        Shift+left-click: close the most recent open (point) event by
        setting its end_time to the clicked time.
        If no open event exists, creates a new point event instead.
        """
        t = self._pixel_to_time(event.x)
        if t is None:
            return

        # find the most recent point event whose start_time < t
        open_events = [
            e for e in self._session.events
            if e.is_point and e.start_time < t
        ]

        if open_events:
            # close the most recently started one
            target = max(open_events, key=lambda e: e.start_time)
            self._session.update_event(target, end_time=t)
            self.refresh()
            if self._on_event_updated:
                self._on_event_updated(target)
        else:
            # no open event to close — create a new point event
            ev = self._session.add_event(start_time=t, end_time=None, label="")
            self.refresh()
            if self._on_event_added:
                self._on_event_added(ev)

    def _on_right_click(self, event):
        """
        Right-click: open a label-edit dialog for the nearest event
        within CLICK_TOLERANCE_S. If no event is nearby, do nothing.
        """
        t = self._pixel_to_time(event.x)
        if t is None:
            return

        target = self._nearest_event(t)
        if target is None:
            return

        self._open_label_dialog(target)

    def _nearest_event(self, t: float) -> Event | None:
        """
        Return the event closest to time t (by start_time), within
        CLICK_TOLERANCE_S, or None if no event is close enough.
        """
        all_events = list(self._session.events) + list(self._session.suggested_events)
        if not all_events:
            return None

        def dist(ev):
            # For intervals, use the closer of the two edges
            if ev.is_interval:
                return min(abs(t - ev.start_time), abs(t - ev.end_time))
            return abs(t - ev.start_time)

        closest = min(all_events, key=dist)

        # Convert tolerance from seconds to something meaningful regardless
        # of zoom — use raw seconds since the axis is always full-duration
        if dist(closest) <= CLICK_TOLERANCE_S:
            return closest
        return None

    def _open_label_dialog(self, event: Event):
        """Open a simple dialog to edit the label (and optionally delete) an event."""
        dialog = _EventEditDialog(self, event)
        self.wait_window(dialog)

        if dialog.result == "delete":
            self._session.remove_event(event)
            self.refresh()
            if self._on_event_removed:
                self._on_event_removed(event)
        elif dialog.result == "save":
            self._session.update_event(event, label=dialog.new_label)
            self.refresh()
            if self._on_event_updated:
                self._on_event_updated(event)


# ─────────────────────────────────────────────────────────────────────────────
# Label-edit dialog
# ─────────────────────────────────────────────────────────────────────────────

class _EventEditDialog(tk.Toplevel):
    """
    Modal dialog for editing an event's label or deleting the event.

    After wait_window(dialog):
        dialog.result  : "save" | "delete" | "cancel"
        dialog.new_label : str (only meaningful if result == "save")
    """

    def __init__(self, parent, event: Event):
        super().__init__(parent)
        self.title("Edit Event")
        self.resizable(False, False)
        self.grab_set()         # make modal

        self.result    : str = "cancel"
        self.new_label : str = event.label

        # ── info row ──────────────────────────────────────────────────────
        info = (
            f"Start: {event.start_time:.3f} s"
            + (f"    End: {event.end_time:.3f} s" if event.is_interval else "  (point event)")
            + f"    Source: {event.source}"
        )
        tk.Label(self, text=info, fg="gray", font=("TkDefaultFont", 8)
                 ).pack(padx=12, pady=(10, 2))

        # ── label entry ───────────────────────────────────────────────────
        tk.Label(self, text="Label:").pack(padx=12, anchor=tk.W)
        self._label_var = tk.StringVar(value=event.label)
        entry = tk.Entry(self, textvariable=self._label_var, width=32)
        entry.pack(padx=12, pady=(2, 10))
        entry.focus_set()
        entry.select_range(0, tk.END)
        entry.bind("<Return>", lambda _: self._save())

        # ── buttons ───────────────────────────────────────────────────────
        btn_frame = tk.Frame(self)
        btn_frame.pack(padx=12, pady=(0, 10))
        tk.Button(btn_frame, text="Save",   width=8,
                  command=self._save).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="Delete", width=8, fg="red",
                  command=self._delete).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="Cancel", width=8,
                  command=self._cancel).pack(side=tk.LEFT, padx=4)

        self.bind("<Escape>", lambda _: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # centre over parent
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{px}+{py}")

    def _save(self):
        self.new_label = self._label_var.get().strip()
        self.result    = "save"
        self.destroy()

    def _delete(self):
        self.result = "delete"
        self.destroy()

    def _cancel(self):
        self.result = "cancel"
        self.destroy()