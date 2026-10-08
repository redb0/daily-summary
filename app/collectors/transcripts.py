"""Сбор сессий Cursor из JSONL-транскриптов."""

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.collectors.replies import agent_role, json_object, truncate_replies
from app.config import Config
from app.summary.models import TranscriptMessage, TranscriptSession, Window

# Cursor подмешивает их в промпт. Содержимое — не слова пользователя.
_SERVICE_TAGS = (
    "agent_skills",
    "attached_files",
    "code_selection",
    "communication",
    "git_status",
    "image_files",
    "manually_attached_skills",
    "open_and_recently_viewed_files",
    "rules",
    "system_notification",
    "system_reminder",
    "timestamp",
    "user_info",
)
_SERVICE_BLOCK = re.compile(
    r"<(" + "|".join(_SERVICE_TAGS) + r")\b[^>]*>.*?</\1>",
    re.DOTALL,
)
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_BLANK_LINES = re.compile(r"\n{2,}")


class CollectedTranscripts(BaseModel):
    """Результат сборщика. Запись дампа и маскирование — не его работа."""

    model_config = ConfigDict(extra="forbid")

    sessions: list[TranscriptSession]
    truncations: list[str]


def collect_transcripts(config: Config, window: Window) -> CollectedTranscripts:
    """Собрать сессии, чей файл изменён внутри окна.

    Отметки времени внутри строк нет, поэтому сессия попадает в день целиком
    по mtime файла. Лимиты усечения берутся из загруженного конфига.

    Args:
        config: Загруженные настройки. Корни и лимиты берутся отсюда.
        window: Закрытый интервал сбора.

    Returns:
        Сессии, в которых остался текст, и записи об усечении.
    """
    sessions: list[TranscriptSession] = []
    truncations: list[str] = []
    for path in _discover(config.transcripts.roots):
        if not _in_window(path, window):
            continue
        collected = _collect_file(path, config)
        if collected is None:
            continue
        session, note = collected
        sessions.append(session)
        if note is not None:
            truncations.append(note)
    return CollectedTranscripts(sessions=sessions, truncations=truncations)


def _discover(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(
            path
            for path in root.glob("*/agent-transcripts/*/*.jsonl")
            if path.parent.name == path.stem
        )
    return sorted(found, key=lambda path: (path.stat().st_mtime_ns, path.as_posix()))


def _in_window(path: Path, window: Window) -> bool:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=window.from_.tzinfo)
    return window.from_ <= mtime <= window.to


def _collect_file(path: Path, config: Config) -> tuple[TranscriptSession, str | None] | None:
    messages = _messages(path)
    if not messages:
        return None
    # root / project / agent-transcripts / <uuid> / <uuid>.jsonl
    project = path.parents[2].name
    kept, note = truncate_replies(
        messages,
        head=config.transcripts.head_messages,
        tail=config.transcripts.tail_messages,
        project=project,
        session_id=path.stem,
    )
    session = TranscriptSession(project=project, id=path.stem, messages=kept)
    return session, note


def _messages(path: Path) -> list[TranscriptMessage]:
    messages: list[TranscriptMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        message = _message(line)
        if message is not None:
            messages.append(message)
    return messages


def _message(line: str) -> TranscriptMessage | None:
    payload = _payload(line)
    if payload is None:
        return None
    role = _role(payload)
    if role is None:
        return None
    text = _text(payload.get("message"))
    if role == "user":
        text = _strip_wrappers(text)
    if text == "":
        return None
    return TranscriptMessage(role=role, text=text)


def _payload(line: str) -> dict[str, object] | None:
    stripped = line.strip()
    if stripped == "":
        return None
    return json_object(stripped)


def _role(payload: dict[str, object]) -> Literal["user", "assistant"] | None:
    return agent_role(payload.get("role"))


def _text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts = [_block_text(block) for block in content]
    return "\n".join(part for part in parts if part).strip()


def _strip_wrappers(text: str) -> str:
    cleaned = _SERVICE_BLOCK.sub("", text)
    cleaned = _USER_QUERY.sub(r"\1", cleaned)
    return _BLANK_LINES.sub("\n", cleaned).strip()


def _block_text(block: object) -> str:
    if not isinstance(block, dict) or block.get("type") != "text":
        return ""
    text = block.get("text")
    if isinstance(text, str):
        return text
    return ""
