"""Persistent note storage: atomic writes, corruption recovery, legacy import.

Pure standard library on purpose. The Kivy UI imports this module, and the
tests exercise it without a display, so a storage bug can never hide behind a
GUI that "looks fine".
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
MAX_TEXT = 10_000


@dataclass(frozen=True)
class Note:
    id: str
    text: str
    created: float

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "created": self.created}

    @classmethod
    def create(cls, text: str, now: float | None = None) -> "Note":
        cleaned = clean_text(text)
        return cls(id=uuid.uuid4().hex, text=cleaned, created=time.time() if now is None else now)


def clean_text(text: str) -> str:
    """Normalise input and enforce the size limit.

    Raises ``ValueError`` for an empty note so the caller can show a message
    instead of silently saving whitespace.
    """

    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        raise ValueError("note is empty")
    if len(cleaned) > MAX_TEXT:
        raise ValueError(f"note is longer than {MAX_TEXT} characters")
    return cleaned


def _parse_note(raw: Any) -> Note | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    note_id = raw.get("id")
    created = raw.get("created")
    return Note(
        id=note_id if isinstance(note_id, str) and note_id else uuid.uuid4().hex,
        text=text[:MAX_TEXT],
        created=float(created) if isinstance(created, (int, float)) else 0.0,
    )


class NoteStore:
    """A single JSON file inside the app's private storage directory."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "notes.json"

    # -- reading ---------------------------------------------------------

    def load(self) -> list[Note]:
        try:
            raw_text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError):
            self._quarantine()
            return []

        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            self._quarantine()
            return []

        notes = self._from_payload(raw)
        return sorted(notes, key=lambda note: note.created, reverse=True)

    def _from_payload(self, raw: Any) -> list[Note]:
        # Legacy builds wrote a bare list, sometimes of plain strings.
        if isinstance(raw, list):
            notes = []
            for item in raw:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        notes.append(Note.create(text))
                else:
                    note = _parse_note(item)
                    if note:
                        notes.append(note)
            return notes
        if isinstance(raw, dict):
            items = raw.get("notes")
            if isinstance(items, list):
                return [note for note in (_parse_note(item) for item in items) if note]
        return []

    def _quarantine(self) -> None:
        """Move an unreadable file aside instead of deleting the user's data."""

        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.directory / f"notes.corrupt-{stamp}.json"
        try:
            os.replace(self.path, target)
        except OSError:
            pass

    # -- writing ---------------------------------------------------------

    def save(self, notes: Iterable[Note]) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "notes": [note.as_dict() for note in notes],
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".notes.", dir=str(self.directory))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    # -- operations ------------------------------------------------------

    def add(self, text: str) -> list[Note]:
        note = Note.create(text)
        notes = [note, *self.load()]
        self.save(notes)
        return notes

    def delete(self, note_id: str) -> list[Note]:
        notes = [note for note in self.load() if note.id != note_id]
        self.save(notes)
        return notes

    def clear(self) -> list[Note]:
        self.save([])
        return []
