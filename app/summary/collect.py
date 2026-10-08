"""Сборка сырых дампов: окно дня, источники, маскирование и каталог вне vault."""

import shutil
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from app.atomic import replace_text
from app.collectors.git import collect_git
from app.collectors.opencode import collect_opencode
from app.collectors.telegram.collector import collect_telegram
from app.collectors.transcripts import collect_transcripts
from app.config import Config
from app.errors import ErrorCode, SummaryError
from app.summary.masking import mask_secrets
from app.summary.models import (
    GitDump,
    SessionDump,
    SourceStatus,
    TelegramDump,
    Window,
    render_dump,
)

_TODAY = "today"
_YESTERDAY = "yesterday"
RELATIVE_DAYS = frozenset({_TODAY, _YESTERDAY})


def local_now(timezone_name: str) -> datetime:
    """Текущий момент в часовом поясе заметок.

    Args:
        timezone_name: Имя пояса из `notes.timezone`.

    Returns:
        Момент с часовым поясом.
    """
    return datetime.now(ZoneInfo(timezone_name))


class CollectedDay(NamedTuple):
    """Четыре дампа полного сбора и каталог, если ретенция его оставила."""

    directory: Path | None
    total_bytes: int
    git: GitDump
    transcripts: SessionDump
    opencode: SessionDump
    telegram: TelegramDump


class _Dumps(NamedTuple):
    git: GitDump
    transcripts: SessionDump
    opencode: SessionDump
    telegram: TelegramDump


class _When(NamedTuple):
    """Дата, окно и момент одного прохода сбора. У каждого дампа копия этих полей."""

    day: date
    window: Window
    generated_at: datetime


def collect_and_store(
    config: Config,
    day_text: str | None,
    *,
    progress: Callable[[str], None] | None = None,
) -> CollectedDay:
    """Собрать день и записать четыре дампа в каталог.

    Каталоги создаются с правами 700, файлы — 600. После записи удаляются
    каталоги дней и одиночные JSON старше `state.raw_retention_days`
    относительно сегодняшнего дня, включая только что записанный день, если
    его дата тоже старше окна. Содержимое при удалении не читается.
    `progress` получает ход Telegram, пока обходятся чаты и диалоги.

    Args:
        config: Загруженные настройки.
        day_text: `None` — сегодня до текущего момента. `today`, `yesterday`
            или `YYYY-MM-DD` — календарный день целиком.
        progress: Куда писать ход Telegram. `None` — молчать.

    Returns:
        Каталог дня, если он остался после ретенции, сумма размеров файлов
        и четыре дампа. У каждого дампа своё окно и свой `bytes`.
    """
    now = local_now(config.notes.timezone)
    day, window = _resolve_window(day_text, now=now)
    dumps = _assemble(config, day, window, generated_at=now, progress=progress)
    directory, total_bytes = _store(config, day, dumps, today=now.date())
    return CollectedDay(directory, total_bytes, *dumps)


def _resolve_window(day_text: str | None, *, now: datetime) -> tuple[date, Window]:
    day = resolve_day(day_text, today=now.date())
    start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
    end = now if day_text is None else datetime.combine(day, time.max, tzinfo=now.tzinfo)
    return day, Window.model_validate({"from": start, "to": end})


def resolve_day(day_text: str | None, *, today: date) -> date:
    """Разобрать день сбора или записи.

    Args:
        day_text: `None` и `today` — `today`. `yesterday` — предыдущий день.
            Иначе дата `YYYY-MM-DD`.
        today: Сегодня в часовом поясе заметок.

    Returns:
        Календарный день.
    """
    if day_text is None or day_text == _TODAY:
        return today
    if day_text == _YESTERDAY:
        return today - timedelta(days=1)
    return date.fromisoformat(day_text)


def _include_uncommitted(day: date, *, generated_at: datetime) -> bool:
    """Включать ли незакоммиченные файлы в этот сбор.

    Да — запуск без даты и сегодняшний календарный день. Решение принимается
    вместе с окном: часы, ушедшие вперёд при обходе репозиториев, его не меняют.
    """
    return day == generated_at.date()


def _assemble(
    config: Config,
    day: date,
    window: Window,
    *,
    generated_at: datetime,
    progress: Callable[[str], None] | None,
) -> _Dumps:
    when = _When(day, window, generated_at)
    include_uncommitted = _include_uncommitted(day, generated_at=generated_at)
    return _Dumps(
        _with_size(_mask_model(_git_dump(config, when, include_uncommitted=include_uncommitted))),
        _with_size(_mask_model(_transcript_dump(config, when))),
        _with_size(_mask_model(_opencode_dump(config, when))),
        _with_size(_mask_model(_telegram_dump(config, when, progress=progress))),
    )


def _git_dump(config: Config, when: _When, *, include_uncommitted: bool) -> GitDump:
    try:
        collected = collect_git(config, when.window, include_uncommitted=include_uncommitted)
    except (OSError, SummaryError) as exc:
        return _failed(GitDump, exc, when)
    if not collected.repos:
        return _status_dump(GitDump, when, status=SourceStatus.EMPTY)
    return _status_dump(GitDump, when, status=SourceStatus.OK).model_copy(
        update={"repos": collected.repos, "truncations": list(collected.truncations)},
    )


def _transcript_dump(config: Config, when: _When) -> SessionDump:
    try:
        collected = collect_transcripts(config, when.window)
    except (OSError, SummaryError) as exc:
        return _failed(SessionDump, exc, when)
    if not collected.sessions:
        return _status_dump(SessionDump, when, status=SourceStatus.EMPTY)
    return _status_dump(SessionDump, when, status=SourceStatus.OK).model_copy(
        update={"sessions": collected.sessions, "truncations": list(collected.truncations)},
    )


def _opencode_dump(config: Config, when: _When) -> SessionDump:
    if not config.opencode.enabled:
        return _status_dump(SessionDump, when, status=SourceStatus.DISABLED)
    try:
        collected = collect_opencode(config, when.window)
    except (OSError, SummaryError) as exc:
        return _failed(SessionDump, exc, when)
    status = SourceStatus.OK if collected.sessions else SourceStatus.EMPTY
    return _status_dump(SessionDump, when, status=status).model_copy(
        update={"sessions": collected.sessions, "truncations": list(collected.truncations)},
    )


def _telegram_dump(
    config: Config,
    when: _When,
    *,
    progress: Callable[[str], None] | None,
) -> TelegramDump:
    if not config.telegram.enabled:
        return _status_dump(TelegramDump, when, status=SourceStatus.DISABLED)
    try:
        collected = collect_telegram(config, when.window, progress=progress)
    except (OSError, SummaryError) as exc:
        return _failed(TelegramDump, exc, when)
    if not collected.chats and collected.unlisted_active == 0:
        return _status_dump(TelegramDump, when, status=SourceStatus.EMPTY).model_copy(
            update={"truncations": list(collected.truncations)},
        )
    return _status_dump(TelegramDump, when, status=SourceStatus.OK).model_copy(
        update={
            "chats": collected.chats,
            "unlisted_active": collected.unlisted_active,
            "truncations": list(collected.truncations),
        },
    )


def _status_dump[D: (GitDump, SessionDump, TelegramDump)](
    kind: type[D],
    when: _When,
    *,
    status: SourceStatus,
) -> D:
    return kind(
        date=when.day,
        window=when.window,
        generated_at=when.generated_at,
        status=status,
    )


def _failed[D: (GitDump, SessionDump, TelegramDump)](
    kind: type[D],
    exc: Exception,
    when: _When,
) -> D:
    code, reason = _error_parts(exc)
    return _status_dump(kind, when, status=SourceStatus.UNAVAILABLE).model_copy(
        update={"code": code, "reason": reason},
    )


def _error_parts(exc: Exception) -> tuple[ErrorCode | None, str]:
    if isinstance(exc, SummaryError):
        return exc.code, exc.message
    reason = str(exc).strip()
    if reason == "":
        return None, exc.__class__.__name__
    return None, reason


def _mask_model[D: (GitDump, SessionDump, TelegramDump)](dump: D) -> D:
    payload = dump.model_dump(mode="json", by_alias=True, exclude_none=True)
    return type(dump).model_validate(_mask_value(payload))


def _mask_value(value: object) -> object:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _mask_value(item) for key, item in value.items()}
    return value


def _with_size[D: (GitDump, SessionDump, TelegramDump)](dump: D) -> D:
    placeholder = dump.model_copy(update={"bytes": 0})
    # В заготовке `bytes` равен 0 — одна цифра. Длина файла с настоящим числом
    # больше на ширину этого числа минус эта цифра.
    base_length = len(render_dump(placeholder).encode()) - 1
    width = len(str(base_length))
    size = base_length + width
    while len(str(size)) != width:
        width = len(str(size))
        size = base_length + width
    return dump.model_copy(update={"bytes": size})


def _store(
    config: Config,
    day: date,
    dumps: _Dumps,
    *,
    today: date,
) -> tuple[Path | None, int]:
    state_dir = config.state.dir
    _private_dir(state_dir)
    raw_dir = state_dir / "raw"
    _private_dir(raw_dir)
    day_dir = raw_dir / day.isoformat()
    _private_dir(day_dir)
    written = (
        ("git", dumps.git),
        ("transcripts", dumps.transcripts),
        ("opencode", dumps.opencode),
        ("telegram", dumps.telegram),
    )
    for name, dump in written:
        _write_private(day_dir / f"{name}.json", render_dump(dump))
    total = sum((day_dir / f"{name}.json").stat().st_size for name, _dump in written)
    _purge(raw_dir, today=today, retention_days=config.state.raw_retention_days)
    if day_dir.is_dir():
        return day_dir, total
    return None, total


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_private(path: Path, text: str) -> None:
    replace_text(path, text, mode=0o600)


def _purge(raw_dir: Path, *, today: date, retention_days: int) -> None:
    cutoff = today - timedelta(days=retention_days)
    for path in list(raw_dir.iterdir()):
        dumped = _dump_date(path)
        if dumped is not None and dumped < cutoff:
            _remove(path)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def _dump_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None
