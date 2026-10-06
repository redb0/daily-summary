"""Сборщик транскриптов OpenCode: временная SQLite, без живой базы."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest

from app.collectors.opencode import CollectedOpencode, collect_opencode
from app.config import (
    Config,
    GitConfig,
    NotesConfig,
    OpencodeConfig,
    StateConfig,
    TelegramConfig,
    TranscriptsConfig,
)
from app.summary.collect import collect_and_store
from app.summary.models import (
    SourceStatus,
    TranscriptMessage,
    TranscriptSession,
    TranscriptsSource,
    Window,
)

_MOSCOW = ZoneInfo("Europe/Moscow")
_SESSION = "ses_today"
_DIRECTORY = "/home/vvoronov/projects/my/experience"
# Миллисекунды. Литералы, не пересчёт окна в тесте.
_NOON = 1_791_190_800_000  # 2026-10-05 12:00:00+03:00
_YESTERDAY = 1_791_126_000_000  # 2026-10-04 18:00:00+03:00
_TOMORROW = 1_791_234_000_000  # 2026-10-06 00:00:00+03:00
_SESSION_START = 1_791_093_600_000  # 2026-10-04 09:00:00+03:00


class _Line(NamedTuple):
    message_id: str
    created_ms: int
    role: str
    parts: list[tuple[str, object]]


def _config(db: Path, *, head: int = 10, tail: int = 10) -> Config:
    return Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        opencode=OpencodeConfig(db=db, head_messages=head, tail_messages=tail),
    )


def _day() -> Window:
    return Window.model_validate(
        {
            "from": datetime(2026, 10, 5, 0, 0, tzinfo=_MOSCOW),
            "to": datetime(2026, 10, 5, 23, 59, 59, tzinfo=_MOSCOW),
        },
    )


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            time_created INTEGER NOT NULL
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """,
    )
    return connection


def _session(connection: sqlite3.Connection, session_id: str, *, created_ms: int) -> None:
    connection.execute(
        "INSERT INTO session (id, directory, time_created) VALUES (?, ?, ?)",
        (session_id, _DIRECTORY, created_ms),
    )


def _message(connection: sqlite3.Connection, session_id: str, line: _Line) -> None:
    connection.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
        (line.message_id, session_id, line.created_ms, json.dumps({"role": line.role})),
    )
    for index, (part_type, payload) in enumerate(line.parts):
        body: dict[str, object] = {"type": part_type}
        if isinstance(payload, str):
            body["text"] = payload
        elif isinstance(payload, dict):
            body.update(payload)
        connection.execute(
            """
            INSERT INTO part (id, message_id, session_id, time_created, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"{line.message_id}-{index}",
                line.message_id,
                session_id,
                line.created_ms + index,
                json.dumps(body),
            ),
        )


def test_only_spoken_text_is_kept(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    connection = _database(db)
    _session(connection, _SESSION, created_ms=_NOON)
    _message(
        connection,
        _SESSION,
        _Line(
            "user-1",
            _NOON,
            "user",
            [
                ("text", "почини сбор"),
                ("text", {"text": "и тесты", "synthetic": 1}),
                ("tool", "не текст"),
                ("reasoning", "внутреннее"),
                ("patch", "diff"),
                ("file", "файл"),
                ("step-start", ""),
                ("step-finish", ""),
                ("compaction", "сжатие"),
                ("subtask", "подзадача"),
            ],
        ),
    )
    _message(
        connection,
        _SESSION,
        _Line(
            "assistant-1",
            _NOON + 1_000,
            "assistant",
            [("text", "сделаю"), ("text", "сегодня")],
        ),
    )
    connection.commit()
    connection.close()

    collected = collect_opencode(_config(db), _day())

    assert collected == CollectedOpencode(
        sessions=[
            TranscriptSession(
                project=_DIRECTORY,
                id=_SESSION,
                messages=[
                    TranscriptMessage(role="user", text="почини сбор"),
                    TranscriptMessage(role="assistant", text="сделаю\nсегодня"),
                ],
            ),
        ],
        truncations=[],
    )


def test_message_outside_the_window_is_dropped_even_if_the_session_started_earlier(
    tmp_path: Path,
) -> None:
    db = tmp_path / "opencode.db"
    connection = _database(db)
    _session(connection, _SESSION, created_ms=_SESSION_START)
    _message(connection, _SESSION, _Line("old", _YESTERDAY, "user", [("text", "вчера")]))
    _message(connection, _SESSION, _Line("today", _NOON, "user", [("text", "сегодня")]))
    _message(connection, _SESSION, _Line("next", _TOMORROW, "assistant", [("text", "завтра")]))
    connection.commit()
    connection.close()

    collected = collect_opencode(_config(db), _day())

    assert collected == CollectedOpencode(
        sessions=[
            TranscriptSession(
                project=_DIRECTORY,
                id=_SESSION,
                messages=[TranscriptMessage(role="user", text="сегодня")],
            ),
        ],
        truncations=[],
    )


def test_long_session_keeps_head_and_tail(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    connection = _database(db)
    _session(connection, _SESSION, created_ms=_NOON)
    for index, text in enumerate(("задача", "уточнение", "середина", "ещё", "итог")):
        _message(
            connection,
            _SESSION,
            _Line(f"m{index}", _NOON + index * 1_000, "user", [("text", text)]),
        )
    connection.commit()
    connection.close()

    collected = collect_opencode(_config(db, head=2, tail=1), _day())

    assert collected == CollectedOpencode(
        sessions=[
            TranscriptSession(
                project=_DIRECTORY,
                id=_SESSION,
                messages=[
                    TranscriptMessage(role="user", text="задача"),
                    TranscriptMessage(role="user", text="уточнение"),
                    TranscriptMessage(role="user", text="итог"),
                ],
            ),
        ],
        truncations=[
            f"{_DIRECTORY}: сессия {_SESSION} усечена до 2 первых и 1 последних сообщений",
        ],
    )


def test_collected_session_is_counted_in_the_dump(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    moment = datetime(2026, 10, 5, 15, 0, tzinfo=_MOSCOW)
    monkeypatch.setattr("app.summary.collect.local_now", lambda _timezone: moment)
    db = tmp_path / "opencode.db"
    connection = _database(db)
    _session(connection, _SESSION, created_ms=_NOON)
    _message(connection, _SESSION, _Line("user-1", _NOON, "user", [("text", "сегодня")]))
    connection.commit()
    connection.close()

    _path, dump = collect_and_store(_day_config(tmp_path, db=db), "2026-10-05")

    assert (dump.sources.opencode, dump.stats.sessions, dump.stats.messages) == (
        TranscriptsSource(
            status=SourceStatus.OK,
            sessions=[
                TranscriptSession(
                    project=_DIRECTORY,
                    id=_SESSION,
                    messages=[TranscriptMessage(role="user", text="сегодня")],
                ),
            ],
        ),
        1,
        1,
    )


def test_disabled_source_stays_disabled_when_the_database_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    _path, dump = collect_and_store(
        _day_config(tmp_path, db=missing, enabled=False),
        "2026-10-05",
    )

    assert dump.sources.opencode == TranscriptsSource(status=SourceStatus.DISABLED)


def _day_config(root: Path, *, db: Path, enabled: bool = True) -> Config:
    return Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        state=StateConfig(dir=root / "state"),
        git=GitConfig(roots=[root / "repos"]),
        transcripts=TranscriptsConfig(roots=[root / "transcripts"]),
        telegram=TelegramConfig(enabled=False),
        opencode=OpencodeConfig(enabled=enabled, db=db),
    )
