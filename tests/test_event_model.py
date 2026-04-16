"""
tests/test_event_model.py
-------------------------
Test suite for Branch 1 (event model + session CRUD + JSON I/O)
and the model-layer logic introduced in Branch 2 (no GUI required).

Run from the repository root:
    python -m pytest tests/test_event_model.py -v
    # or without pytest:
    python tests/test_event_model.py

All tests are pure Python — no Tkinter, no matplotlib, no HDF5 files needed.
"""

import json
import os
import sys
import tempfile
import unittest

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sd_viewer"))

from models.event import Event, VALID_SOURCES
from models.session import Session
from annotation_io.export_annotations import (
    save_json,
    load_json,
    apply_loaded_annotations,
    SCHEMA_VERSION,
)


# =============================================================================
# Branch 1 — Event dataclass
# =============================================================================

class TestEventCreation(unittest.TestCase):

    def test_minimal_creation(self):
        e = Event(start_time=10.0)
        self.assertEqual(e.start_time, 10.0)
        self.assertIsNone(e.end_time)
        self.assertEqual(e.label, "")
        self.assertEqual(e.source, "manual")
        self.assertIsNotNone(e.id)
        self.assertEqual(len(e.id), 32)   # uuid4 hex

    def test_full_creation(self):
        e = Event(start_time=5.0, end_time=20.0, label="SD1", source="imported")
        self.assertEqual(e.start_time, 5.0)
        self.assertEqual(e.end_time, 20.0)
        self.assertEqual(e.label, "SD1")
        self.assertEqual(e.source, "imported")

    def test_id_is_unique(self):
        ids = {Event(start_time=float(i)).id for i in range(100)}
        self.assertEqual(len(ids), 100)

    def test_explicit_id_preserved(self):
        e = Event(start_time=1.0, id="abc123")
        self.assertEqual(e.id, "abc123")

    def test_integer_times_accepted(self):
        """start_time and end_time accept ints (common in test code)."""
        e = Event(start_time=10, end_time=20)
        self.assertEqual(e.start_time, 10)
        self.assertEqual(e.end_time, 20)


class TestEventValidation(unittest.TestCase):

    def test_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            Event(start_time=50.0, end_time=10.0)

    def test_end_equal_start_is_allowed(self):
        """Zero-duration interval is degenerate but not invalid."""
        e = Event(start_time=10.0, end_time=10.0)
        self.assertEqual(e.duration, 0.0)

    def test_invalid_source_raises(self):
        with self.assertRaises(ValueError):
            Event(start_time=1.0, source="robot")

    def test_non_numeric_start_raises(self):
        with self.assertRaises(TypeError):
            Event(start_time="ten")

    def test_non_numeric_end_raises(self):
        with self.assertRaises(TypeError):
            Event(start_time=1.0, end_time="twenty")

    def test_all_valid_sources(self):
        for src in VALID_SOURCES:
            e = Event(start_time=1.0, source=src)
            self.assertEqual(e.source, src)


class TestEventProperties(unittest.TestCase):

    def test_duration_interval(self):
        e = Event(start_time=10.0, end_time=45.0)
        self.assertAlmostEqual(e.duration, 35.0)

    def test_duration_point(self):
        e = Event(start_time=10.0)
        self.assertIsNone(e.duration)

    def test_is_interval(self):
        self.assertTrue(Event(start_time=1.0, end_time=2.0).is_interval)
        self.assertFalse(Event(start_time=1.0).is_interval)

    def test_is_point(self):
        self.assertTrue(Event(start_time=1.0).is_point)
        self.assertFalse(Event(start_time=1.0, end_time=2.0).is_point)


class TestEventSerialization(unittest.TestCase):

    def _round_trip(self, **kwargs):
        e = Event(**kwargs)
        return Event.from_dict(e.to_dict())

    def test_round_trip_interval(self):
        e2 = self._round_trip(start_time=10.0, end_time=45.0, label="SD1")
        self.assertAlmostEqual(e2.start_time, 10.0)
        self.assertAlmostEqual(e2.end_time, 45.0)
        self.assertEqual(e2.label, "SD1")

    def test_round_trip_point(self):
        e2 = self._round_trip(start_time=88.0, label="onset")
        self.assertAlmostEqual(e2.start_time, 88.0)
        self.assertIsNone(e2.end_time)

    def test_round_trip_preserves_id(self):
        e  = Event(start_time=1.0)
        e2 = Event.from_dict(e.to_dict())
        self.assertEqual(e.id, e2.id)

    def test_round_trip_all_sources(self):
        for src in VALID_SOURCES:
            e2 = self._round_trip(start_time=1.0, source=src)
            self.assertEqual(e2.source, src)

    def test_to_dict_keys(self):
        d = Event(start_time=1.0).to_dict()
        self.assertSetEqual(set(d.keys()), {"id", "start", "end", "label", "source"})

    def test_from_dict_missing_label_defaults(self):
        d = {"start": 1.0, "end": None}
        e = Event.from_dict(d)
        self.assertEqual(e.label, "")

    def test_from_dict_missing_source_defaults_to_imported(self):
        """Older annotation files without a source field load as 'imported'."""
        d = {"start": 1.0, "end": None, "label": "old"}
        e = Event.from_dict(d)
        self.assertEqual(e.source, "imported")

    def test_from_dict_missing_id_generates_new(self):
        d = {"start": 1.0, "end": None, "label": ""}
        e = Event.from_dict(d)
        self.assertIsNotNone(e.id)


# =============================================================================
# Branch 1 — Session CRUD
# =============================================================================

class TestSessionCRUD(unittest.TestCase):

    def setUp(self):
        self.s = Session()

    # ── add_event ─────────────────────────────────────────────────────────

    def test_add_returns_event(self):
        e = self.s.add_event(10.0, 45.0, "SD1")
        self.assertIsInstance(e, Event)

    def test_add_stores_event(self):
        e = self.s.add_event(10.0)
        self.assertIn(e, self.s.events)

    def test_events_sorted_by_start(self):
        self.s.add_event(30.0, label="C")
        self.s.add_event(10.0, label="A")
        self.s.add_event(20.0, label="B")
        starts = [e.start_time for e in self.s.events]
        self.assertEqual(starts, sorted(starts))

    def test_add_multiple(self):
        for i in range(5):
            self.s.add_event(float(i))
        self.assertEqual(len(self.s.events), 5)

    # ── remove_event ──────────────────────────────────────────────────────

    def test_remove_existing(self):
        e = self.s.add_event(10.0)
        result = self.s.remove_event(e)
        self.assertTrue(result)
        self.assertNotIn(e, self.s.events)

    def test_remove_nonexistent_returns_false(self):
        e = Event(start_time=99.0)
        self.assertFalse(self.s.remove_event(e))

    def test_remove_correct_event_when_multiple(self):
        e1 = self.s.add_event(10.0, label="keep")
        e2 = self.s.add_event(20.0, label="remove")
        self.s.remove_event(e2)
        self.assertIn(e1, self.s.events)
        self.assertNotIn(e2, self.s.events)

    # ── update_event ──────────────────────────────────────────────────────

    def test_update_label(self):
        e = self.s.add_event(10.0)
        self.s.update_event(e, label="new label")
        self.assertEqual(e.label, "new label")

    def test_update_end_time(self):
        e = self.s.add_event(10.0)
        self.s.update_event(e, end_time=50.0)
        self.assertEqual(e.end_time, 50.0)

    def test_update_multiple_fields(self):
        e = self.s.add_event(10.0)
        self.s.update_event(e, start_time=5.0, end_time=25.0, label="updated")
        self.assertEqual(e.start_time, 5.0)
        self.assertEqual(e.end_time, 25.0)
        self.assertEqual(e.label, "updated")

    def test_update_validates_times(self):
        e = self.s.add_event(10.0, end_time=20.0)
        with self.assertRaises(ValueError):
            self.s.update_event(e, start_time=30.0)   # start > existing end

    def test_update_nonexistent_raises(self):
        e = Event(start_time=1.0)
        with self.assertRaises(ValueError):
            self.s.update_event(e, label="x")

    # ── get_event_by_id ───────────────────────────────────────────────────

    def test_get_by_id_found(self):
        e = self.s.add_event(10.0, label="find me")
        found = self.s.get_event_by_id(e.id)
        self.assertIs(found, e)

    def test_get_by_id_not_found(self):
        self.assertIsNone(self.s.get_event_by_id("nonexistent"))

    # ── clear / replace ───────────────────────────────────────────────────

    def test_clear_events(self):
        self.s.add_event(1.0)
        self.s.add_event(2.0)
        self.s.clear_events()
        self.assertEqual(len(self.s.events), 0)

    def test_replace_events(self):
        self.s.add_event(1.0)
        new_events = [Event(start_time=5.0), Event(start_time=10.0)]
        self.s.replace_events(new_events)
        self.assertEqual(len(self.s.events), 2)
        self.assertIn(new_events[0], self.s.events)


class TestSessionSuggested(unittest.TestCase):

    def setUp(self):
        self.s = Session()

    def test_set_suggested(self):
        sug = [Event(start_time=10.0, source="suggested")]
        self.s.set_suggested_events(sug)
        self.assertEqual(len(self.s.suggested_events), 1)

    def test_set_suggested_wrong_source_raises(self):
        with self.assertRaises(ValueError):
            self.s.set_suggested_events([Event(start_time=1.0, source="manual")])

    def test_accept_suggestion_promotes(self):
        sug = Event(start_time=10.0, end_time=20.0, label="auto", source="suggested")
        self.s.set_suggested_events([sug])
        accepted = self.s.accept_suggestion(sug)
        self.assertEqual(accepted.source, "manual")
        self.assertIn(accepted, self.s.events)
        self.assertEqual(len(self.s.suggested_events), 0)

    def test_accept_suggestion_not_in_list_raises(self):
        sug = Event(start_time=10.0, source="suggested")
        with self.assertRaises(ValueError):
            self.s.accept_suggestion(sug)

    def test_reject_suggestion(self):
        sug = Event(start_time=10.0, source="suggested")
        self.s.set_suggested_events([sug])
        result = self.s.reject_suggestion(sug)
        self.assertTrue(result)
        self.assertEqual(len(self.s.suggested_events), 0)

    def test_reject_nonexistent_returns_false(self):
        sug = Event(start_time=10.0, source="suggested")
        self.assertFalse(self.s.reject_suggestion(sug))

    def test_clear_suggestions(self):
        self.s.set_suggested_events([
            Event(start_time=1.0, source="suggested"),
            Event(start_time=2.0, source="suggested"),
        ])
        self.s.clear_suggestions()
        self.assertEqual(len(self.s.suggested_events), 0)

    def test_suggestions_do_not_appear_in_events(self):
        sug = Event(start_time=10.0, source="suggested")
        self.s.set_suggested_events([sug])
        self.assertNotIn(sug, self.s.events)

    def test_confirmed_events_do_not_appear_in_suggested(self):
        ev = self.s.add_event(10.0)
        self.assertNotIn(ev, self.s.suggested_events)


class TestSessionMetadata(unittest.TestCase):

    def test_has_data_false_initially(self):
        self.assertFalse(Session().has_data)

    def test_has_data_true_after_ephys(self):
        s = Session()
        s.ephys_path = "/data/rec.h5"
        self.assertTrue(s.has_data)

    def test_has_data_true_after_tiff(self):
        s = Session()
        s.tiff_folder = "/data/tiffs"
        self.assertTrue(s.has_data)


# =============================================================================
# Branch 1 — JSON I/O
# =============================================================================

class TestJSONIO(unittest.TestCase):

    def _make_session(self):
        s = Session()
        s.ephys_path  = "/data/recording.h5"
        s.tiff_folder = "/data/tiffs"
        s.tiff_offset = 7.5
        s.add_event(10.0, 45.0, "SD1")
        s.add_event(88.0, label="onset only")
        return s

    def test_save_creates_file(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_json(s, path)
            self.assertTrue(os.path.exists(path))
        finally:
            os.unlink(path)

    def test_json_structure(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("version", data)
            self.assertIn("events", data)
            self.assertIn("ephys_path", data)
            self.assertIn("tiff_folder", data)
            self.assertIn("tiff_offset", data)
            self.assertEqual(data["version"], SCHEMA_VERSION)
        finally:
            os.unlink(path)

    def test_only_confirmed_events_saved(self):
        """Suggested events must NOT appear in the saved file."""
        s = self._make_session()
        s.set_suggested_events([
            Event(start_time=5.0, source="suggested"),
            Event(start_time=6.0, source="suggested"),
        ])
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            with open(path) as f:
                data = json.load(f)
            # only the 2 confirmed events should be present
            self.assertEqual(len(data["events"]), 2)
            sources = {e["source"] for e in data["events"]}
            self.assertNotIn("suggested", sources)
        finally:
            os.unlink(path)

    def test_full_round_trip(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            events, meta = load_json(path)

            self.assertEqual(len(events), len(s.events))
            for orig, loaded in zip(s.events, events):
                self.assertEqual(orig.id,          loaded.id)
                self.assertAlmostEqual(orig.start_time, loaded.start_time)
                self.assertEqual(orig.end_time,    loaded.end_time)
                self.assertEqual(orig.label,       loaded.label)
                self.assertEqual(orig.source,      loaded.source)

            self.assertEqual(meta["ephys_path"],  s.ephys_path)
            self.assertEqual(meta["tiff_folder"], s.tiff_folder)
            self.assertAlmostEqual(meta["tiff_offset"], s.tiff_offset)
        finally:
            os.unlink(path)

    def test_apply_loaded_annotations(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            s2 = Session()
            n, meta = apply_loaded_annotations(s2, path)
            self.assertEqual(n, len(s.events))
            self.assertEqual(len(s2.events), len(s.events))
        finally:
            os.unlink(path)

    def test_apply_does_not_restore_paths_by_default(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            s2 = Session()
            apply_loaded_annotations(s2, path, restore_paths=False)
            self.assertIsNone(s2.ephys_path)
        finally:
            os.unlink(path)

    def test_apply_restores_paths_when_requested(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            s2 = Session()
            apply_loaded_annotations(s2, path, restore_paths=True)
            self.assertEqual(s2.ephys_path,  s.ephys_path)
            self.assertEqual(s2.tiff_folder, s.tiff_folder)
            self.assertAlmostEqual(s2.tiff_offset, s.tiff_offset)
        finally:
            os.unlink(path)

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_json("/nonexistent/path/file.json")

    def test_load_invalid_json_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            f.write("this is not json {{{")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_json(path)
        finally:
            os.unlink(path)

    def test_load_malformed_event_raises(self):
        bad = {"version": 1, "events": [{"not_start": 1.0}]}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            json.dump(bad, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_json(path)
        finally:
            os.unlink(path)

    def test_atomic_write_no_partial_file_on_bad_path(self):
        """save_json should raise cleanly if the directory doesn't exist."""
        s = self._make_session()
        with self.assertRaises(OSError):
            save_json(s, "/nonexistent_dir/annotations.json")

    def test_save_overwrites_existing_file(self):
        s = self._make_session()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(s, path)
            # second save with different content
            s.clear_events()
            s.add_event(99.0, label="new")
            save_json(s, path)
            events, _ = load_json(path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].label, "new")
        finally:
            os.unlink(path)


# =============================================================================
# Branch 2 — model-layer logic (no GUI)
# =============================================================================

class TestBranch2ModelLogic(unittest.TestCase):
    """
    Tests for the annotation workflow that Branch 2 implements in the GUI.
    These verify the session-level state transitions that timeline_panel.py
    and event_list_panel.py depend on, without needing Tkinter.
    """

    def setUp(self):
        self.s = Session()

    def test_left_click_creates_point_event(self):
        """Simulates timeline left-click: add point event at a time."""
        t = 42.5
        ev = self.s.add_event(start_time=t)
        self.assertTrue(ev.is_point)
        self.assertAlmostEqual(ev.start_time, t)
        self.assertEqual(ev.source, "manual")

    def test_shift_click_closes_most_recent_open_event(self):
        """
        Simulates shift-click logic: find the most recent point event
        whose start < t, close it by setting end_time.
        """
        t_start = 20.0
        t_end   = 55.0
        ev = self.s.add_event(start_time=t_start)
        self.assertTrue(ev.is_point)

        # find most recent open event with start < t_end and close it
        open_events = [
            e for e in self.s.events
            if e.is_point and e.start_time < t_end
        ]
        self.assertEqual(len(open_events), 1)
        target = max(open_events, key=lambda e: e.start_time)
        self.s.update_event(target, end_time=t_end)

        self.assertTrue(target.is_interval)
        self.assertAlmostEqual(target.end_time, t_end)

    def test_shift_click_with_no_open_event_creates_new(self):
        """
        If no open point event exists before t, shift-click should
        fall back to creating a new point event.
        """
        t = 30.0
        # no existing events
        open_events = [
            e for e in self.s.events
            if e.is_point and e.start_time < t
        ]
        self.assertEqual(len(open_events), 0)
        # fallback: create point event
        ev = self.s.add_event(start_time=t)
        self.assertTrue(ev.is_point)

    def test_shift_click_selects_most_recent_not_earliest(self):
        """When multiple open events exist, the most recently started is closed."""
        self.s.add_event(start_time=10.0)
        self.s.add_event(start_time=20.0)
        t_end = 50.0

        open_events = [
            e for e in self.s.events
            if e.is_point and e.start_time < t_end
        ]
        target = max(open_events, key=lambda e: e.start_time)
        self.assertAlmostEqual(target.start_time, 20.0)

        self.s.update_event(target, end_time=t_end)
        # the 10.0 event should still be a point event
        remaining_points = [e for e in self.s.events if e.is_point]
        self.assertEqual(len(remaining_points), 1)
        self.assertAlmostEqual(remaining_points[0].start_time, 10.0)

    def test_right_click_label_edit(self):
        """Simulates the label dialog saving a new label."""
        ev = self.s.add_event(start_time=15.0)
        self.s.update_event(ev, label="SD onset")
        self.assertEqual(ev.label, "SD onset")

    def test_right_click_delete(self):
        """Simulates the label dialog choosing delete."""
        ev = self.s.add_event(start_time=15.0)
        self.s.remove_event(ev)
        self.assertNotIn(ev, self.s.events)

    def test_event_list_delete_via_session(self):
        """Simulates event list delete button: remove event, verify list shrinks."""
        e1 = self.s.add_event(10.0, label="keep")
        e2 = self.s.add_event(20.0, label="remove")
        self.s.remove_event(e2)
        self.assertEqual(len(self.s.events), 1)
        self.assertIn(e1, self.s.events)

    def test_event_list_seek_on_select(self):
        """
        Simulates clicking an event in the list panel, which should
        report the event's start_time for the controller to seek to.
        """
        ev = self.s.add_event(start_time=33.7, end_time=80.0, label="SD2")
        # The callback just needs ev.start_time to pass to ctrl.seek()
        self.assertAlmostEqual(ev.start_time, 33.7)

    def test_suggested_events_separate_from_confirmed(self):
        """Accept/reject workflow keeps the two lists independent."""
        # add two confirmed events
        e1 = self.s.add_event(10.0, label="confirmed")
        e2 = self.s.add_event(20.0, label="confirmed 2")

        # detection adds suggestions
        s1 = Event(start_time=5.0, end_time=8.0, source="suggested", label="auto1")
        s2 = Event(start_time=15.0, source="suggested", label="auto2")
        self.s.set_suggested_events([s1, s2])

        self.assertEqual(len(self.s.events), 2)
        self.assertEqual(len(self.s.suggested_events), 2)

        # accept one
        self.s.accept_suggestion(s1)
        self.assertEqual(len(self.s.events), 3)
        self.assertEqual(len(self.s.suggested_events), 1)
        self.assertEqual(s1.source, "manual")

        # reject the other
        self.s.reject_suggestion(s2)
        self.assertEqual(len(self.s.suggested_events), 0)
        self.assertEqual(len(self.s.events), 3)

    def test_save_and_reload_preserves_complete_annotation_session(self):
        """
        Full end-to-end: annotate several events, save, load into a
        fresh session, verify everything survived the round-trip.
        """
        self.s.ephys_path  = "/data/rec.h5"
        self.s.tiff_folder = "/data/frames"
        self.s.tiff_offset = 3.0

        self.s.add_event(10.0, 45.0, "SD1")
        self.s.add_event(120.0, label="SD2 onset")
        self.s.add_event(130.0, 200.0, "SD2")

        # add suggestions — should NOT appear after reload
        self.s.set_suggested_events([
            Event(start_time=50.0, source="suggested"),
        ])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            save_json(self.s, path)

            s2 = Session()
            n, meta = apply_loaded_annotations(s2, path, restore_paths=True)

            self.assertEqual(n, 3)
            self.assertEqual(len(s2.events), 3)
            self.assertEqual(len(s2.suggested_events), 0)

            for orig, loaded in zip(self.s.events, s2.events):
                self.assertEqual(orig.id,           loaded.id)
                self.assertAlmostEqual(orig.start_time, loaded.start_time)
                self.assertEqual(orig.end_time,     loaded.end_time)
                self.assertEqual(orig.label,        loaded.label)
                self.assertEqual(orig.source,       loaded.source)

            self.assertEqual(s2.ephys_path,   self.s.ephys_path)
            self.assertEqual(s2.tiff_folder,  self.s.tiff_folder)
            self.assertAlmostEqual(s2.tiff_offset, self.s.tiff_offset)
        finally:
            os.unlink(path)


# =============================================================================
# Runner
# =============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)