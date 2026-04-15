"""
models/session.py
-----------------
Session is the single source of truth for all data associated with one
recording session: which files are loaded, what the time offset is, and
the complete list of user-confirmed events.

Design rules
------------
- No GUI imports. No I/O imports. Pure data + logic only.
- Session does not load files; it records which files are loaded.
- CRUD methods are the only way to mutate the events list, so that any
  future observer/callback pattern has a single place to hook into.
- suggested_events is kept separate from events so detection output can
  never silently contaminate the confirmed annotation list.

Usage
-----
>>> from models.session import Session
>>> from models.event import Event
>>> s = Session()
>>> e = s.add_event(start_time=10.0, end_time=45.0, label="SD1")
>>> s.update_event(e, label="SD1 confirmed")
>>> s.remove_event(e)
>>> len(s.events)
0
"""

from __future__ import annotations

from typing import Optional

from models.event import Event


class Session:
    """
    Holds all state for a single viewer session.

    Attributes
    ----------
    ephys_path : str or None
        Absolute path to the loaded HDF5 ephys file.
    tiff_folder : str or None
        Absolute path to the loaded TIFF folder.
    tiff_offset : float
        Seconds into the ephys recording when the first TIFF frame was
        captured. Mirrors SyncController._offset; updated whenever the
        user changes the offset field.
    events : list[Event]
        User-confirmed annotations, in insertion order.
    suggested_events : list[Event]
        Algorithm-generated event candidates. Never mixed into `events`
        until the user explicitly accepts them.
    """

    def __init__(self):
        self.ephys_path:       Optional[str]   = None
        self.tiff_folder:      Optional[str]   = None
        self.tiff_offset:      float           = 0.0

        self._events:          list[Event]     = []
        self._suggested:       list[Event]     = []

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    @property
    def events(self) -> list[Event]:
        """Confirmed events, sorted by start_time."""
        return sorted(self._events, key=lambda e: e.start_time)

    @property
    def suggested_events(self) -> list[Event]:
        """Algorithm-suggested events, sorted by start_time."""
        return sorted(self._suggested, key=lambda e: e.start_time)

    # ------------------------------------------------------------------
    # CRUD — confirmed events
    # ------------------------------------------------------------------

    def add_event(
        self,
        start_time: float,
        end_time:   Optional[float] = None,
        label:      str             = "",
        source:     str             = "manual",
    ) -> Event:
        """
        Create and store a new confirmed event.

        Parameters
        ----------
        start_time : float
            Onset in seconds (ephys timeline).
        end_time : float or None
            Offset in seconds, or None for a point event.
        label : str
            Optional annotation string.
        source : str
            Provenance: "manual" or "imported".

        Returns
        -------
        Event
            The newly created event (has a fresh id assigned).
        """
        e = Event(start_time=start_time, end_time=end_time,
                  label=label, source=source)
        self._events.append(e)
        return e

    def remove_event(self, event: Event) -> bool:
        """
        Remove a confirmed event by identity.

        Returns True if the event was found and removed, False otherwise.
        """
        try:
            self._events.remove(event)
            return True
        except ValueError:
            return False

    def update_event(
        self,
        event:      Event,
        start_time: Optional[float] = None,
        end_time:   Optional[float] = None,
        label:      Optional[str]   = None,
        source:     Optional[str]   = None,
    ) -> Event:
        """
        Update fields on an existing confirmed event in-place.

        Only supplied (non-None) keyword arguments are applied.
        Validation is re-run by reconstructing a temporary Event so that
        invariants (e.g. end >= start) are always enforced.

        Returns the modified event.

        Raises
        ------
        ValueError
            If the event is not in this session's event list.
        """
        if event not in self._events:
            raise ValueError("Event is not part of this session.")

        new_start  = start_time if start_time is not None else event.start_time
        new_end    = end_time   if end_time   is not None else event.end_time
        new_label  = label      if label      is not None else event.label
        new_source = source     if source     is not None else event.source

        # Validate by constructing a temporary Event (raises on bad values)
        Event(start_time=new_start, end_time=new_end,
              label=new_label, source=new_source)

        event.start_time = new_start
        event.end_time   = new_end
        event.label      = new_label
        event.source     = new_source
        return event

    def get_event_by_id(self, event_id: str) -> Optional[Event]:
        """Return the confirmed event with the given id, or None."""
        for e in self._events:
            if e.id == event_id:
                return e
        return None

    # ------------------------------------------------------------------
    # CRUD — suggested events
    # ------------------------------------------------------------------

    def set_suggested_events(self, suggestions: list[Event]) -> None:
        """
        Replace the entire suggested events list.
        Called by detection algorithms after a run completes.
        All events must have source="suggested".
        """
        for e in suggestions:
            if e.source != "suggested":
                raise ValueError(
                    f"Expected source='suggested', got {e.source!r} "
                    f"for event {e!r}")
        self._suggested = list(suggestions)

    def accept_suggestion(self, event: Event) -> Event:
        """
        Promote a suggested event to confirmed, changing its source to
        "manual". Returns the now-confirmed event.

        Raises
        ------
        ValueError
            If the event is not in the suggested list.
        """
        if event not in self._suggested:
            raise ValueError("Event is not in the suggested list.")
        self._suggested.remove(event)
        event.source = "manual"
        self._events.append(event)
        return event

    def reject_suggestion(self, event: Event) -> bool:
        """
        Discard a suggested event. Returns True if found and removed.
        """
        try:
            self._suggested.remove(event)
            return True
        except ValueError:
            return False

    def clear_suggestions(self) -> None:
        """Discard all pending suggestions."""
        self._suggested.clear()

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------

    def clear_events(self) -> None:
        """Remove all confirmed events."""
        self._events.clear()

    def replace_events(self, events: list[Event]) -> None:
        """
        Replace the confirmed event list wholesale.
        Used by load_json to restore a saved session.
        """
        self._events = list(events)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def has_data(self) -> bool:
        """True if at least one data file is loaded."""
        return self.ephys_path is not None or self.tiff_folder is not None

    def __repr__(self) -> str:
        return (
            f"Session(ephys={self.ephys_path!r}, tiff={self.tiff_folder!r}, "
            f"events={len(self._events)}, suggested={len(self._suggested)})"
        )