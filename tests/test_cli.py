"""CLI как публичная граница: что видит человек и агент, запустив команду."""

import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.cli import main

_MOSCOW = ZoneInfo("Europe/Moscow")
_AUTHOR = "vvoronov@mwnts.ru"
_UNCOMMITTED = {
    "files": ["note.txt"],
    "diffstat": " note.txt | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)",
}
_SECRET_ENV = ("TG_API_ID", "TG_API_HASH", "TG_PHONE")


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


def test_collect_writes_private_dump_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment.replace(hour=18))
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "добавил заметку", moment)
    _session(tmp_path / "transcripts", moment, text="сделал штуку")
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    names = ("git", "transcripts", "opencode", "telegram")
    files = [day_dir / f"{name}.json" for name in names]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    total = sum(path.stat().st_size for path in files)
    window = {
        "from": "2026-10-05T00:00:00+03:00",
        "to": "2026-10-05T23:59:59.999999+03:00",
    }
    assert (
        exit_code,
        capsys.readouterr().out,
        [path.stat().st_mode & 0o777 for path in files],
        [(tmp_path / "state").stat().st_mode & 0o777, (day_dir.parent).stat().st_mode & 0o777],
        day_dir.stat().st_mode & 0o777,
        (day_dir.parent / "2026-10-05.json").exists(),
        [
            (
                item["schema_version"],
                item["date"],
                item["window"],
                item["generated_at"],
                item["bytes"],
            )
            for item in payloads
        ],
        payloads[0]["repos"][0]["commits"][0]["message"],
        payloads[1]["sessions"][0]["messages"],
        (payloads[2]["status"], payloads[3]["status"]),
        (home / ".local" / "state").exists(),
    ) == (
        0,
        (
            f"{day_dir}\n"
            "дата: 2026-10-05\n"
            f"байты: {total}\n"
            "порог: 100000\n"
            "порог превышен: нет\n"
            "git: ok, репозиториев: 1, коммитов: 1\n"
            "transcripts: ok, сессий: 1\n"
            "opencode: выключен, сессий: 0\n"
            "telegram: выключен, чатов: 0, unlisted: 0\n"
        ),
        [0o600, 0o600, 0o600, 0o600],
        [0o700, 0o700],
        0o700,
        False,
        [
            (2, "2026-10-05", window, "2026-10-05T18:00:00+03:00", path.stat().st_size)
            for path in files
        ],
        "добавил заметку",
        [{"role": "user", "text": "сделал штуку"}],
        ("disabled", "disabled"),
        False,
    )


def test_collect_one_source_again_replaces_only_its_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    morning = datetime(2026, 10, 5, 10, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, morning)
    _session(tmp_path / "transcripts", morning.replace(hour=9), text="утром")
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "утром", morning.replace(hour=9))
    _commit(repo, "вечером", morning.replace(hour=17))
    config = _config_file(tmp_path)

    morning_code = main(["collect", "git", "--config", str(config)])
    transcripts_code = main(["collect", "transcripts", "--config", str(config)])
    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    kept = (day_dir / "transcripts.json").read_text(encoding="utf-8")
    capsys.readouterr()

    _freeze(monkeypatch, morning.replace(hour=18))
    evening_code = main(["collect", "git", "--config", str(config)])

    git_path = day_dir / "git.json"
    transcripts_path = day_dir / "transcripts.json"
    git_payload = json.loads(git_path.read_text(encoding="utf-8"))
    messages = [commit["message"] for commit in git_payload["repos"][0]["commits"]]
    total = git_path.stat().st_size + transcripts_path.stat().st_size
    evening_out = capsys.readouterr().out
    show_code = main(["show", "--config", str(config)])
    summary = (
        f"{day_dir}\n"
        "дата: 2026-10-05\n"
        f"байты: {total}\n"
        "порог: 100000\n"
        "порог превышен: нет\n"
        "git: ok, репозиториев: 1, коммитов: 2, "
        "окно: 2026-10-05T00:00:00+03:00..2026-10-05T18:00:00+03:00\n"
        "transcripts: ok, сессий: 1, "
        "окно: 2026-10-05T00:00:00+03:00..2026-10-05T10:00:00+03:00\n"
        "opencode: не собран, сессий: 0\n"
        "telegram: не собран, чатов: 0, unlisted: 0\n"
    )
    assert (
        morning_code,
        transcripts_code,
        evening_code,
        sorted(path.name for path in day_dir.iterdir()),
        transcripts_path.read_text(encoding="utf-8"),
        json.loads(kept)["window"]["to"],
        git_payload["window"]["to"],
        messages,
        evening_out,
        show_code,
        capsys.readouterr().out,
    ) == (
        0,
        0,
        0,
        ["git.json", "transcripts.json"],
        kept,
        "2026-10-05T10:00:00+03:00",
        "2026-10-05T18:00:00+03:00",
        ["утром", "вечером"],
        summary,
        0,
        summary,
    )


@pytest.mark.parametrize("command", ["collect", "show"])
def test_unknown_source_name_is_a_usage_error(command: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([command, "slack"])

    assert exit_info.value.code == 2


def test_show_without_source_repeats_the_summary_and_does_not_collect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 18, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "добавил заметку", moment.replace(hour=12))
    _session(tmp_path / "transcripts", moment.replace(hour=12), text="сделал штуку")
    config = _config_file(tmp_path)

    collect_code = main(["collect", "--config", str(config)])
    collected = capsys.readouterr().out
    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    before = _files(day_dir)

    _freeze(monkeypatch, moment.replace(hour=21))
    show_code = main(["show", "--config", str(config)])

    assert (collect_code, show_code, capsys.readouterr().out, _files(day_dir)) == (
        0,
        0,
        collected,
        before,
    )


def test_show_missing_day_lists_four_uncollected_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _freeze(monkeypatch, datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW))
    config = _config_file(tmp_path)

    exit_code = main(["show", "--date", "2026-10-05", "--config", str(config)])

    assert (exit_code, capsys.readouterr().out, (tmp_path / "state").exists()) == (
        0,
        (
            "дата: 2026-10-05\n"
            "байты: 0\n"
            "порог: 100000\n"
            "порог превышен: нет\n"
            "git: не собран, репозиториев: 0, коммитов: 0\n"
            "transcripts: не собран, сессий: 0\n"
            "opencode: не собран, сессий: 0\n"
            "telegram: не собран, чатов: 0, unlisted: 0\n"
        ),
        False,
    )


def test_show_missing_dump_says_not_collected_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    _session(tmp_path / "transcripts", moment, text="утром")
    config = _config_file(tmp_path)
    main(["collect", "transcripts", "--date", "2026-10-05", "--config", str(config)])
    capsys.readouterr()
    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    before = _files(day_dir)

    exit_code = main(["show", "git", "--date", "2026-10-05", "--config", str(config)])

    assert (exit_code, capsys.readouterr().out, _files(day_dir)) == (
        0,
        "git: не собран, репозиториев: 0, коммитов: 0\n",
        before,
    )


def test_show_git_prints_the_diff_and_keeps_uncommitted_apart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_home(monkeypatch, tmp_path / "home")
    sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    _plant(
        tmp_path,
        "git",
        {
            "schema_version": 2,
            "date": "2026-10-05",
            "window": {
                "from": "2026-10-05T00:00:00+03:00",
                "to": "2026-10-05T18:00:00+03:00",
            },
            "generated_at": "2026-10-05T18:00:00+03:00",
            "status": "ok",
            "bytes": 1,
            "repos": [
                {
                    "path": "/repos/demo",
                    "commits": [
                        {
                            "sha": sha,
                            "committed_at": "2026-10-05T12:00:00+03:00",
                            "message": "добавил заметку",
                            "files": ["note.txt", "other.txt"],
                            "diffstat": " note.txt | 1 +\n 1 file changed, 1 insertion(+)",
                            "diff": "diff --git a/note.txt b/note.txt\n+добавил заметку\n",
                        },
                    ],
                    "dirty": {
                        "files": ["note.txt"],
                        "diffstat": (
                            " note.txt | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
                        ),
                    },
                },
            ],
        },
    )
    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    before = _files(day_dir)
    config = _config_file(tmp_path)

    exit_code = main(["show", "git", "--date", "2026-10-05", "--config", str(config)])

    assert (exit_code, capsys.readouterr().out, _files(day_dir)) == (
        0,
        (
            "# /repos/demo\n"
            "## commit 2026-10-05T12:00:00+03:00\n"
            "добавил заметку\n"
            "files: note.txt, other.txt\n"
            " note.txt | 1 +\n"
            " 1 file changed, 1 insertion(+)\n"
            "diff --git a/note.txt b/note.txt\n"
            "+добавил заметку\n"
            "\n"
            "## в процессе\n"
            "note.txt\n"
            " note.txt | 2 +-\n"
            " 1 file changed, 1 insertion(+), 1 deletion(-)\n"
        ),
        before,
    )


def test_show_omits_session_and_chat_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_home(monkeypatch, tmp_path / "home")
    session_id = "11111111-1111-1111-1111-111111111111"
    chat_id = 424242
    replies = [("user", "сделал штуку"), ("assistant", "готово")]
    _plant(tmp_path, "transcripts", _session_dump([("proj", session_id, replies)]))
    _plant(tmp_path, "opencode", _session_dump([("oc", session_id, replies)]))
    _plant(
        tmp_path,
        "telegram",
        {
            "schema_version": 2,
            "date": "2026-10-05",
            "window": {
                "from": "2026-10-05T00:00:00+03:00",
                "to": "2026-10-05T18:00:00+03:00",
            },
            "generated_at": "2026-10-05T18:00:00+03:00",
            "status": "ok",
            "bytes": 1,
            "unlisted_active": 3,
            "chats": [
                {
                    "id": chat_id,
                    "name": "работа",
                    "messages": [
                        {
                            "sent_at": "2026-10-05T12:00:00+03:00",
                            "author": "Аня",
                            "text": "посмотрела diff",
                        },
                    ],
                },
            ],
        },
    )
    before = _files(tmp_path / "state" / "raw" / "2026-10-05")
    config = _config_file(tmp_path)
    args = ["--date", "2026-10-05", "--config", str(config)]

    transcripts_code = main(["show", "transcripts", *args])
    transcripts_out = capsys.readouterr().out
    opencode_code = main(["show", "opencode", *args])
    opencode_out = capsys.readouterr().out
    telegram_code = main(["show", "telegram", *args])
    telegram_out = capsys.readouterr().out

    assert (
        transcripts_code,
        transcripts_out,
        opencode_code,
        opencode_out,
        telegram_code,
        telegram_out,
        _files(tmp_path / "state" / "raw" / "2026-10-05"),
    ) == (
        0,
        "## proj\nuser: сделал штуку\nassistant: готово\n",
        0,
        "## oc\nuser: сделал штуку\nassistant: готово\n",
        0,
        "unlisted_active 3\n# работа\n2026-10-05T12:00:00+03:00 Аня: посмотрела diff\n",
        before,
    )


def test_show_drops_summary_calls_and_keeps_a_later_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_home(monkeypatch, tmp_path / "home")
    _plant(
        tmp_path,
        "transcripts",
        _session_dump(
            [
                (
                    "proj",
                    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    [
                        ("user", "/daily-summary"),
                        ("assistant", "собрал"),
                        (
                            "user",
                            (
                                "Briefly inform the user about the task result "
                                "and perform any follow-up actions (if needed)."
                            ),
                        ),
                        ("assistant", "молчу"),
                        ("user", "разбери скилл"),
                        ("assistant", "разобрал"),
                    ],
                ),
                (
                    "only-call",
                    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    [
                        ("user", "/daily-summary yesterday"),
                        ("assistant", "пусто"),
                    ],
                ),
                (
                    "only-brief",
                    "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    [
                        ("user", "Briefly inform the user about the task result"),
                        ("assistant", "готово"),
                    ],
                ),
                (
                    "keep",
                    "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    [("user", "/daily-summary разобрать скилл")],
                ),
            ],
        ),
    )
    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    before = _files(day_dir)
    config = _config_file(tmp_path)

    exit_code = main(
        ["show", "transcripts", "--date", "2026-10-05", "--config", str(config)],
    )

    assert (exit_code, capsys.readouterr().out, _files(day_dir)) == (
        0,
        (
            "## proj\n"
            "assistant: собрал\n"
            "assistant: молчу\n"
            "user: разбери скилл\n"
            "assistant: разобрал\n"
            "\n"
            "## keep\n"
            "user: /daily-summary разобрать скилл\n"
        ),
        before,
    )


def test_show_marks_review_axis_and_repeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_home(monkeypatch, tmp_path / "home")
    standards = "You are the STANDARDS axis of a two-axis code review\nсмотри diff"
    standards_spaced = "You  are the STANDARDS   axis of a two-axis code review\nсмотри diff"
    spec = "You are the SPEC axis of a two-axis code review"
    _plant(
        tmp_path,
        "opencode",
        _session_dump(
            [
                ("alpha", "11111111-1111-1111-1111-111111111111", [("user", standards)]),
                ("alpha", "22222222-2222-2222-2222-222222222222", [("user", standards_spaced)]),
                ("alpha", "33333333-3333-3333-3333-333333333333", [("user", spec)]),
                ("beta", "44444444-4444-4444-4444-444444444444", [("user", spec)]),
                ("gamma", "55555555-5555-5555-5555-555555555555", [("user", "сделай штуку")]),
                ("delta", "77777777-7777-7777-7777-777777777777", [("user", "один\nдва")]),
                ("delta", "88888888-8888-8888-8888-888888888888", [("user", "один два")]),
                (
                    "zeta",
                    "99999999-9999-9999-9999-999999999999",
                    [("user", "You are the\nSTANDARDS axis of a two-axis code review")],
                ),
                (
                    "alpha",
                    "66666666-6666-6666-6666-666666666666",
                    [
                        ("user", "/daily-summary"),
                        ("assistant", "сначала"),
                        ("user", standards),
                    ],
                ),
            ],
        ),
    )
    config = _config_file(tmp_path)

    exit_code = main(["show", "opencode", "--date", "2026-10-05", "--config", str(config)])

    assert (exit_code, capsys.readouterr().out) == (
        0,
        (
            "## alpha review\n"
            f"user: {standards}\n"
            "\n"
            "## alpha review repeat\n"
            f"user: {standards_spaced}\n"
            "\n"
            "## alpha review\n"
            f"user: {spec}\n"
            "\n"
            "## beta review\n"
            f"user: {spec}\n"
            "\n"
            "## gamma\n"
            "user: сделай штуку\n"
            "\n"
            "## delta\n"
            "user: один\n"
            "два\n"
            "\n"
            "## delta\n"
            "user: один два\n"
            "\n"
            "## zeta\n"
            "user: You are the\n"
            "STANDARDS axis of a two-axis code review\n"
            "\n"
            "## alpha review repeat\n"
            "assistant: сначала\n"
            f"user: {standards}\n"
        ),
    )


def test_show_threshold_uses_raw_file_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_home(monkeypatch, tmp_path / "home")
    _plant(
        tmp_path,
        "transcripts",
        _session_dump(
            [
                (
                    "proj",
                    "11111111-1111-1111-1111-111111111111",
                    [("user", "Briefly inform the user about the task result " + "x" * 5000)],
                ),
            ],
        ),
    )
    config = _config_file(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8") + "\n[summary]\ntwo_stage_threshold_bytes = 1000\n",
        encoding="utf-8",
    )
    size = (tmp_path / "state" / "raw" / "2026-10-05" / "transcripts.json").stat().st_size

    exit_code = main(["show", "--date", "2026-10-05", "--config", str(config)])

    assert (exit_code, capsys.readouterr().out.splitlines()[1:5]) == (
        0,
        [
            "дата: 2026-10-05",
            f"байты: {size}",
            "порог: 1000",
            "порог превышен: да",
        ],
    )


def test_full_collect_after_partial_rewrites_every_dump(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    morning = datetime(2026, 10, 5, 10, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, morning)
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "утром", morning.replace(hour=9))
    _commit(repo, "вечером", morning.replace(hour=17))
    config = _config_file(tmp_path)

    partial_code = main(["collect", "git", "--config", str(config)])
    partial = _stored(tmp_path, "2026-10-05", "git")

    _freeze(monkeypatch, morning.replace(hour=18))
    full_code = main(["collect", "--config", str(config)])

    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    evening = _stored(tmp_path, "2026-10-05", "git")
    transcripts = _stored(tmp_path, "2026-10-05", "transcripts")
    opencode = _stored(tmp_path, "2026-10-05", "opencode")
    telegram = _stored(tmp_path, "2026-10-05", "telegram")
    partial_messages = [commit["message"] for commit in partial["repos"][0]["commits"]]
    evening_messages = [commit["message"] for commit in evening["repos"][0]["commits"]]
    assert (
        partial_code,
        partial["window"]["to"],
        partial_messages,
        full_code,
        sorted(path.name for path in day_dir.iterdir()),
        evening["window"]["to"],
        evening_messages,
        (
            transcripts["status"],
            transcripts["window"]["to"],
            opencode["status"],
            telegram["status"],
        ),
    ) == (
        0,
        "2026-10-05T10:00:00+03:00",
        ["утром"],
        0,
        ["git.json", "opencode.json", "telegram.json", "transcripts.json"],
        "2026-10-05T18:00:00+03:00",
        ["утром", "вечером"],
        ("empty", "2026-10-05T18:00:00+03:00", "disabled", "disabled"),
    )


def test_purged_partial_collect_reports_dumps_that_were_on_disk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _freeze(monkeypatch, datetime(2026, 10, 6, 10, 0, tzinfo=_MOSCOW))
    day_dir = tmp_path / "state" / "raw" / "2026-09-01"
    day_dir.mkdir(parents=True)
    (day_dir / "transcripts.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "date": "2026-09-01",
                "window": {
                    "from": "2026-09-01T00:00:00+03:00",
                    "to": "2026-09-01T10:00:00+03:00",
                },
                "generated_at": "2026-09-01T10:00:00+03:00",
                "status": "ok",
                "bytes": 1,
                "sessions": [
                    {
                        "project": "proj",
                        "id": "11111111-1111-1111-1111-111111111111",
                        "messages": [{"role": "user", "text": "было"}],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    config = _config_file(tmp_path)

    exit_code = main(["collect", "git", "--date", "2026-09-01", "--config", str(config)])

    lines = capsys.readouterr().out.splitlines()
    assert (exit_code, lines[0], lines[5:9], day_dir.exists()) == (
        0,
        "дамп не сохранён: дата старше ретенции",
        [
            (
                "git: пустой, репозиториев: 0, коммитов: 0, "
                "окно: 2026-09-01T00:00:00+03:00..2026-09-01T23:59:59.999999+03:00"
            ),
            (
                "transcripts: ok, сессий: 1, "
                "окно: 2026-09-01T00:00:00+03:00..2026-09-01T10:00:00+03:00"
            ),
            "opencode: не собран, сессий: 0",
            "telegram: не собран, чатов: 0, unlisted: 0",
        ],
        False,
    )


def test_collect_default_window_stops_at_now_and_date_today_covers_the_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, now)
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "утром", now.replace(hour=14))
    _commit(repo, "вечером", now.replace(hour=16))
    config = _config_file(tmp_path)

    default_code = main(["collect", "--config", str(config)])
    default_payload = _stored(tmp_path, "2026-10-05", "git")
    capsys.readouterr()

    today_code = main(["collect", "--date", "today", "--config", str(config)])
    today_payload = _stored(tmp_path, "2026-10-05", "git")

    default_messages = [commit["message"] for commit in default_payload["repos"][0]["commits"]]
    today_messages = [commit["message"] for commit in today_payload["repos"][0]["commits"]]
    assert (
        default_code,
        default_payload["window"],
        default_messages,
        today_code,
        today_payload["window"]["to"],
        today_messages,
        capsys.readouterr().out.splitlines()[0],
    ) == (
        0,
        {"from": "2026-10-05T00:00:00+03:00", "to": "2026-10-05T15:00:00+03:00"},
        ["утром"],
        0,
        "2026-10-05T23:59:59.999999+03:00",
        ["утром", "вечером"],
        str(tmp_path / "state" / "raw" / "2026-10-05"),
    )


def test_collect_without_date_keeps_uncommitted_after_the_window_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    captured = datetime(2026, 10, 5, 15, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, captured)
    monkeypatch.setattr(
        "app.collectors.git._now",
        lambda _zone: captured + timedelta(minutes=1),
    )
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "утром", captured.replace(hour=9))
    (repo / "note.txt").write_text("ещё правка\n", encoding="utf-8")
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--config", str(config)])

    payload = _stored(tmp_path, "2026-10-05", "git")
    assert (
        exit_code,
        payload["window"]["to"],
        payload["repos"][0].get("dirty"),
    ) == (
        0,
        "2026-10-05T15:00:00+03:00",
        _UNCOMMITTED,
    )


def test_collect_named_past_day_omits_uncommitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    today = datetime(2026, 10, 6, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, today)
    # Часы проверки внутри прошедшего дня: решение сбора всё равно их не включает.
    monkeypatch.setattr(
        "app.collectors.git._now",
        lambda _zone: datetime(2026, 10, 5, 18, 0, tzinfo=_MOSCOW),
    )
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "вчера", datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW))
    (repo / "note.txt").write_text("сегодняшняя правка\n", encoding="utf-8")
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    payload = _stored(tmp_path, "2026-10-05", "git")
    repo_dump = payload["repos"][0]
    assert (
        exit_code,
        payload["window"]["to"],
        [commit["message"] for commit in repo_dump["commits"]],
        repo_dump.get("dirty"),
    ) == (
        0,
        "2026-10-05T23:59:59.999999+03:00",
        ["вчера"],
        None,
    )


def test_collect_today_covers_the_day_and_keeps_uncommitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    captured = datetime(2026, 10, 5, 15, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, captured)
    # Конец суток уже позади: незакоммиченное держится решением на старте, не часами.
    monkeypatch.setattr(
        "app.collectors.git._now",
        lambda _zone: datetime(2026, 10, 6, 0, 1, tzinfo=_MOSCOW),
    )
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "утром", captured.replace(hour=9))
    _commit(repo, "вечером", captured.replace(hour=16))
    (repo / "note.txt").write_text("ещё правка\n", encoding="utf-8")
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--date", "today", "--config", str(config)])

    payload = _stored(tmp_path, "2026-10-05", "git")
    repo_dump = payload["repos"][0]
    assert (
        exit_code,
        payload["window"]["to"],
        [commit["message"] for commit in repo_dump["commits"]],
        repo_dump.get("dirty"),
    ) == (
        0,
        "2026-10-05T23:59:59.999999+03:00",
        ["утром", "вечером"],
        _UNCOMMITTED,
    )


def test_collect_yesterday_is_the_previous_calendar_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _freeze(monkeypatch, datetime(2026, 10, 6, 10, 0, tzinfo=_MOSCOW))
    repo = tmp_path / "repos" / "demo"
    _commit(repo, "вчера", datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW))
    _commit(repo, "сегодня", datetime(2026, 10, 6, 9, 0, tzinfo=_MOSCOW))
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--date", "yesterday", "--config", str(config)])

    payload = _stored(tmp_path, "2026-10-05", "git")
    messages = [commit["message"] for commit in payload["repos"][0]["commits"]]
    assert (exit_code, payload["date"], payload["window"]["from"], messages) == (
        0,
        "2026-10-05",
        "2026-10-05T00:00:00+03:00",
        ["вчера"],
    )


def test_collect_drops_dumps_older_than_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _freeze(monkeypatch, datetime(2026, 10, 6, 10, 0, tzinfo=_MOSCOW))
    raw = tmp_path / "state" / "raw"
    raw.mkdir(parents=True)
    for name in ("2026-09-01.json", "2026-09-21.json", "2026-09-22.json", "notes.json"):
        (raw / name).write_text("{}", encoding="utf-8")
    expired = raw / "2026-09-21"
    expired.mkdir()
    (expired / "git.json").write_text("{не json", encoding="utf-8")
    kept = raw / "2026-09-22"
    kept.mkdir()
    (kept / "git.json").write_text("оставить", encoding="utf-8")
    config = _config_file(tmp_path, retention_days=14)

    exit_code = main(["collect", "--date", "2026-09-01", "--config", str(config)])

    names = sorted(path.name for path in raw.iterdir())
    lines = capsys.readouterr().out.splitlines()
    assert (
        exit_code,
        names,
        lines[0],
        lines[1],
        (kept / "git.json").read_text(encoding="utf-8"),
    ) == (
        0,
        ["2026-09-22", "2026-09-22.json", "notes.json"],
        "дамп не сохранён: дата старше ретенции",
        "дата: 2026-09-01",
        "оставить",
    )


def test_collect_summary_says_the_threshold_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _freeze(monkeypatch, datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW))
    config = _config_file(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8") + "\n[summary]\ntwo_stage_threshold_bytes = 1\n",
        encoding="utf-8",
    )

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    total = sum(path.stat().st_size for path in day_dir.glob("*.json"))
    assert (exit_code, capsys.readouterr().out.splitlines()[1:5]) == (
        0,
        [
            "дата: 2026-10-05",
            f"байты: {total}",
            "порог: 1",
            "порог превышен: да",
        ],
    )


def test_collect_summary_prints_truncations_without_commit_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    _commit(tmp_path / "repos" / "demo", "уникальная строка", moment)
    config = _config_file(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "[git]\n",
            "[git]\nmax_diff_lines_per_day = 1\n",
            1,
        ),
        encoding="utf-8",
    )

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    commit = _stored(tmp_path, "2026-10-05", "git")["repos"][0]["commits"][0]
    total = sum(path.stat().st_size for path in day_dir.glob("*.json"))
    note = (
        f"demo: diff коммита {commit['sha']} снят из-за лимита "
        "1 строк на день: оставлен только diffstat"
    )
    assert (
        exit_code,
        commit["message"],
        commit.get("diff"),
        capsys.readouterr().out,
    ) == (
        0,
        "уникальная строка",
        None,
        (
            f"{day_dir}\n"
            "дата: 2026-10-05\n"
            f"байты: {total}\n"
            "порог: 100000\n"
            "порог превышен: нет\n"
            "git: ok, репозиториев: 1, коммитов: 1\n"
            "transcripts: пустой, сессий: 0\n"
            "opencode: выключен, сессий: 0\n"
            "telegram: выключен, чатов: 0, unlisted: 0\n"
            f"{note}\n"
        ),
    )


def test_collect_ignores_a_legacy_single_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    _commit(tmp_path / "repos" / "demo", "добавил заметку", moment)
    raw = tmp_path / "state" / "raw"
    raw.mkdir(parents=True)
    legacy = raw / "2026-10-05.json"
    legacy.write_text("это не json", encoding="utf-8")
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    git = _stored(tmp_path, "2026-10-05", "git")
    assert (
        exit_code,
        legacy.read_text(encoding="utf-8"),
        git["repos"][0]["commits"][0]["message"],
    ) == (
        0,
        "это не json",
        "добавил заметку",
    )


def test_collect_marks_telegram_unavailable_and_keeps_other_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    _commit(tmp_path / "repos" / "demo", "коммит без чатов", moment)
    env = home / ".config" / "daily-summary" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("TG_API_ID=1\nTG_API_HASH=hash\nTG_PHONE=+79990000000\n", encoding="utf-8")
    config = _config_file(tmp_path, telegram=True)

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    git = _stored(tmp_path, "2026-10-05", "git")
    telegram = _stored(tmp_path, "2026-10-05", "telegram")
    reason = f"Сессия Telegram не найдена: {tmp_path / 'state' / 'session.session'}"
    assert (
        exit_code,
        git["status"],
        len(git["repos"][0]["commits"]),
        telegram["status"],
        telegram["code"],
        telegram["reason"],
        capsys.readouterr().out.splitlines()[-1],
    ) == (
        0,
        "ok",
        1,
        "unavailable",
        "NO_SESSION",
        reason,
        "telegram: недоступен, чатов: 0, unlisted: 0",
    )


def test_collect_masks_secrets_before_writing_the_dump(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    token = "ghp_" + "a" * 36
    _commit(tmp_path / "repos" / "demo", f"утечка {token} в коммите", moment)
    config = _config_file(tmp_path)

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    raw = (tmp_path / "state" / "raw" / "2026-10-05" / "git.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    message = payload["repos"][0]["commits"][0]["message"]
    assert (exit_code, message, token if token in raw else "") == (
        0,
        "утечка [REDACTED] в коммите",
        "",
    )


def test_collect_marks_missing_opencode_unavailable_and_keeps_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    _commit(tmp_path / "repos" / "demo", "коммит на месте", moment)
    missing = tmp_path / "missing.db"
    config = _config_file(tmp_path, opencode_db=missing)

    exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])

    git = _stored(tmp_path, "2026-10-05", "git")
    transcripts = _stored(tmp_path, "2026-10-05", "transcripts")
    opencode = _stored(tmp_path, "2026-10-05", "opencode")
    reason = f"База OpenCode не найдена: {missing}"
    assert (
        exit_code,
        git["status"],
        transcripts["status"],
        opencode["status"],
        opencode["reason"],
        capsys.readouterr().out.splitlines()[7],
    ) == (
        0,
        "ok",
        "empty",
        "unavailable",
        reason,
        "opencode: недоступен, сессий: 0",
    )


def test_collect_keeps_commits_when_a_transcript_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    moment = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    _freeze(monkeypatch, moment)
    _commit(tmp_path / "repos" / "demo", "коммит на месте", moment)
    _session(tmp_path / "transcripts", moment, text="секретный тред")
    session = "11111111-1111-1111-1111-111111111111"
    transcript = (
        tmp_path / "transcripts" / "proj" / "agent-transcripts" / session / f"{session}.jsonl"
    )
    transcript.chmod(0)
    config = _config_file(tmp_path)
    try:
        exit_code = main(["collect", "--date", "2026-10-05", "--config", str(config)])
    finally:
        transcript.chmod(0o644)

    day_dir = tmp_path / "state" / "raw" / "2026-10-05"
    git = _stored(tmp_path, "2026-10-05", "git")
    transcripts = _stored(tmp_path, "2026-10-05", "transcripts")
    total = sum(path.stat().st_size for path in day_dir.glob("*.json"))
    reason = f"[Errno 13] Permission denied: '{transcript}'"
    report = capsys.readouterr().out
    assert (
        exit_code,
        git["repos"][0]["commits"][0]["message"],
        transcripts["status"],
        transcripts["reason"],
        transcripts["sessions"],
        report,
    ) == (
        0,
        "коммит на месте",
        "unavailable",
        reason,
        [],
        (
            f"{day_dir}\n"
            "дата: 2026-10-05\n"
            f"байты: {total}\n"
            "порог: 100000\n"
            "порог превышен: нет\n"
            "git: ok, репозиториев: 1, коммитов: 1\n"
            "transcripts: недоступен, сессий: 0\n"
            "opencode: выключен, сессий: 0\n"
            "telegram: выключен, чатов: 0, unlisted: 0\n"
        ),
    )


def test_write_without_apply_prints_diff_and_leaves_the_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    note = _note(tmp_path / "notes")
    original = note.read_bytes()
    config = _config_file(tmp_path, daily_dir=note.parent)
    monkeypatch.setattr(sys, "stdin", StringIO(_BODY))

    exit_code = main(
        ["write", "--date", "2026-10-05", "--body", "-", "--config", str(config)],
    )

    assert (exit_code, note.read_bytes(), capsys.readouterr().out) == (
        0,
        original,
        _DIFF,
    )


def test_write_apply_appends_the_block_and_keeps_handwritten_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    note = _note(tmp_path / "notes")
    config = _config_file(tmp_path, daily_dir=note.parent)
    monkeypatch.setattr(sys, "stdin", StringIO(_BODY))

    exit_code = main(
        ["write", "--date", "2026-10-05", "--body", "-", "--apply", "--config", str(config)],
    )

    assert (exit_code, note.read_text(encoding="utf-8")) == (0, _WRITTEN)


def test_write_without_notes_dir_reports_the_code_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    config = _config_file(tmp_path)
    monkeypatch.setattr(sys, "stdin", StringIO(_BODY))

    exit_code = main(
        ["write", "--date", "2026-10-05", "--body", "-", "--apply", "--config", str(config)],
    )

    error = capsys.readouterr().err
    assert (exit_code, error.splitlines()[:2], list(tmp_path.rglob("*.md"))) == (
        1,
        ["code: NO_NOTES_DIR", "Не задан каталог ежедневных заметок."],
        [],
    )


def test_collect_reports_a_missing_config_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_home(monkeypatch, tmp_path / "home")
    missing = tmp_path / "missing.toml"

    exit_code = main(["collect", "--config", str(missing)])

    assert (exit_code, capsys.readouterr().err) == (
        1,
        f"Файл конфигурации не найден: {missing}\n",
    )


@pytest.mark.parametrize("command", ["collect", "show"])
def test_invalid_day_token_is_a_usage_error(command: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--date", "завтра"])

    assert exit_info.value.code == 2


def test_write_rejects_today_and_yesterday() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["write", "--date", "yesterday", "--body", "-"])

    assert exit_info.value.code == 2


_BODY = """\
## 🤖 Итоги дня

### Сделано
- собрал дамп
"""
_NOTE = "# день\n\nрукописное\n"
_WRITTEN = f"{_NOTE}<!-- auto:start -->\n{_BODY}<!-- auto:end -->\n"
_DIFF = (
    "--- 2026-10-05.md\n"
    "+++ 2026-10-05.md\n"
    "@@ -1,3 +1,9 @@\n"
    " # день\n"
    " \n"
    " рукописное\n"
    "+<!-- auto:start -->\n"
    "+## 🤖 Итоги дня\n"
    "+\n"
    "+### Сделано\n"
    "+- собрал дамп\n"
    "+<!-- auto:end -->\n"
)


def _note(daily_dir: Path) -> Path:
    daily_dir.mkdir(parents=True)
    note = daily_dir / "2026-10-05.md"
    note.write_text("# день\n\nрукописное\n", encoding="utf-8")
    return note


def _isolate_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr("app.summary.collect.local_now", lambda _timezone: moment)


def _stored(root: Path, day: str, source: str) -> Any:  # noqa: ANN401
    path = root / "state" / "raw" / day / f"{source}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _session_dump(
    sessions: list[tuple[str, str, list[tuple[str, str]]]],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "date": "2026-10-05",
        "window": {
            "from": "2026-10-05T00:00:00+03:00",
            "to": "2026-10-05T18:00:00+03:00",
        },
        "generated_at": "2026-10-05T18:00:00+03:00",
        "status": "ok",
        "bytes": 1,
        "sessions": [
            {
                "project": project,
                "id": session_id,
                "messages": [{"role": role, "text": text} for role, text in messages],
            }
            for project, session_id, messages in sessions
        ],
    }


def _plant(root: Path, source: str, payload: dict[str, Any]) -> None:
    day_dir = root / "state" / "raw" / "2026-10-05"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{source}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _files(day_dir: Path) -> list[tuple[str, int, bytes]]:
    return [
        (path.name, path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(day_dir.iterdir())
    ]


def _config_file(
    root: Path,
    *,
    telegram: bool = False,
    opencode_db: Path | None = None,
    daily_dir: Path | None = None,
    retention_days: int = 14,
) -> Path:
    notes_dir = "" if daily_dir is None else str(daily_dir)
    path = root / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[notes]",
                f'daily_dir = "{notes_dir}"',
                'template = ""',
                'timezone = "Europe/Moscow"',
                "",
                "[state]",
                f'dir = "{root / "state"}"',
                f"raw_retention_days = {retention_days}",
                "",
                "[git]",
                f'roots = ["{root / "repos"}"]',
                "max_depth = 2",
                f'authors = ["{_AUTHOR}"]',
                "",
                "[transcripts]",
                f'roots = ["{root / "transcripts"}"]',
                "",
                "[opencode]",
                f"enabled = {'true' if opencode_db is not None else 'false'}",
                *([f'db = "{opencode_db}"'] if opencode_db is not None else []),
                "",
                "[telegram]",
                f"enabled = {'true' if telegram else 'false'}",
                "",
            ],
        ),
        encoding="utf-8",
    )
    return path


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    merged = os.environ.copy()
    if env is not None:
        merged.update(env)
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env=merged,
    )


def _commit(repo: Path, message: str, when: datetime) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(  # noqa: S603
            ["git", "init", "-b", "main", str(repo)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        _git(repo, "config", "user.email", _AUTHOR)
        _git(repo, "config", "user.name", "Vladimir Voronov")
    note = repo / "note.txt"
    note.write_text(f"{message}\n", encoding="utf-8")
    _git(repo, "add", "note.txt")
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        message,
        env={
            "GIT_AUTHOR_DATE": when.isoformat(),
            "GIT_COMMITTER_DATE": when.isoformat(),
            "GIT_AUTHOR_EMAIL": _AUTHOR,
            "GIT_AUTHOR_NAME": "Vladimir Voronov",
        },
    )


def _session(root: Path, when: datetime, *, text: str) -> None:
    session = "11111111-1111-1111-1111-111111111111"
    path = root / "proj" / "agent-transcripts" / session / f"{session}.jsonl"
    path.parent.mkdir(parents=True)
    line = {
        "role": "user",
        "message": {"content": [{"type": "text", "text": text}]},
    }
    path.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
