"""Сборка сырых дампов: окно дня, источники, маскирование и каталог вне vault."""

import shutil
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal, NamedTuple
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
    Dump,
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
type SourceName = Literal["git", "transcripts", "opencode", "telegram"]
SOURCES: tuple[SourceName, ...] = ("git", "transcripts", "opencode", "telegram")
type _Loaded = tuple[GitDump | None, SessionDump | None, SessionDump | None, TelegramDump | None]


def local_now(timezone_name: str) -> datetime:
    """Текущий момент в часовом поясе заметок.

    Args:
        timezone_name: Имя пояса из `notes.timezone`.

    Returns:
        Момент с часовым поясом.
    """
    return datetime.now(ZoneInfo(timezone_name))


class CollectedDay(NamedTuple):
    """Дампы, которые лежат в каталоге, и сам каталог, если ретенция его оставила.

    Нет файла — поле `None`: этот источник не собирали. Если ретенция удалила
    каталог сразу после записи, поля — дампы этого прохода, их уже не прочитать.
    """

    directory: Path | None
    total_bytes: int
    git: GitDump | None
    transcripts: SessionDump | None
    opencode: SessionDump | None
    telegram: TelegramDump | None


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
    source: SourceName | None = None,
) -> CollectedDay:
    """Собрать день и записать дампы в каталог.

    Без `source` пишутся все четыре файла. С именем переписывается только он:
    остальные не создаются и не затираются. Каталоги — права 700, файлы — 600.
    После записи удаляются каталоги дней и одиночные JSON старше
    `state.raw_retention_days` относительно сегодняшнего дня, включая только
    что записанный день, если его дата тоже старше окна. Содержимое при
    удалении не читается. `progress` получает ход Telegram, пока обходятся
    чаты и диалоги. Сводка читает каталог, который остался после ретенции.

    Args:
        config: Загруженные настройки.
        day_text: `None` — сегодня до текущего момента. `today`, `yesterday`
            или `YYYY-MM-DD` — календарный день целиком.
        progress: Куда писать ход Telegram. `None` — молчать.
        source: Имя одного источника. `None` — полный сбор.

    Returns:
        Каталог дня, если он остался после ретенции, сумма размеров лежащих
        файлов и дамп на каждый существующий файл. У дампа своё окно.
    """
    now = local_now(config.notes.timezone)
    day, window = _resolve_window(day_text, now=now)
    written = _collect(
        config,
        _When(day, window, now),
        source=source,
        progress=progress,
    )
    directory, total_bytes, loaded = _store(config, day, written, today=now.date())
    dumps = loaded if loaded is not None else _kept(written)
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


def _collect(
    config: Config,
    when: _When,
    *,
    source: SourceName | None,
    progress: Callable[[str], None] | None,
) -> dict[SourceName, Dump]:
    names: tuple[SourceName, ...] = SOURCES if source is None else (source,)
    include_uncommitted = _include_uncommitted(when.day, generated_at=when.generated_at)
    return {
        name: _one(
            config,
            when,
            name,
            include_uncommitted=include_uncommitted,
            progress=progress,
        )
        for name in names
    }


def _one(
    config: Config,
    when: _When,
    source: SourceName,
    *,
    include_uncommitted: bool,
    progress: Callable[[str], None] | None,
) -> Dump:
    if source == "git":
        return _with_size(
            _mask_model(_git_dump(config, when, include_uncommitted=include_uncommitted)),
        )
    if source == "transcripts":
        return _with_size(_mask_model(_transcript_dump(config, when)))
    if source == "opencode":
        return _with_size(_mask_model(_opencode_dump(config, when)))
    return _with_size(_mask_model(_telegram_dump(config, when, progress=progress)))


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
    written: Mapping[SourceName, Dump],
    *,
    today: date,
) -> tuple[Path | None, int, _Loaded | None]:
    state_dir = config.state.dir
    _private_dir(state_dir)
    raw_dir = state_dir / "raw"
    _private_dir(raw_dir)
    day_dir = raw_dir / day.isoformat()
    _private_dir(day_dir)
    for name, dump in written.items():
        _write_private(day_dir / f"{name}.json", render_dump(dump))
    total = _total_bytes(day_dir)
    _purge(raw_dir, today=today, retention_days=config.state.raw_retention_days)
    if day_dir.is_dir():
        return day_dir, total, _load(day_dir)
    return None, total, None


def _total_bytes(day_dir: Path) -> int:
    return sum(
        path.stat().st_size for name in SOURCES if (path := day_dir / f"{name}.json").is_file()
    )


def _load(day_dir: Path) -> _Loaded:
    return (
        _read(day_dir / "git.json", GitDump),
        _read(day_dir / "transcripts.json", SessionDump),
        _read(day_dir / "opencode.json", SessionDump),
        _read(day_dir / "telegram.json", TelegramDump),
    )


def _read[D: (GitDump, SessionDump, TelegramDump)](path: Path, kind: type[D]) -> D | None:
    if not path.is_file():
        return None
    return kind.model_validate_json(path.read_text(encoding="utf-8"))


def _kept(written: Mapping[SourceName, Dump]) -> _Loaded:
    git = written.get("git")
    transcripts = written.get("transcripts")
    opencode = written.get("opencode")
    telegram = written.get("telegram")
    return (
        git if isinstance(git, GitDump) else None,
        transcripts if isinstance(transcripts, SessionDump) else None,
        opencode if isinstance(opencode, SessionDump) else None,
        telegram if isinstance(telegram, TelegramDump) else None,
    )


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
