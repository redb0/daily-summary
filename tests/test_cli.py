"""CLI как публичная граница: что видит человек и агент, запустив команду."""

import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.cli import main

_MOSCOW = ZoneInfo("Europe/Moscow")
_AUTHOR = "vvoronov@mwnts.ru"
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

    dump_path = tmp_path / "state" / "raw" / "2026-10-05.json"
    payload = json.loads(dump_path.read_text(encoding="utf-8"))
    size = dump_path.stat().st_size
    commit = payload["sources"]["git"]["repos"][0]["commits"][0]
    session = payload["sources"]["transcripts"]["sessions"][0]
    assert (
        exit_code,
        capsys.readouterr().out,
        dump_path.stat().st_mode & 0o777,
        (tmp_path / "state").stat().st_mode & 0o777,
        (tmp_path / "state" / "raw").stat().st_mode & 0o777,
        payload["date"],
        payload["window"],
        payload["generated_at"],
        commit["message"],
        session["messages"],
        payload["sources"]["telegram"]["status"],
        payload["stats"]["commits"],
        payload["stats"]["sessions"],
        payload["stats"]["messages"],
        payload["stats"]["bytes"],
        (home / ".local" / "state").exists(),
    ) == (
        0,
        (
            f"{dump_path}\n"
            f"репозиториев: 1, коммитов: 1, сессий: 1, сообщений: 1, "
            f"байт: {payload['stats']['bytes']}\n"
        ),
        0o600,
        0o700,
        0o700,
        "2026-10-05",
        {
            "from": "2026-10-05T00:00:00+03:00",
            "to": "2026-10-05T23:59:59.999999+03:00",
        },
        "2026-10-05T18:00:00+03:00",
        "добавил заметку",
        [{"role": "user", "text": "сделал штуку"}],
        "disabled",
        1,
        1,
        1,
        size,
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
    default_payload = json.loads(
        (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8"),
    )
    capsys.readouterr()

    today_code = main(["collect", "--date", "today", "--config", str(config)])
    today_payload = json.loads(
        (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8"),
    )

    default_messages = [
        commit["message"] for commit in default_payload["sources"]["git"]["repos"][0]["commits"]
    ]
    today_messages = [
        commit["message"] for commit in today_payload["sources"]["git"]["repos"][0]["commits"]
    ]
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
        str(tmp_path / "state" / "raw" / "2026-10-05.json"),
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

    payload = json.loads(
        (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8"),
    )
    messages = [commit["message"] for commit in payload["sources"]["git"]["repos"][0]["commits"]]
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
    config = _config_file(tmp_path, retention_days=14)

    exit_code = main(["collect", "--date", "2026-09-01", "--config", str(config)])

    names = sorted(path.name for path in raw.iterdir())
    assert (exit_code, names, capsys.readouterr().out.splitlines()[0]) == (
        0,
        ["2026-09-22.json", "notes.json"],
        "дамп не сохранён: дата старше ретенции",
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

    payload = json.loads(
        (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8"),
    )
    telegram = payload["sources"]["telegram"]
    reason = f"Сессия Telegram не найдена: {tmp_path / 'state' / 'session.session'}"
    assert (
        exit_code,
        payload["sources"]["git"]["status"],
        payload["stats"]["commits"],
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
        f"недоступно: telegram — NO_SESSION: {reason}",
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

    raw = (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    message = payload["sources"]["git"]["repos"][0]["commits"][0]["message"]
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

    payload = json.loads(
        (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8"),
    )
    opencode = payload["sources"]["opencode"]
    reason = f"База OpenCode не найдена: {missing}"
    assert (
        exit_code,
        payload["sources"]["git"]["status"],
        payload["sources"]["transcripts"]["status"],
        opencode["status"],
        opencode["reason"],
        capsys.readouterr().out.splitlines()[-1],
    ) == (
        0,
        "ok",
        "empty",
        "unavailable",
        reason,
        f"недоступно: opencode — {reason}",
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
        payload = json.loads(
            (tmp_path / "state" / "raw" / "2026-10-05.json").read_text(encoding="utf-8"),
        )
    finally:
        transcript.chmod(0o644)

    dump_path = tmp_path / "state" / "raw" / "2026-10-05.json"
    reason = f"[Errno 13] Permission denied: '{transcript}'"
    report = capsys.readouterr().out
    assert (
        exit_code,
        payload["sources"]["git"]["repos"][0]["commits"][0]["message"],
        payload["sources"]["transcripts"],
        report,
    ) == (
        0,
        "коммит на месте",
        {"status": "unavailable", "reason": reason, "sessions": []},
        (
            f"{dump_path}\n"
            f"репозиториев: 1, коммитов: 1, сессий: 0, сообщений: 0, "
            f"байт: {payload['stats']['bytes']}\n"
            f"недоступно: transcripts — {reason}\n"
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


def test_invalid_day_token_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["collect", "--date", "завтра"])

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
