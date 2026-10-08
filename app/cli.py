"""Точка входа команды `daily-summary`."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence
from datetime import date
from importlib.metadata import version
from pathlib import Path

from app.collectors.telegram.chats import load_chats
from app.collectors.telegram.init import run_init
from app.config import Config, load_config
from app.errors import SummaryError
from app.notes.writer import write_note
from app.summary.collect import (
    RELATIVE_DAYS,
    SOURCES,
    CollectedDay,
    SourceName,
    collect_and_store,
    load_stored_day,
    local_now,
    resolve_day,
)
from app.summary.models import GitDump, SessionDump, SourceStatus, TelegramDump
from app.summary.show import render_git, render_sessions, render_telegram

DISTRIBUTION = "daily-summary"
_NOT_KEPT = "дамп не сохранён: дата старше ретенции"


def build_parser() -> argparse.ArgumentParser:
    """Собрать разборщик аргументов.

    Returns:
        Разборщик верхнего уровня.
    """
    parser = argparse.ArgumentParser(
        prog=DISTRIBUTION,
        description="Саммари проделанной за день работы: задачи и принятые решения.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version(DISTRIBUTION)}",
    )
    commands = parser.add_subparsers(dest="command", required=False)
    collect = commands.add_parser("collect", help="Собрать сырой дамп за день.")
    collect.add_argument("source", nargs="?", choices=SOURCES, default=None)
    collect.add_argument("--date", type=_day_token, default=None)
    collect.add_argument("--config", type=Path, default=None)
    show = commands.add_parser("show", help="Показать сводку или тело источника.")
    show.add_argument("source", nargs="?", choices=SOURCES, default=None)
    show.add_argument("--date", type=_day_token, default=None)
    show.add_argument("--config", type=Path, default=None)
    write = commands.add_parser("write", help="Записать блок итогов в ежедневную заметку.")
    write.add_argument("--date", type=_iso_day, required=True)
    write.add_argument("--body", type=_stdin_body, required=True)
    write.add_argument("--apply", action="store_true")
    write.add_argument("--config", type=Path, default=None)
    init = commands.add_parser("init", help="Настроить доступ к Telegram в своём терминале.")
    init.add_argument("--relogin", action="store_true")
    init.add_argument("--login", choices=("qr", "code"), default=None)
    init.add_argument("--config", type=Path, default=None)
    chats = commands.add_parser("chats", help="Показать чаты и фрагмент конфига.")
    chats.add_argument("--config", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Разобрать аргументы и выполнить команду.

    Args:
        argv: Аргументы без имени программы. `None` — взять из `sys.argv`.

    Returns:
        Код возврата процесса.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return _dispatch(args)
    except SummaryError as error:
        _print_error(error)
        return 1
    except FileNotFoundError as exc:
        sys.stderr.write(f"Файл конфигурации не найден: {exc}\n")
        return 1


def _day_token(value: str) -> str:
    if value in RELATIVE_DAYS or _is_iso_day(value):
        return value
    message = "ожидается YYYY-MM-DD, today или yesterday"
    raise argparse.ArgumentTypeError(message)


def _iso_day(value: str) -> str:
    if _is_iso_day(value):
        return value
    message = "ожидается YYYY-MM-DD"
    raise argparse.ArgumentTypeError(message)


def _is_iso_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _stdin_body(value: str) -> str:
    if value != "-":
        message = "тело читается из stdin: укажите --body -"
        raise argparse.ArgumentTypeError(message) from None
    return value


def _dispatch(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.command == "collect":
        return _run_collect(config, args.date, args.source)
    if args.command == "show":
        return _run_show(config, args.date, args.source)
    if args.command == "write":
        return _run_write(config, args.date, apply=args.apply)
    if args.command == "init":
        return _run_init(config, relogin=args.relogin, login=args.login)
    return _run_chats(config)


def _run_collect(config: Config, token: str | None, source: SourceName | None) -> int:
    day = collect_and_store(config, token, progress=_progress, source=source)
    sys.stdout.write(_report(day, threshold=config.summary.two_stage_threshold_bytes))
    return 0


def _run_show(config: Config, token: str | None, source: SourceName | None) -> int:
    day_date, stored = load_stored_day(config, token)
    if source is None:
        sys.stdout.write(
            _report(
                stored,
                threshold=config.summary.two_stage_threshold_bytes,
                empty_date=day_date,
            ),
        )
        return 0
    sys.stdout.write(_source_text(source, stored))
    return 0


def _source_text(source: SourceName, day: CollectedDay) -> str:
    missing = _missing_text(source, day)
    if missing is not None:
        return missing
    return _body(source, day)


def _missing_text(source: SourceName, day: CollectedDay) -> str | None:
    if source == "git" and day.git is None:
        return _git_line(None) + "\n"
    if source == "transcripts" and day.transcripts is None:
        return _session_line("transcripts", None) + "\n"
    if source == "opencode" and day.opencode is None:
        return _session_line("opencode", None) + "\n"
    if source == "telegram" and day.telegram is None:
        return _telegram_line(None) + "\n"
    return None


def _body(source: SourceName, day: CollectedDay) -> str:
    if source == "git" and day.git is not None:
        return render_git(day.git)
    if source == "transcripts" and day.transcripts is not None:
        return render_sessions(day.transcripts)
    if source == "opencode" and day.opencode is not None:
        return render_sessions(day.opencode)
    if source == "telegram" and day.telegram is not None:
        return render_telegram(day.telegram)
    message = "в дне нет дампа источника"
    raise RuntimeError(message)


def _progress(line: str) -> None:
    sys.stderr.write(f"{line}\n")
    sys.stderr.flush()


def _run_write(config: Config, token: str, *, apply: bool) -> int:
    day = resolve_day(token, today=local_now(config.notes.timezone).date())
    result = write_note(config, day, sys.stdin.read(), apply=apply)
    sys.stdout.write(result.diff)
    return 0


def _run_init(config: Config, *, relogin: bool, login: str | None) -> int:
    account = run_init(
        config,
        relogin=relogin,
        login=login,
        ask=_ask_credential,
        choose=_ask_login,
    )
    sys.stdout.write(f"Вошли как {account}\n")
    return 0


def _ask_login() -> str:
    while True:
        answer = input("Вход (qr/code): ").strip().lower()
        if answer in {"qr", "code"}:
            return answer
        sys.stderr.write("Ожидается qr или code.\n")


def _ask_credential(key: str) -> str:
    if key == "TG_API_HASH":
        return getpass.getpass("TG_API_HASH: ")
    if key == "TG_API_ID":
        return input("TG_API_ID (https://my.telegram.org/apps): ")
    return input("TG_PHONE (+7999…): ")


def _run_chats(config: Config) -> int:
    sys.stdout.write(asyncio.run(load_chats(config)))
    return 0


_STATUS = {
    SourceStatus.OK: "ok",
    SourceStatus.EMPTY: "пустой",
    SourceStatus.DISABLED: "выключен",
    SourceStatus.UNAVAILABLE: "недоступен",
}


def _report(
    day: CollectedDay,
    *,
    threshold: int,
    empty_date: date | None = None,
) -> str:
    shown = _day_date(day, empty_date)
    shared = _same_window(day)
    lines = [
        *_heading(day),
        f"дата: {shown.isoformat()}",
        f"байты: {day.total_bytes}",
        f"порог: {threshold}",
        f"порог превышен: {_exceeded(day.total_bytes, threshold)}",
        _git_line(day.git) + _window_suffix(day.git, shared=shared),
        _session_line("transcripts", day.transcripts)
        + _window_suffix(day.transcripts, shared=shared),
        _session_line("opencode", day.opencode) + _window_suffix(day.opencode, shared=shared),
        _telegram_line(day.telegram) + _window_suffix(day.telegram, shared=shared),
        *_notes(day),
    ]
    return "\n".join(lines) + "\n"


def _same_window(day: CollectedDay) -> bool:
    windows = [
        dump.window
        for dump in (day.git, day.transcripts, day.opencode, day.telegram)
        if dump is not None
    ]
    return all(item == windows[0] for item in windows)


def _window_suffix(dump: GitDump | SessionDump | TelegramDump | None, *, shared: bool) -> str:
    if shared or dump is None:
        return ""
    start = dump.window.from_.isoformat()
    end = dump.window.to.isoformat()
    return f", окно: {start}..{end}"


def _heading(day: CollectedDay) -> list[str]:
    if day.directory is not None:
        return [str(day.directory)]
    if day.git or day.transcripts or day.opencode or day.telegram:
        return [_NOT_KEPT]
    return []


def _day_date(day: CollectedDay, fallback: date | None) -> date:
    dump = day.git or day.transcripts or day.opencode or day.telegram
    if dump is not None:
        return dump.date
    if fallback is not None:
        return fallback
    message = "в дне нет ни одного дампа"
    raise RuntimeError(message)


def _exceeded(total: int, threshold: int) -> str:
    if total > threshold:
        return "да"
    return "нет"


def _git_line(dump: GitDump | None) -> str:
    if dump is None:
        return "git: не собран, репозиториев: 0, коммитов: 0"
    commits = sum(len(repo.commits) for repo in dump.repos)
    status = _STATUS[dump.status]
    return f"git: {status}, репозиториев: {len(dump.repos)}, коммитов: {commits}"


def _session_line(name: str, dump: SessionDump | None) -> str:
    if dump is None:
        return f"{name}: не собран, сессий: 0"
    return f"{name}: {_STATUS[dump.status]}, сессий: {len(dump.sessions)}"


def _telegram_line(dump: TelegramDump | None) -> str:
    if dump is None:
        return "telegram: не собран, чатов: 0, unlisted: 0"
    status = _STATUS[dump.status]
    return f"telegram: {status}, чатов: {len(dump.chats)}, unlisted: {dump.unlisted_active}"


def _notes(day: CollectedDay) -> list[str]:
    notes: list[str] = []
    for dump in (day.git, day.transcripts, day.opencode, day.telegram):
        if dump is not None:
            notes.extend(dump.truncations)
    return notes


def _print_error(error: SummaryError) -> None:
    sys.stderr.write(f"code: {error.code}\n{error.message}\n{error.hint}\n")
