"""Схема сырого дампа. Версия нужна, когда агрегация начнёт читать старые файлы."""

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.errors import ErrorCode


class SourceStatus(StrEnum):
    """Итог одного сборщика. Недоступный источник не роняет весь запуск."""

    OK = "ok"
    EMPTY = "empty"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class Window(BaseModel):
    """Окно сбора. В JSON ключ `from` — начало, `to` — конец."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: AwareDatetime = Field(alias="from")
    to: AwareDatetime


class Source(BaseModel):
    """Общая часть источника: статус и, если собрать не удалось, код с причиной."""

    model_config = ConfigDict(extra="forbid")

    status: SourceStatus
    code: ErrorCode | None = None
    reason: str | None = None


class GitSource(Source):
    """Коммиты. Содержимое `repos` заполняет сборщик git."""

    repos: list[dict[str, Any]] = Field(default_factory=list)


class TranscriptsSource(Source):
    """Сессии Cursor. Содержимое `sessions` заполняет сборщик транскриптов."""

    sessions: list[dict[str, Any]] = Field(default_factory=list)


class TelegramSource(Source):
    """Чаты Telegram. Содержимое `chats` заполняет сборщик Telegram."""

    chats: list[dict[str, Any]] = Field(default_factory=list)


class Sources(BaseModel):
    """Три источника, каждый со своим статусом."""

    model_config = ConfigDict(extra="forbid")

    git: GitSource
    transcripts: TranscriptsSource
    telegram: TelegramSource


class Stats(BaseModel):
    """Сводка объёма для stdout и для порога двух проходов."""

    model_config = ConfigDict(extra="forbid")

    commits: int
    messages: int
    sessions: int
    bytes: int


class RawDump(BaseModel):
    """Сырой дамп одного дня."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    date: date
    window: Window
    generated_at: AwareDatetime
    sources: Sources
    stats: Stats
    truncations: list[str]


def parse_raw_dump(payload: str) -> RawDump:
    """Прочитать дамп из JSON.

    Args:
        payload: Содержимое файла дампа.

    Returns:
        Разобранный дамп.
    """
    return RawDump.model_validate_json(payload)


def render_raw_dump(dump: RawDump) -> str:
    """Сериализовать дамп в JSON, который читает `parse_raw_dump`.

    Пустые `code` и `reason` не пишутся: у успешного источника их нет.

    Args:
        dump: Дамп.

    Returns:
        JSON-текст.
    """
    return dump.model_dump_json(by_alias=True, exclude_none=True)
