"""CLI как публичная граница: что видит человек и агент, запустив команду."""

import pytest

from app.cli import main


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_without_arguments_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert "daily-summary" in capsys.readouterr().out
