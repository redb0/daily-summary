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
    CollectedDay,
    collect_and_store,
    local_now,
    resolve_day,
)
from app.summary.models import GitDump, SessionDump, SourceStatus, TelegramDump

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
    collect.add_argument("--date", type=_day_token, default=None)
    collect.add_argument("--config", type=Path, default=None)
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
        return _run_collect(config, args.date)
    if args.command == "write":
        return _run_write(config, args.date, apply=args.apply)
    if args.command == "init":
        return _run_init(config, relogin=args.relogin, login=args.login)
    return _run_chats(config)


def _run_collect(config: Config, token: str | None) -> int:
    day = collect_and_store(config, token, progress=_progress)
    sys.stdout.write(_report(day, threshold=config.summary.two_stage_threshold_bytes))
    return 0


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


def _report(day: CollectedDay, *, threshold: int) -> str:
    lines = [
        str(day.directory) if day.directory is not None else _NOT_KEPT,
        f"дата: {day.git.date.isoformat()}",
        f"байты: {day.total_bytes}",
        f"порог: {threshold}",
        f"порог превышен: {_exceeded(day.total_bytes, threshold)}",
        _git_line(day.git),
        _session_line("transcripts", day.transcripts),
        _session_line("opencode", day.opencode),
        _telegram_line(day.telegram),
        *_notes(day),
    ]
    return "\n".join(lines) + "\n"


def _exceeded(total: int, threshold: int) -> str:
    if total > threshold:
        return "да"
    return "нет"


def _git_line(dump: GitDump) -> str:
    commits = sum(len(repo.commits) for repo in dump.repos)
    status = _STATUS[dump.status]
    return f"git: {status}, репозиториев: {len(dump.repos)}, коммитов: {commits}"


def _session_line(name: str, dump: SessionDump) -> str:
    return f"{name}: {_STATUS[dump.status]}, сессий: {len(dump.sessions)}"


def _telegram_line(dump: TelegramDump) -> str:
    status = _STATUS[dump.status]
    return f"telegram: {status}, чатов: {len(dump.chats)}, unlisted: {dump.unlisted_active}"


def _notes(day: CollectedDay) -> list[str]:
    return [
        *day.git.truncations,
        *day.transcripts.truncations,
        *day.opencode.truncations,
        *day.telegram.truncations,
    ]


def _print_error(error: SummaryError) -> None:
    sys.stderr.write(f"code: {error.code}\n{error.message}\n{error.hint}\n")
