"""Сборщик транскриптов Cursor: фикстуры JSONL, без моков разбора."""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.collectors.transcripts import CollectedTranscripts, collect_transcripts
from app.config import Config, NotesConfig, TranscriptsConfig
from app.summary.models import TranscriptMessage, TranscriptSession, Window

_MOSCOW = ZoneInfo("Europe/Moscow")
_FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
_PROJECT = "home-vvoronov-projects-my-experience"
_SESSION = "11111111-1111-1111-1111-111111111111"


def _config(root: Path, *, head: int = 10, tail: int = 10) -> Config:
    return Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        transcripts=TranscriptsConfig(roots=[root], head_messages=head, tail_messages=tail),
    )


def _day(year: int, month: int, day: int) -> Window:
    return Window.model_validate(
        {
            "from": datetime(year, month, day, 0, 0, tzinfo=_MOSCOW),
            "to": datetime(year, month, day, 23, 59, 59, tzinfo=_MOSCOW),
        },
    )


def _write_session(root: Path, body: str, when: datetime, *, session: str = _SESSION) -> None:
    path = root / _PROJECT / "agent-transcripts" / session / f"{session}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(body)
    timestamp = when.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_fixture_becomes_session(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        (_FIXTURES / "basic.jsonl").read_text(),
        datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW),
    )

    collected = collect_transcripts(_config(tmp_path), _day(2026, 10, 5))

    assert collected == CollectedTranscripts(
        sessions=[
            TranscriptSession(
                project=_PROJECT,
                id=_SESSION,
                messages=[
                    TranscriptMessage(role="user", text="добавить сборщик"),
                    TranscriptMessage(role="assistant", text="сделаю"),
                ],
            ),
        ],
        truncations=[],
    )


def test_tool_use_and_wrappers_are_dropped(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        (_FIXTURES / "wrappers.jsonl").read_text(),
        datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW),
    )

    collected = collect_transcripts(_config(tmp_path), _day(2026, 10, 5))

    assert collected == CollectedTranscripts(
        sessions=[
            TranscriptSession(
                project=_PROJECT,
                id=_SESSION,
                messages=[
                    TranscriptMessage(role="user", text="заметка снаружи\nисправь баг"),
                    TranscriptMessage(role="assistant", text="смотрю\nготово"),
                ],
            ),
        ],
        truncations=[],
    )


def test_long_session_keeps_head_and_tail(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        (_FIXTURES / "long.jsonl").read_text(),
        datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW),
    )

    collected = collect_transcripts(_config(tmp_path, head=2, tail=1), _day(2026, 10, 5))

    assert collected == CollectedTranscripts(
        sessions=[
            TranscriptSession(
                project=_PROJECT,
                id=_SESSION,
                messages=[
                    TranscriptMessage(role="user", text="задача"),
                    TranscriptMessage(role="assistant", text="план"),
                    TranscriptMessage(role="user", text="итог"),
                ],
            ),
        ],
        truncations=[
            f"{_PROJECT}: сессия {_SESSION} усечена до 2 первых и 1 последних сообщений",
        ],
    )


def test_broken_line_is_skipped(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        (_FIXTURES / "broken.jsonl").read_text(),
        datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW),
    )

    collected = collect_transcripts(_config(tmp_path), _day(2026, 10, 5))

    assert collected == CollectedTranscripts(
        sessions=[
            TranscriptSession(
                project=_PROJECT,
                id=_SESSION,
                messages=[
                    TranscriptMessage(role="user", text="первое"),
                    TranscriptMessage(role="assistant", text="второе"),
                ],
            ),
        ],
        truncations=[],
    )


def test_window_follows_file_mtime(tmp_path: Path) -> None:
    body = (_FIXTURES / "basic.jsonl").read_text()
    start = "33333333-3333-3333-3333-333333333333"
    end = "44444444-4444-4444-4444-444444444444"
    messages = [
        TranscriptMessage(role="user", text="добавить сборщик"),
        TranscriptMessage(role="assistant", text="сделаю"),
    ]
    _write_session(tmp_path, body, datetime(2026, 10, 5, 0, 0, tzinfo=_MOSCOW), session=start)
    _write_session(tmp_path, body, datetime(2026, 10, 5, 23, 59, 59, tzinfo=_MOSCOW), session=end)
    _write_session(
        tmp_path,
        body,
        datetime(2026, 10, 4, 23, 59, 59, tzinfo=_MOSCOW),
        session="22222222-2222-2222-2222-222222222222",
    )
    _write_session(
        tmp_path,
        body,
        datetime(2026, 10, 6, 0, 0, tzinfo=_MOSCOW),
        session="55555555-5555-5555-5555-555555555555",
    )

    collected = collect_transcripts(_config(tmp_path), _day(2026, 10, 5))

    assert collected == CollectedTranscripts(
        sessions=[
            TranscriptSession(project=_PROJECT, id=start, messages=messages),
            TranscriptSession(project=_PROJECT, id=end, messages=messages),
        ],
        truncations=[],
    )
