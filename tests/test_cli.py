"""CLI как публичная граница: что видит человек и агент, запустив команду."""

import tomllib
from pathlib import Path

import pytest

from app.cli import main


def declared_version() -> str:
    """Версия из `pyproject.toml` — независимый от кода источник истины.

    Returns:
        Строка версии, объявленная в метаданных проекта.
    """
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])


def test_version_flag_prints_declared_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"daily-summary {declared_version()}"


def test_help_flag_describes_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    assert "usage: daily-summary" in capsys.readouterr().out


def test_without_arguments_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert "usage: daily-summary" in capsys.readouterr().out
