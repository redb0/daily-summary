"""Общие куски реплик агента: роль, JSON и усечение длинной сессии."""

import json
from typing import Literal

from app.summary.models import TranscriptMessage

_Role = Literal["user", "assistant"]


def json_object(text: str) -> dict[str, object] | None:
    """Разобрать JSON-объект. Битый текст и не-объект — `None`.

    Args:
        text: Одна строка или поле `data`.

    Returns:
        Словарь со строковыми ключами либо `None`.
    """
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return {str(key): value for key, value in parsed.items()}
    return None


def agent_role(value: object) -> _Role | None:
    """Оставить только реплики человека и модели.

    Args:
        value: Поле `role`.

    Returns:
        `user`, `assistant` либо `None`.
    """
    match value:
        case "user" | "assistant" as role:
            return role
        case _:
            return None


def truncate_replies(
    messages: list[TranscriptMessage],
    *,
    head: int,
    tail: int,
    project: str,
    session_id: str,
) -> tuple[list[TranscriptMessage], str | None]:
    """Оставить начало и конец длинной сессии.

    Args:
        messages: Реплики сессии по порядку.
        head: Сколько первых оставить.
        tail: Сколько последних оставить.
        project: Подпись проекта в пометке.
        session_id: Идентификатор сессии в пометке.

    Returns:
        Усечённый список и текст пометки. Пометки нет, если лимит не превышен.
    """
    if len(messages) <= head + tail:
        return messages, None
    tail_part = messages[-tail:] if tail else []
    note = f"{project}: сессия {session_id} усечена до {head} первых и {tail} последних сообщений"
    return [*messages[:head], *tail_part], note
