"""Точка входа команды `daily-summary`.

Домен не печатает ничего сам и не решает, как выглядит ошибка — он бросает
`SummaryError`. Формат вывода выбирается здесь: этот модуль единственный, кто
знает, что работает в терминале, а не внутри вызова инструмента агентом.
"""

import argparse
from collections.abc import Sequence

from app import __version__


def build_parser() -> argparse.ArgumentParser:
    """Собрать разборщик аргументов.

    Returns:
        Разборщик верхнего уровня.
    """
    parser = argparse.ArgumentParser(
        prog="daily-summary",
        description="Саммари проделанной за день работы: задачи и принятые решения.",
    )
    parser.add_argument("--version", action="version", version=__version__)
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
