"""Схема сырого дампа. Версия нужна, когда агрегация начнёт читать старые файлы."""

from datetime import date
from enum import StrEnum
from typing import Literal

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


class GitCommit(BaseModel):
    """Коммит из окна дня: метаданные и уже усечённый diff."""

    model_config = ConfigDict(extra="forbid")

    sha: str
    committed_at: AwareDatetime
    message: str
    files: list[str]
    diffstat: str
    diff: str | None = None


class GitDirty(BaseModel):
    """Незакоммиченные изменения. Полный diff сюда не кладём: его нет в задаче."""

    model_config = ConfigDict(extra="forbid")

    files: list[str]
    diffstat: str


class GitRepo(BaseModel):
    """Один локальный репозиторий, в котором нашлась работа за окно."""

    model_config = ConfigDict(extra="forbid")

    path: str
    commits: list[GitCommit]
    dirty: GitDirty | None = None


class TranscriptMessage(BaseModel):
    """Текст реплики. Блоки инструментов в дамп не попадают."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str


class TranscriptSession(BaseModel):
    """Одна сессия агента.

    `project` у Cursor — имя каталога в корне транскриптов, у OpenCode —
    путь `session.directory`.
    """

    model_config = ConfigDict(extra="forbid")

    project: str
    id: str
    messages: list[TranscriptMessage]


class TelegramMessage(BaseModel):
    """Сообщение из белого списка. Реакции и служебные события сюда не попадают."""

    model_config = ConfigDict(extra="forbid")

    sent_at: AwareDatetime
    author: str | None = None
    text: str


class TelegramChatLog(BaseModel):
    """Один чат за окно дня. `name` — подпись из конфига."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    messages: list[TelegramMessage]


class SourceDump(BaseModel):
    """Один источник за день: своё окно, статус и размер файла.

    Сводка дня из этих полей считается при печати, отдельным файлом не лежит.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    date: date
    window: Window
    generated_at: AwareDatetime
    status: SourceStatus
    code: ErrorCode | None = None
    reason: str | None = None
    bytes: int = 0
    truncations: list[str] = Field(default_factory=list)


class GitDump(SourceDump):
    """Коммиты и незакоммиченные файлы. Лежит в `git.json`."""

    repos: list[GitRepo] = Field(default_factory=list)


class SessionDump(SourceDump):
    """Сессии агента. `transcripts.json` и `opencode.json` устроены одинаково."""

    sessions: list[TranscriptSession] = Field(default_factory=list)


class TelegramDump(SourceDump):
    """Чаты белого списка. `unlisted_active` — активность вне конфига, без текста."""

    chats: list[TelegramChatLog] = Field(default_factory=list)
    unlisted_active: int = 0


Dump = GitDump | SessionDump | TelegramDump


def parse_git_dump(payload: str) -> GitDump:
    """Прочитать дамп git из JSON.

    Args:
        payload: Содержимое `git.json`.

    Returns:
        Разобранный дамп.
    """
    return GitDump.model_validate_json(payload)


def render_dump(dump: Dump) -> str:
    """Сериализовать дамп источника в JSON.

    Пустые `code` и `reason` не пишутся: у собранного источника их нет.

    Args:
        dump: Дамп одного источника.

    Returns:
        JSON-текст.
    """
    return dump.model_dump_json(by_alias=True, exclude_none=True)
