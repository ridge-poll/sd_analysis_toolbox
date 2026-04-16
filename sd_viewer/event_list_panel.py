"""
event_list_panel.py
-------------------
A compact tk.Frame containing a ttk.Treeview that lists all confirmed
events in the current session.

Mirrors timeline_panel.py: reads from session, never mutates it directly.
All mutations are delegated back to main_gui via callbacks so the session,
timeline panel, and event list all stay in sync through a single update path.

Columns
-------
  #    | sequential display number (1, 2, 3 …)
  Start | onset time in seconds
  End   | offset time, or "—" for point events
  Dur   | duration in seconds, or "—" for point events
  Label | annotation string
  Src   | source tag (manual / suggested / imported)

Public API (called by main_gui)
-------------------------------
    panel.refresh()           — rebuild list from session (call after any mutation)
    panel.set_session(s)      — swap session at runtime
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from models.session import Session
from models.event import Event

# Column definitions: (id, heading, width, anchor)
_COLUMNS = [
    ("num",   "#",     32,  tk.CENTER),
    ("start", "Start", 72,  tk.E),
    ("end",   "End",   72,  tk.E),
    ("dur",   "Dur",   64,  tk.E),
    ("label", "Label", 140, tk.W),
    ("src",   "Src",   68,  tk.CENTER),
]


class EventListPanel(tk.Frame):
    """
    Scrollable event list with delete key support.

    Parameters
    ----------
    parent : tk.Widget
    session : Session
    on_event_removed : callable, optional
        Called with (Event,) when the user deletes a row.
    on_event_selected : callable, optional
        Called with (Event,) when the user clicks a row.
        main_gui can use this to seek the playhead to that event's start time.
    kwargs : passed to tk.Frame
    """

    def __init__(
        self,
        parent,
        session: Session,
        on_event_removed=None,
        on_event_selected=None,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self._session            = session
        self._on_event_removed   = on_event_removed
        self._on_event_selected  = on_event_selected

        # Maps Treeview item IDs → Event objects for O(1) lookup
        self._item_to_event: dict[str, Event] = {}

        self._build_ui()

    # =========================================================================
    # Public API
    # =========================================================================

    def set_session(self, session: Session) -> None:
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the current session. Call after any mutation."""
        # remember which event was selected so we can re-select after refresh
        selected_id = self._selected_event_id()

        self._tree.delete(*self._tree.get_children())
        self._item_to_event.clear()

        for i, ev in enumerate(self._session.events, start=1):
            end_str = f"{ev.end_time:.3f}" if ev.is_interval else "—"
            dur_str = f"{ev.duration:.3f}" if ev.is_interval else "—"
            iid = self._tree.insert(
                "", tk.END,
                values=(i, f"{ev.start_time:.3f}", end_str, dur_str,
                        ev.label, ev.source),
            )
            self._item_to_event[iid] = ev

        # restore selection if the event still exists
        if selected_id is not None:
            for iid, ev in self._item_to_event.items():
                if ev.id == selected_id:
                    self._tree.selection_set(iid)
                    self._tree.see(iid)
                    break

        # update row count label
        n = len(self._session.events)
        self._count_var.set(f"{n} event{'s' if n != 1 else ''}")

    # =========================================================================
    # UI construction
    # =========================================================================

    def _build_ui(self):
        # ── header row ────────────────────────────────────────────────────
        hdr = tk.Frame(self)
        hdr.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 0))

        tk.Label(hdr, text="Events", font=("TkDefaultFont", 9, "bold")
                 ).pack(side=tk.LEFT)
        self._count_var = tk.StringVar(value="0 events")
        tk.Label(hdr, textvariable=self._count_var, fg="gray",
                 font=("TkDefaultFont", 8)).pack(side=tk.LEFT, padx=6)

        tk.Button(hdr, text="Delete selected", font=("TkDefaultFont", 8),
                  command=self._delete_selected).pack(side=tk.RIGHT, padx=2)

        # ── treeview + scrollbar ──────────────────────────────────────────
        frame = tk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)

        col_ids = [c[0] for c in _COLUMNS]
        self._tree = ttk.Treeview(frame, columns=col_ids, show="headings",
                                  selectmode="browse", height=6)

        for col_id, heading, width, anchor in _COLUMNS:
            self._tree.heading(col_id, text=heading,
                               command=lambda c=col_id: self._sort_by(c))
            self._tree.column(col_id, width=width, anchor=anchor,
                              stretch=(col_id == "label"))

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL,
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # ── bindings ──────────────────────────────────────────────────────
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Delete>",           self._delete_selected)
        self._tree.bind("<BackSpace>",        self._delete_selected)

        # ── sort state ────────────────────────────────────────────────────
        self._sort_col: str  = "start"
        self._sort_asc: bool = True

    # =========================================================================
    # Interaction
    # =========================================================================

    def _on_select(self, _event=None):
        iid = self._selected_iid()
        if iid is None:
            return
        ev = self._item_to_event.get(iid)
        if ev is not None and self._on_event_selected:
            self._on_event_selected(ev)

    def _delete_selected(self, _event=None):
        iid = self._selected_iid()
        if iid is None:
            return
        ev = self._item_to_event.get(iid)
        if ev is None:
            return
        self._session.remove_event(ev)
        self.refresh()
        if self._on_event_removed:
            self._on_event_removed(ev)

    def _sort_by(self, col: str):
        """Sort the visible rows by column. Clicking the same column toggles order."""
        if col == self._sort_col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True

        # Collect (value, iid) pairs and sort
        rows = [(self._tree.set(iid, col), iid)
                for iid in self._tree.get_children("")]

        def sort_key(pair):
            val = pair[0]
            # Try numeric sort for time/duration columns
            try:
                return (0, float(val))
            except (ValueError, TypeError):
                return (1, str(val).lower())

        rows.sort(key=sort_key, reverse=not self._sort_asc)
        for idx, (_, iid) in enumerate(rows):
            self._tree.move(iid, "", idx)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _selected_iid(self) -> str | None:
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _selected_event_id(self) -> str | None:
        """Return the Event.id of the currently selected row, or None."""
        iid = self._selected_iid()
        if iid is None:
            return None
        ev = self._item_to_event.get(iid)
        return ev.id if ev else None