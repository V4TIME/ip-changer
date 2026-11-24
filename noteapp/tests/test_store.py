"""Storage tests: run without Kivy, a display, or Android."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteapp.store import MAX_TEXT, Note, NoteStore


class NoteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = NoteStore(self.temp.name)
        self.data = Path(self.temp.name) / "notes.json"

    def test_empty_store_loads_empty_list(self) -> None:
        self.assertEqual(self.store.load(), [])

    def test_add_persists_and_round_trips(self) -> None:
        notes = self.store.add("first note")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].text, "first note")
        reloaded = NoteStore(self.temp.name).load()
        self.assertEqual([note.text for note in reloaded], ["first note"])

    def test_newest_note_is_first(self) -> None:
        self.store.save(
            [
                Note(id="a", text="old", created=1.0),
                Note(id="b", text="new", created=2.0),
            ]
        )
        self.assertEqual([note.text for note in self.store.load()], ["new", "old"])

    def test_second_save_does_not_corrupt_json(self) -> None:
        self.store.add("one")
        self.store.add("two")
        raw = json.loads(self.data.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)
        self.assertEqual(len(raw["notes"]), 2)

    def test_delete_removes_only_the_target(self) -> None:
        notes = self.store.add("keep")
        notes = self.store.add("remove")
        removed = self.store.delete(notes[0].id)
        self.assertEqual([note.text for note in removed], ["keep"])

    def test_clear_empties_the_file(self) -> None:
        self.store.add("gone")
        self.assertEqual(self.store.clear(), [])
        self.assertEqual(NoteStore(self.temp.name).load(), [])

    def test_empty_text_is_rejected(self) -> None:
        for candidate in ("", "   ", "\n\t "):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.store.add(candidate)

    def test_overlong_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add("x" * (MAX_TEXT + 1))

    def test_whitespace_is_normalised(self) -> None:
        notes = self.store.add("  padded  \r\nsecond\r\n")
        self.assertEqual(notes[0].text, "padded  \nsecond")

    def test_corrupt_file_is_quarantined_not_deleted(self) -> None:
        self.data.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load(), [])
        self.assertFalse(self.data.exists())
        backups = list(Path(self.temp.name).glob("notes.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{not json")

    def test_legacy_string_list_is_imported(self) -> None:
        self.data.write_text(json.dumps(["alpha", "beta"]), encoding="utf-8")
        self.assertEqual(sorted(note.text for note in self.store.load()), ["alpha", "beta"])

    def test_legacy_records_missing_ids_get_ids(self) -> None:
        self.data.write_text(json.dumps({"notes": [{"text": "legacy"}]}), encoding="utf-8")
        notes = self.store.load()
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].id)

    def test_saved_file_is_private(self) -> None:
        self.store.add("secret")
        mode = self.data.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_load_ignores_malformed_records(self) -> None:
        self.data.write_text(
            json.dumps({"notes": [{"text": "ok"}, {"nope": 1}, "loose", {"text": "   "}]}),
            encoding="utf-8",
        )
        self.assertEqual([note.text for note in self.store.load()], ["ok"])


if __name__ == "__main__":
    unittest.main()
