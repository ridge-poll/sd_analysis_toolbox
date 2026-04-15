"""
models/event.py
---------------
Core Event dataclass. Intentionally has no GUI or I/O dependencies —
it is a pure data structure that the rest of the system builds on.

Fields
------
id          : str       Stable UUID (uuid4 hex). Assigned at creation;
                        preserved across save/load cycles so external
                        references remain valid.
start_time  : float     Onset time in seconds on the ephys timeline.
end_time    : float | None
                        Offset time in seconds, or None for a point event
                        (onset only, duration unknown/irrelevant).
label       : str       Free-text annotation. Empty string by default.
source      : str       One of "manual" | "suggested" | "imported".
                        - "manual"    : created interactively by the user
                        - "suggested" : produced by a detection algorithm;
                                        not yet accepted by the user
                        - "imported"  : loaded from an external annotations
                                        file (preserves provenance on
                                        round-trips without silently
                                        promoting foreign events to manual)

Usage
-----
>>> from models.event import Event
>>> e = Event(start_time=12.4, end_time=45.1, label="SD1")
>>> e.duration
32.7
>>> d = e.to_dict()
>>> e2 = Event.from_dict(d)
>>> e2 == e
True
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

# Valid source values — kept as a frozenset so validation is O(1)
VALID_SOURCES = frozenset({"manual", "suggested", "imported"})


def _new_id() -> str:
    """Generate a fresh UUID4 hex string."""
    return uuid.uuid4().hex


@dataclass
class Event:
    """
    A single annotated event on the ephys/TIFF timeline.

    Parameters
    ----------
    start_time : float
        Event onset in seconds (ephys timeline).
    end_time : float or None, optional
        Event offset in seconds, or None if this is a point event.
    label : str, optional
        Human-readable annotation string.
    source : str, optional
        Provenance tag: "manual", "suggested", or "imported".
    id : str, optional
        UUID hex string. Auto-generated if not supplied.
    """

    start_time: float
    end_time:   Optional[float]  = None
    label:      str              = ""
    source:     str              = "manual"
    id:         str              = field(default_factory=_new_id)

    # ------------------------------------------------------------------
    # Post-init validation
    # ------------------------------------------------------------------

    def __post_init__(self):
        if not isinstance(self.start_time, (int, float)):
            raise TypeError(f"start_time must be numeric, got {type(self.start_time)}")
        if self.end_time is not None:
            if not isinstance(self.end_time, (int, float)):
                raise TypeError(f"end_time must be numeric or None, got {type(self.end_time)}")
            if self.end_time < self.start_time:
                raise ValueError(
                    f"end_time ({self.end_time}) must be >= start_time ({self.start_time})")
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(VALID_SOURCES)}, got {self.source!r}")

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def duration(self) -> Optional[float]:
        """Duration in seconds, or None for point events."""
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    @property
    def is_interval(self) -> bool:
        """True if this event has a defined end time."""
        return self.end_time is not None

    @property
    def is_point(self) -> bool:
        """True if this event has no end time (onset only)."""
        return self.end_time is None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """
        Serialize to a plain dict suitable for JSON encoding.

        Returns
        -------
        dict with keys: id, start, end, label, source
        """
        return {
            "id":     self.id,
            "start":  self.start_time,
            "end":    self.end_time,      # None serializes to JSON null
            "label":  self.label,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        """
        Reconstruct an Event from a dict produced by to_dict().

        Missing keys use sensible defaults so that older annotation files
        (which may not have all fields) still load cleanly.
        """
        return cls(
            start_time = float(d["start"]),
            end_time   = float(d["end"]) if d.get("end") is not None else None,
            label      = str(d.get("label", "")),
            source     = str(d.get("source", "imported")),
            id         = str(d.get("id", _new_id())),
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        end_str = f"{self.end_time:.3f}" if self.end_time is not None else "None"
        return (
            f"Event(start={self.start_time:.3f}, end={end_str}, "
            f"label={self.label!r}, source={self.source!r}, id={self.id[:8]}…)"
        )