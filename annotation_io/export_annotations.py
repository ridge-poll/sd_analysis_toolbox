"""
io/export_annotations.py
------------------------
Save and load event annotations as JSON.

File format
-----------
{
    "version": 1,
    "ephys_path":   "/path/to/recording.h5"  | null,
    "tiff_folder":  "/path/to/tiff_folder"   | null,
    "tiff_offset":  0.0,
    "events": [
        {"id": "...", "start": 12.4, "end": 45.1, "label": "SD1", "source": "manual"},
        {"id": "...", "start": 88.0, "end": null,  "label": "",    "source": "manual"},
        ...
    ]
}

Notes
-----
- Only confirmed events (session.events) are saved. Suggested events are
  intentionally excluded — they are transient algorithm output and should
  not be persisted.
- The version field exists so future schema changes can be handled
  gracefully on load.
- Paths are stored as strings for reference only; load_json does not
  attempt to re-open the files — that remains the user's responsibility
  via the GUI.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from models.event import Event  # noqa: E402
from models.session import Session  # noqa: E402

# Bump this when the JSON schema changes in a breaking way.
SCHEMA_VERSION = 1


def save_json(session: Session, path: str) -> None:
    """
    Serialize all confirmed events (and session metadata) to a JSON file.

    Parameters
    ----------
    session : Session
        The current session. Only `events`, `ephys_path`, `tiff_folder`,
        and `tiff_offset` are written.
    path : str
        Destination file path. The parent directory must exist.
        If the file already exists it will be overwritten.

    Raises
    ------
    OSError
        If the file cannot be written.
    """
    payload = {
        "version":     SCHEMA_VERSION,
        "ephys_path":  session.ephys_path,
        "tiff_folder": session.tiff_folder,
        "tiff_offset": session.tiff_offset,
        "events":      [e.to_dict() for e in session.events],
    }

    # Write to a temp file then rename — atomic on most platforms, so a
    # crash mid-write never produces a truncated annotations file.
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")           # trailing newline for clean diffs
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file if something went wrong
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_json(path: str) -> tuple[list[Event], dict]:
    """
    Load annotations from a JSON file previously written by save_json().

    Parameters
    ----------
    path : str
        Path to the annotations JSON file.

    Returns
    -------
    events : list[Event]
        Reconstructed Event objects. Source field is preserved as-is
        (events saved as "manual" stay "manual"; anything loaded from
        an external file that lacks a source field defaults to
        "imported" — see Event.from_dict).
    metadata : dict
        Raw metadata from the file:
        {
            "version":     int,
            "ephys_path":  str | None,
            "tiff_folder": str | None,
            "tiff_offset": float,
        }
        The caller decides whether to apply these to the live session.

    Raises
    ------
    FileNotFoundError
        If path does not exist.
    ValueError
        If the file is not valid JSON or is missing required fields.
    """
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path!r}: {exc}") from exc

    version = data.get("version", 1)
    if version > SCHEMA_VERSION:
        # Future schema — we can still try to load it, but warn the caller
        # by including the version in metadata so the GUI can surface it.
        pass

    events_raw = data.get("events", [])
    events: list[Event] = []
    for i, raw in enumerate(events_raw):
        try:
            events.append(Event.from_dict(raw))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Could not parse event at index {i} in {path!r}: {exc}"
            ) from exc

    metadata = {
        "version":     version,
        "ephys_path":  data.get("ephys_path"),
        "tiff_folder": data.get("tiff_folder"),
        "tiff_offset": float(data.get("tiff_offset", 0.0)),
    }

    return events, metadata


def apply_loaded_annotations(
    session: Session,
    path: str,
    restore_paths: bool = False,
) -> tuple[int, dict]:
    """
    Convenience wrapper: load a JSON file and populate session.events.

    Replaces any existing confirmed events in the session.

    Parameters
    ----------
    session : Session
        The session to update.
    path : str
        Path to the annotations JSON file.
    restore_paths : bool
        If True, also update session.ephys_path, session.tiff_folder,
        and session.tiff_offset from the file metadata.
        Default False — the GUI usually manages paths itself.

    Returns
    -------
    n_loaded : int
        Number of events loaded.
    metadata : dict
        Raw metadata dict (see load_json).
    """
    events, metadata = load_json(path)
    session.replace_events(events)

    if restore_paths:
        session.ephys_path  = metadata["ephys_path"]
        session.tiff_folder = metadata["tiff_folder"]
        session.tiff_offset = metadata["tiff_offset"]

    return len(events), metadata