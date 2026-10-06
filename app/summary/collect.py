"""Сборка сырого дампа: окно дня, источники, маскирование и файл вне vault."""

import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.collectors.git import collect_git
from app.collectors.telegram.collector import collect_telegram
from app.collectors.transcripts import collect_transcripts
from app.config import Config
from app.errors import ErrorCode, SummaryError
from app.summary.masking import mask_secrets
from app.summary.models import (
    GitSource,
    RawDump,
    Sources,
    SourceStatus,
    Stats,
    TelegramSource,
    TranscriptsSource,
    Window,
    render_raw_dump,
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


def collect_and_store(config: Config, day_text: str | None) -> tuple[Path | None, RawDump]:
    """Собрать день и записать JSON.

    Каталог дампов создаётся с правами 700, файл — 600. После записи удаляются
    дампы старше `state.raw_retention_days` относительно сегодняшнего дня,
    включая только что записанный, если его дата тоже старше окна.

    Args:
        config: Загруженные настройки.
        day_text: `None` — сегодня до текущего момента. `today`, `yesterday`
            или `YYYY-MM-DD` — календарный день целиком.

    Returns:
        Путь к файлу, если он остался после ретенции, и дамп. `stats.bytes`
        равен длине JSON.
    """
    now = local_now(config.notes.timezone)
    day, window = _resolve_window(day_text, now=now)
    dump = _with_size(_mask_dump(_assemble(config, day, window, generated_at=now)))
    path = _store(config, day, render_raw_dump(dump), today=now.date())
    return path, dump


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


def _assemble(config: Config, day: date, window: Window, *, generated_at: datetime) -> RawDump:
    sources, truncations = _sources(config, window)
    return RawDump(
        schema_version=1,
        date=day,
        window=window,
        generated_at=generated_at,
        sources=sources,
        stats=_stats(sources),
        truncations=truncations,
    )


def _sources(config: Config, window: Window) -> tuple[Sources, list[str]]:
    git, git_notes = _git_source(config, window)
    transcripts, transcript_notes = _transcript_source(config, window)
    telegram, telegram_notes = _telegram_source(config, window)
    sources = Sources(git=git, transcripts=transcripts, telegram=telegram)
    return sources, [*git_notes, *transcript_notes, *telegram_notes]


def _git_source(config: Config, window: Window) -> tuple[GitSource, list[str]]:
    try:
        collected = collect_git(config, window)
    except (OSError, SummaryError) as exc:
        return _failed_git(exc), []
    if not collected.repos:
        return GitSource(status=SourceStatus.EMPTY), []
    return GitSource(status=SourceStatus.OK, repos=collected.repos), list(collected.truncations)


def _transcript_source(config: Config, window: Window) -> tuple[TranscriptsSource, list[str]]:
    try:
        collected = collect_transcripts(config, window)
    except (OSError, SummaryError) as exc:
        return _failed_transcripts(exc), []
    if not collected.sessions:
        return TranscriptsSource(status=SourceStatus.EMPTY), []
    return (
        TranscriptsSource(status=SourceStatus.OK, sessions=collected.sessions),
        list(collected.truncations),
    )


def _telegram_source(config: Config, window: Window) -> tuple[TelegramSource, list[str]]:
    if not config.telegram.enabled:
        return TelegramSource(status=SourceStatus.DISABLED), []
    try:
        collected = collect_telegram(config, window)
    except (OSError, SummaryError) as exc:
        return _failed_telegram(exc), []
    if not collected.chats and collected.unlisted_active == 0:
        return TelegramSource(status=SourceStatus.EMPTY), list(collected.truncations)
    return (
        TelegramSource(
            status=SourceStatus.OK,
            chats=collected.chats,
            unlisted_active=collected.unlisted_active,
        ),
        list(collected.truncations),
    )


def _failed_telegram(exc: Exception) -> TelegramSource:
    code, reason = _error_parts(exc)
    return TelegramSource(status=SourceStatus.UNAVAILABLE, code=code, reason=reason)


def _failed_git(exc: Exception) -> GitSource:
    code, reason = _error_parts(exc)
    return GitSource(status=SourceStatus.UNAVAILABLE, code=code, reason=reason)


def _failed_transcripts(exc: Exception) -> TranscriptsSource:
    code, reason = _error_parts(exc)
    return TranscriptsSource(status=SourceStatus.UNAVAILABLE, code=code, reason=reason)


def _error_parts(exc: Exception) -> tuple[ErrorCode | None, str]:
    if isinstance(exc, SummaryError):
        return exc.code, exc.message
    reason = str(exc).strip()
    if reason == "":
        return None, exc.__class__.__name__
    return None, reason


def _stats(sources: Sources) -> Stats:
    commits = sum(len(repo.commits) for repo in sources.git.repos)
    sessions = sources.transcripts.sessions
    messages = sum(len(session.messages) for session in sessions)
    messages += sum(len(chat.messages) for chat in sources.telegram.chats)
    return Stats(commits=commits, messages=messages, sessions=len(sessions), bytes=0)


def _mask_dump(dump: RawDump) -> RawDump:
    payload = dump.model_dump(mode="json", by_alias=True, exclude_none=True)
    return RawDump.model_validate(_mask_value(payload))


def _mask_value(value: object) -> object:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _mask_value(item) for key, item in value.items()}
    return value


def _with_size(dump: RawDump) -> RawDump:
    placeholder = dump.model_copy(update={"stats": dump.stats.model_copy(update={"bytes": 0})})
    # В заготовке `bytes` равен 0 — одна цифра. Длина файла с настоящим числом
    # больше на ширину этого числа минус эта цифра.
    base_length = len(render_raw_dump(placeholder).encode()) - 1
    width = len(str(base_length))
    size = base_length + width
    while len(str(size)) != width:
        width = len(str(size))
        size = base_length + width
    return dump.model_copy(update={"stats": dump.stats.model_copy(update={"bytes": size})})


def _store(config: Config, day: date, text: str, *, today: date) -> Path | None:
    state_dir = config.state.dir
    _private_dir(state_dir)
    raw_dir = state_dir / "raw"
    _private_dir(raw_dir)
    path = raw_dir / f"{day.isoformat()}.json"
    _write_private(path, text)
    _purge(raw_dir, today=today, retention_days=config.state.raw_retention_days)
    if path.is_file():
        return path
    return None


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_private(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _purge(raw_dir: Path, *, today: date, retention_days: int) -> None:
    cutoff = today - timedelta(days=retention_days)
    for path in raw_dir.glob("*.json"):
        dumped = _dump_date(path)
        if dumped is not None and dumped < cutoff:
            path.unlink()


def _dump_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None
