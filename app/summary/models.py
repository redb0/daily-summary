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


class Source(BaseModel):
    """Общая часть источника: статус и, если собрать не удалось, код с причиной."""

    model_config = ConfigDict(extra="forbid")

    status: SourceStatus
    code: ErrorCode | None = None
    reason: str | None = None


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


class GitSource(Source):
    """Коммиты. Содержимое `repos` заполняет сборщик git."""

    repos: list[GitRepo] = Field(default_factory=list)


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


class TranscriptsSource(Source):
    """Сессии агента. Одно и то же поле у Cursor и у OpenCode."""

    sessions: list[TranscriptSession] = Field(default_factory=list)


def _disabled_opencode() -> TranscriptsSource:
    """Старые дампы не знали про OpenCode.

    Returns:
        Выключенный источник без сессий.
    """
    return TranscriptsSource(status=SourceStatus.DISABLED)


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


class TelegramSource(Source):
    """Чаты Telegram. Содержимое заполняет сборщик Telegram.

    `unlisted_active` — сколько чатов с активностью в окне нет в конфиге.
    Содержимое этих чатов не читается.
    """

    chats: list[TelegramChatLog] = Field(default_factory=list)
    unlisted_active: int = 0


class Sources(BaseModel):
    """Источники дня, каждый со своим статусом.

    `opencode` по умолчанию выключен: старый дамп без этого ключа читается.
    """

    model_config = ConfigDict(extra="forbid")

    git: GitSource
    transcripts: TranscriptsSource
    opencode: TranscriptsSource = Field(default_factory=_disabled_opencode)
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
