"""Загрузка конфига: публичная граница для сборщиков и записи заметок."""

from pathlib import Path

import pytest

from app.config import Config, NotesConfig, load_config, require_notes_dir
from app.errors import SummaryError

_SECRET_ENV = ("TG_API_ID", "TG_API_HASH", "TG_PHONE")


def _isolate_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Подменить домашний каталог и убрать секреты из окружения процесса.

    Args:
        monkeypatch: Фикстура pytest.
        home: Каталог, который код должен считать домашним.
    """
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)


def test_missing_file_uses_builtin_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)

    config = load_config()

    assert config.notes.daily_dir is None
    assert config.notes.template is None
    assert config.notes.filename_format == "%Y-%m-%d"
    assert config.notes.timezone == "Europe/Moscow"
    assert config.state.dir == home / ".local" / "state" / "daily-summary"
    assert config.state.raw_retention_days == 14
    assert config.git.roots == [home / "projects"]
    assert config.git.max_depth == 3
    assert config.git.authors == [
        "vvoronov@mwnts.ru",
        "vvoronov@sila.ru",
        "30475117+redb0@users.noreply.github.com",
    ]
    assert config.git.include_dirty is True
    assert config.git.exclude_globs == [
        "*.min.js",
        "*.lock",
        "*.sum",
        "*.pb.go",
        "*_pb2.py",
        "*.svg",
        "*.png",
        "vendor/*",
        "dist/*",
    ]
    assert config.git.max_diff_lines_per_file == 400
    assert config.git.max_diff_lines_per_commit == 2000
    assert config.git.max_diff_lines_per_day == 6000
    assert config.transcripts.roots == [home / ".cursor" / "projects"]
    assert config.transcripts.head_messages == 10
    assert config.transcripts.tail_messages == 10
    assert config.opencode.enabled is True
    assert config.opencode.db == home / ".local" / "share" / "opencode" / "opencode.db"
    assert config.opencode.head_messages == 10
    assert config.opencode.tail_messages == 10
    assert config.telegram.enabled is True
    assert config.telegram.max_messages_per_chat == 500
    assert config.telegram.page_size == 100
    assert config.telegram.flood_wait_retry_seconds == 60
    assert config.telegram.chats == []
    assert config.summary.two_stage_threshold_bytes == 100_000
    assert config.tg_api_id is None
    assert config.tg_api_hash is None
    assert config.tg_phone is None


def test_missing_daily_dir_fails_only_when_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)

    config = load_config()

    with pytest.raises(SummaryError) as exc_info:
        require_notes_dir(config)

    error = exc_info.value
    assert error.code == "NO_NOTES_DIR"
    assert error.message
    assert "notes.daily_dir" in error.hint


def test_toml_overrides_defaults_and_expands_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    config_dir = home / ".config" / "daily-summary"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        """
[notes]
daily_dir = "~/notes/daily"
template = "~/notes/template.md"
timezone = "Europe/Kaliningrad"

[state]
dir = "~/state"
raw_retention_days = 3

[git]
roots = ["~/work", "~/other"]
max_depth = 1
authors = ["dev@example.com"]
include_dirty = false
exclude_globs = ["*.lock"]
max_diff_lines_per_file = 10
max_diff_lines_per_commit = 20
max_diff_lines_per_day = 30

[transcripts]
roots = ["~/transcripts"]
head_messages = 2
tail_messages = 4

[opencode]
enabled = false
db = "~/opencode.db"
head_messages = 3
tail_messages = 5

[telegram]
enabled = false
max_messages_per_chat = 5
page_size = 6
flood_wait_retry_seconds = 7

[[telegram.chats]]
id = -1001234567890
name = "пример: НТР / FM core"

[summary]
two_stage_threshold_bytes = 50
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.notes.daily_dir == home / "notes" / "daily"
    assert config.notes.template == home / "notes" / "template.md"
    assert config.notes.timezone == "Europe/Kaliningrad"
    assert require_notes_dir(config) == home / "notes" / "daily"
    assert config.state.dir == home / "state"
    assert config.state.raw_retention_days == 3
    assert config.git.roots == [home / "work", home / "other"]
    assert config.git.max_depth == 1
    assert config.git.authors == ["dev@example.com"]
    assert config.git.include_dirty is False
    assert config.git.exclude_globs == ["*.lock"]
    assert config.git.max_diff_lines_per_file == 10
    assert config.git.max_diff_lines_per_commit == 20
    assert config.git.max_diff_lines_per_day == 30
    assert config.transcripts.roots == [home / "transcripts"]
    assert config.transcripts.head_messages == 2
    assert config.transcripts.tail_messages == 4
    assert config.opencode.enabled is False
    assert config.opencode.db == home / "opencode.db"
    assert config.opencode.head_messages == 3
    assert config.opencode.tail_messages == 5
    assert config.telegram.enabled is False
    assert config.telegram.max_messages_per_chat == 5
    assert config.telegram.page_size == 6
    assert config.telegram.flood_wait_retry_seconds == 7
    assert [(chat.id, chat.name) for chat in config.telegram.chats] == [
        (-1001234567890, "пример: НТР / FM core"),
    ]
    assert config.summary.two_stage_threshold_bytes == 50


def test_partial_toml_keeps_defaults_and_blank_dir_stays_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    config_dir = home / ".config" / "daily-summary"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        """
[notes]
daily_dir = ""
template = ""
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.notes.daily_dir is None
    assert config.notes.template is None
    assert config.notes.filename_format == "%Y-%m-%d"
    assert config.notes.timezone == "Europe/Moscow"
    assert config.git.authors == [
        "vvoronov@mwnts.ru",
        "vvoronov@sila.ru",
        "30475117+redb0@users.noreply.github.com",
    ]
    assert config.state.dir == home / ".local" / "state" / "daily-summary"
    with pytest.raises(SummaryError) as exc_info:
        require_notes_dir(config)
    assert exc_info.value.code == "NO_NOTES_DIR"


def test_secrets_are_read_from_dotenv_beside_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    config_dir = home / ".config" / "daily-summary"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "TG_API_ID=12345\nTG_API_HASH=hash-from-dotenv\nTG_PHONE=+79990001122\n",
        encoding="utf-8",
    )

    config = load_config()

    assert config.tg_api_id == 12345
    assert config.tg_api_hash == "hash-from-dotenv"
    assert config.tg_phone == "+79990001122"


def test_explicit_config_path_keeps_default_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    default_dir = home / ".config" / "daily-summary"
    default_dir.mkdir(parents=True)
    (default_dir / "config.toml").write_text(
        '[notes]\ntimezone = "Europe/Kaliningrad"\n',
        encoding="utf-8",
    )
    (default_dir / ".env").write_text(
        "TG_API_HASH=from-default-location\n",
        encoding="utf-8",
    )
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    (custom / "config.toml").write_text(
        '[notes]\ntimezone = "Asia/Yekaterinburg"\n',
        encoding="utf-8",
    )
    (custom / ".env").write_text(
        "TG_API_ID=7\nTG_API_HASH=from-explicit-path\nTG_PHONE=+70000000000\n",
        encoding="utf-8",
    )

    config = load_config(custom / "config.toml")

    assert config.notes.timezone == "Asia/Yekaterinburg"
    assert config.tg_api_id is None
    assert config.tg_api_hash == "from-default-location"
    assert config.tg_phone is None


def test_explicit_config_path_expands_tilde(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    (home / "custom.toml").write_text(
        '[notes]\ntimezone = "Asia/Yekaterinburg"\n',
        encoding="utf-8",
    )

    config = load_config(Path("~/custom.toml"))

    assert config.notes.timezone == "Asia/Yekaterinburg"


def test_example_config_loads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    example = Path(__file__).parent.parent / "config.example.toml"

    config = load_config(example)

    assert config.notes.daily_dir is None
    assert config.notes.template is None
    assert config.notes.filename_format == "%Y-%m-%d"
    assert config.notes.timezone == "Europe/Moscow"
    assert config.state.dir == home / ".local" / "state" / "daily-summary"
    assert config.state.raw_retention_days == 14
    assert config.git.roots == [home / "projects"]
    assert config.git.authors == [
        "vvoronov@mwnts.ru",
        "vvoronov@sila.ru",
        "30475117+redb0@users.noreply.github.com",
    ]
    assert config.git.max_diff_lines_per_file == 400
    assert config.git.max_diff_lines_per_commit == 2000
    assert config.git.max_diff_lines_per_day == 6000
    assert config.transcripts.roots == [home / ".cursor" / "projects"]
    assert config.opencode.enabled is True
    assert config.opencode.db == home / ".local" / "share" / "opencode" / "opencode.db"
    assert config.opencode.head_messages == 10
    assert config.opencode.tail_messages == 10
    assert config.telegram.enabled is True
    assert [(chat.id, chat.name) for chat in config.telegram.chats] == [
        (-1001234567890, "пример: НТР / FM core"),
    ]
    assert config.summary.two_stage_threshold_bytes == 100_000
    with pytest.raises(SummaryError) as exc_info:
        require_notes_dir(config)
    assert exc_info.value.code == "NO_NOTES_DIR"


def test_missing_explicit_config_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"

    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_require_notes_dir_returns_configured_path() -> None:
    daily = Path("/vault/daily")
    config = Config(notes=NotesConfig(daily_dir=daily))

    assert require_notes_dir(config) == daily
