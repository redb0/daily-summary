"""Чтение настроек daily-summary."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource

from app.errors import ErrorCode, SummaryError


def _default_state_dir() -> Path:
    """Каталог сырых дампов вне vault.

    Returns:
        Путь по умолчанию.
    """
    return Path.home() / ".local" / "state" / "daily-summary"


def _default_git_roots() -> list[Path]:
    """Корни обхода репозиториев.

    Returns:
        Список корней.
    """
    return [Path.home() / "projects"]


def _default_authors() -> list[str]:
    """Адреса, чьи коммиты попадают в саммари.

    Returns:
        Список адресов.
    """
    return [
        "vvoronov@mwnts.ru",
        "vvoronov@sila.ru",
        "30475117+redb0@users.noreply.github.com",
    ]


def _default_exclude_globs() -> list[str]:
    """Маски файлов без полезного текста.

    Returns:
        Список glob-масок.
    """
    return [
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


def _expand_optional_path(value: object) -> Path | None:
    """Развернуть `~`. Пустая строка значит, что путь не задан.

    Args:
        value: Строка из toml, уже готовый путь или пустое значение.

    Returns:
        Путь с развёрнутым `~` либо `None`.

    Raises:
        TypeError: Значение не строка и не путь.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Path):
        return value.expanduser()
    if isinstance(value, str):
        return Path(value).expanduser()
    message = "путь должен быть строкой"
    raise TypeError(message)


def _expand_required_path(value: object) -> Path:
    """Развернуть обязательный путь.

    Args:
        value: Строка из toml или уже готовый путь.

    Returns:
        Путь с развёрнутым `~`.

    Raises:
        ValueError: Путь пустой.
    """
    path = _expand_optional_path(value)
    if path is None:
        message = "путь не задан"
        raise ValueError(message)
    return path


def _expand_path_list(value: object) -> object:
    """Развернуть каждый путь в списке.

    Args:
        value: Список путей из toml. Иначе значение возвращается как есть,
            чтобы pydantic сообщил о неверном типе.

    Returns:
        Список путей либо исходное значение.
    """
    if not isinstance(value, list):
        return value
    return [_expand_required_path(item) for item in value]


def _default_transcript_roots() -> list[Path]:
    """Корни транскриптов Cursor.

    Returns:
        Список корней.
    """
    return [Path.home() / ".cursor" / "projects"]


class NotesConfig(BaseModel):
    """Куда и как писать ежедневную заметку.

    Пустые `daily_dir` и `template` означают «не задано»: писать некуда,
    пока человек не заполнит конфиг.
    """

    model_config = ConfigDict(extra="forbid")

    daily_dir: Path | None = None
    template: Path | None = None
    filename_format: str = "%Y-%m-%d"
    timezone: str = "Europe/Moscow"

    @field_validator("daily_dir", "template", mode="before")
    @classmethod
    def expand_optional_path(cls, value: object) -> Path | None:
        """Развернуть `~` и считать пустую строку отсутствием пути.

        Args:
            value: Значение поля из toml.

        Returns:
            Путь или `None`.
        """
        return _expand_optional_path(value)


class StateConfig(BaseModel):
    """Сырые дампы. Лежат вне vault, потому что vault пушится в GitHub."""

    model_config = ConfigDict(extra="forbid")

    dir: Path = Field(default_factory=_default_state_dir)
    raw_retention_days: int = 14

    @field_validator("dir", mode="before")
    @classmethod
    def expand_dir(cls, value: object) -> Path:
        """Развернуть `~` в каталоге состояния.

        Args:
            value: Значение поля из toml.

        Returns:
            Каталог состояния.
        """
        return _expand_required_path(value)


class GitConfig(BaseModel):
    """Обход локальных репозиториев и лимиты diff."""

    model_config = ConfigDict(extra="forbid")

    roots: list[Path] = Field(default_factory=_default_git_roots)
    max_depth: int = 3
    authors: list[str] = Field(default_factory=_default_authors)
    include_dirty: bool = True
    exclude_globs: list[str] = Field(default_factory=_default_exclude_globs)
    max_diff_lines_per_file: int = 400
    max_diff_lines_per_commit: int = 2000
    max_diff_lines_per_day: int = 6000

    @field_validator("roots", mode="before")
    @classmethod
    def expand_roots(cls, value: object) -> object:
        """Развернуть `~` в корнях обхода.

        Args:
            value: Список путей из toml.

        Returns:
            Список путей.
        """
        return _expand_path_list(value)


class TranscriptsConfig(BaseModel):
    """Усечение длинных сессий Cursor."""

    model_config = ConfigDict(extra="forbid")

    roots: list[Path] = Field(default_factory=_default_transcript_roots)
    head_messages: int = 10
    tail_messages: int = 10

    @field_validator("roots", mode="before")
    @classmethod
    def expand_roots(cls, value: object) -> object:
        """Развернуть `~` в корнях транскриптов.

        Args:
            value: Список путей из toml.

        Returns:
            Список путей.
        """
        return _expand_path_list(value)


class TelegramChat(BaseModel):
    """Чат из белого списка."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str


class TelegramConfig(BaseModel):
    """Чтение чатов Telegram."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_messages_per_chat: int = 500
    page_size: int = 100
    flood_wait_retry_seconds: int = 60
    chats: list[TelegramChat] = Field(default_factory=list)


class SummaryConfig(BaseModel):
    """Порог, после которого саммари собирается в два прохода."""

    model_config = ConfigDict(extra="forbid")

    two_stage_threshold_bytes: int = 100_000


class Config(BaseSettings):
    """Настройки инструмента и секреты Telegram из `.env`."""

    model_config = SettingsConfigDict(extra="forbid", env_file_encoding="utf-8")

    notes: NotesConfig = Field(default_factory=NotesConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    transcripts: TranscriptsConfig = Field(default_factory=TranscriptsConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    tg_phone: str | None = None


def default_config_path() -> Path:
    """Путь к конфигу, если флаг `--config` не передан.

    Returns:
        `~/.config/daily-summary/config.toml`.
    """
    return Path.home() / ".config" / "daily-summary" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Прочитать конфиг и секреты из `.env` рядом с ним.

    Отсутствующий toml по умолчанию не ошибка: остаются значения по умолчанию.
    Пустой `notes.daily_dir` здесь не ошибка: сбор дампа не пишет в vault.
    Явный путь — это значение флага `--config`. Секреты при этом всё равно
    читаются из `~/.config/daily-summary/.env`, а не из каталога toml.

    Args:
        path: Путь к toml. `None` — `~/.config/daily-summary/config.toml`.

    Returns:
        Настройки из файла либо значения по умолчанию.

    Raises:
        FileNotFoundError: Явный путь не указывает на файл.
    """
    if path is None:
        config_path = default_config_path()
    else:
        config_path = path.expanduser()
        if not config_path.is_file():
            raise FileNotFoundError(config_path)
    loaded = TomlConfigSettingsSource(Config, toml_file=config_path)()
    env_file = default_config_path().parent / ".env"
    if not env_file.is_file():
        return Config(**loaded)

    # Файл `.env` известен только в момент вызова, поэтому класс настроек
    # собирается здесь: `env_file` в model_config иначе некуда записать.
    class ConfigWithEnv(Config):
        """Настройки с секретами из `.env` рядом с toml."""

        model_config = SettingsConfigDict(
            extra="forbid",
            env_file=env_file,
            env_file_encoding="utf-8",
        )

    return ConfigWithEnv(**loaded)


def require_notes_dir(config: Config) -> Path:
    """Вернуть каталог ежедневных заметок для записи.

    Сбор этот вызов не делает. Пустой каталог мешает только `write`.

    Args:
        config: Уже загруженные настройки.

    Returns:
        Каталог заметок.

    Raises:
        SummaryError: Код `NO_NOTES_DIR`, если каталог не задан.
    """
    daily_dir = config.notes.daily_dir
    if daily_dir is None:
        message = "Не задан каталог ежедневных заметок."
        hint = "Укажите notes.daily_dir в ~/.config/daily-summary/config.toml."
        raise SummaryError(message, hint, ErrorCode.NO_NOTES_DIR)
    return daily_dir
