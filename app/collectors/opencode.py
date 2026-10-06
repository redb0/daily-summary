"""Сбор сессий OpenCode из SQLite. Сеть и живая база здесь не открываются."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import Config
from app.summary.models import TranscriptMessage, TranscriptSession, Window

_Role = Literal["user", "assistant"]
_MESSAGES = """
SELECT session.id, session.directory, message.id, message.data
FROM message
JOIN session ON session.id = message.session_id
WHERE message.time_created >= ? AND message.time_created <= ?
ORDER BY message.time_created, message.id
"""
_PARTS = """
SELECT part.message_id, part.data
FROM part
JOIN message ON message.id = part.message_id
WHERE message.time_created >= ? AND message.time_created <= ?
  AND json_extract(part.data, '$.type') = 'text'
  AND IFNULL(json_extract(part.data, '$.synthetic'), 0) != 1
ORDER BY part.time_created, part.id
"""


class CollectedOpencode(BaseModel):
    """Результат сборщика. Запись дампа — не его работа."""

    model_config = ConfigDict(extra="forbid")

    sessions: list[TranscriptSession]
    truncations: list[str]


def collect_opencode(config: Config, window: Window) -> CollectedOpencode:
    """Собрать реплики, чьё `time_created` внутри окна.

    База открывается только на чтение. В дамп попадает текст `user` и
    `assistant` без синтетических частей.

    Args:
        config: Загруженные настройки. Путь и лимиты берутся отсюда.
        window: Закрытый интервал сбора.

    Returns:
        Сессии, в которых остался текст, и записи об усечении.

    Raises:
        FileNotFoundError: Файла базы нет.
        OSError: Файл есть, но SQLite его не читает.
    """
    path = config.opencode.db
    if not path.is_file():
        message = f"База OpenCode не найдена: {path}"
        raise FileNotFoundError(message)
    try:
        connection = _connect(path)
        try:
            sessions, notes = _sessions(
                connection,
                config,
                start_ms=_epoch_ms(window.from_),
                end_ms=_epoch_ms(window.to),
            )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        message = f"База OpenCode недоступна: {path}"
        raise OSError(message) from exc
    return CollectedOpencode(sessions=sessions, truncations=notes)


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp()) * 1000 + moment.microsecond // 1000


def _sessions(
    connection: sqlite3.Connection,
    config: Config,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[list[TranscriptSession], list[str]]:
    texts = _part_texts(connection, start_ms=start_ms, end_ms=end_ms)
    directories: dict[str, str] = {}
    messages_by_session: dict[str, list[TranscriptMessage]] = {}
    order: list[str] = []
    for session_id, directory, message_id, data in _message_rows(
        connection,
        start_ms=start_ms,
        end_ms=end_ms,
    ):
        message = _message(data, texts.get(message_id, ""))
        if message is None:
            continue
        if session_id not in messages_by_session:
            directories[session_id] = directory
            messages_by_session[session_id] = []
            order.append(session_id)
        messages_by_session[session_id].append(message)
    sessions: list[TranscriptSession] = []
    notes: list[str] = []
    for session_id in order:
        directory = directories[session_id]
        messages = messages_by_session[session_id]
        kept, note = _truncate(
            messages,
            head=config.opencode.head_messages,
            tail=config.opencode.tail_messages,
            project=directory,
            session_id=session_id,
        )
        sessions.append(TranscriptSession(project=directory, id=session_id, messages=kept))
        if note is not None:
            notes.append(note)
    return sessions, notes


def _message_rows(
    connection: sqlite3.Connection,
    *,
    start_ms: int,
    end_ms: int,
) -> list[tuple[str, str, str, str]]:
    rows = connection.execute(_MESSAGES, (start_ms, end_ms)).fetchall()
    return [
        (str(session_id), str(directory), str(message_id), str(data))
        for session_id, directory, message_id, data in rows
    ]


def _part_texts(
    connection: sqlite3.Connection,
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    rows = connection.execute(_PARTS, (start_ms, end_ms)).fetchall()
    for message_id, data in rows:
        text = _part_text(str(data))
        if text == "":
            continue
        grouped.setdefault(str(message_id), []).append(text)
    return {message_id: "\n".join(parts) for message_id, parts in grouped.items()}


def _message(data: str, text: str) -> TranscriptMessage | None:
    role = _role(data)
    if role is None or text == "":
        return None
    return TranscriptMessage(role=role, text=text)


def _role(data: str) -> _Role | None:
    parsed = _object(data)
    if parsed is None:
        return None
    match parsed.get("role"):
        case "user" | "assistant" as role:
            return role
        case _:
            return None


def _part_text(data: str) -> str:
    parsed = _object(data)
    if parsed is None:
        return ""
    text = parsed.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _object(data: str) -> dict[str, object] | None:
    try:
        parsed: object = json.loads(data)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return {str(key): value for key, value in parsed.items()}
    return None


def _truncate(
    messages: list[TranscriptMessage],
    *,
    head: int,
    tail: int,
    project: str,
    session_id: str,
) -> tuple[list[TranscriptMessage], str | None]:
    if len(messages) <= head + tail:
        return messages, None
    tail_part = messages[-tail:] if tail else []
    note = f"{project}: сессия {session_id} усечена до {head} первых и {tail} последних сообщений"
    return [*messages[:head], *tail_part], note
