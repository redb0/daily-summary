"""Точка входа команды `daily-summary`."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version

DISTRIBUTION = "daily-summary"


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Разобрать аргументы и выполнить команду.

    Args:
        argv: Аргументы без имени программы. `None` — взять из `sys.argv`.

    Returns:
        Код возврата процесса.
    """
    parser = build_parser()
    parser.parse_args(argv)
    # Подкоманд пока нет — до тикетов 02 и 06 единственное осмысленное
    # поведение без аргументов это справка.
    parser.print_help()
    return 0
