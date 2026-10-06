"""Учётные данные и вход в Telegram. Спрашивает терминал, не этот модуль.

Порт `init.py` из Lancetnik/slop-writer, Apache-2.0:
https://github.com/Lancetnik/slop-writer/blob/main/src/slop_writer/init.py
Секреты лежат в `~/.config/daily-summary/.env`, сессия — в каталоге состояния.
`--relogin` удаляет файл сессии и не вызывает `log_out`: на отозванной сессии
серверный выход упал бы и оставил мёртвый файл.
"""

import asyncio
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
)

from app.collectors.telegram import tg
from app.collectors.telegram.tg import flood_wait, session_path
from app.config import Config
from app.errors import ErrorCode, SummaryError

ENV_KEYS = ("TG_API_ID", "TG_API_HASH", "TG_PHONE")
_CREDENTIALS_URL = "https://my.telegram.org/apps"
_SETUP = "daily-summary init"


def credentials_path() -> Path:
    """Файл секретов рядом с конфигом по умолчанию, не рядом с `--config`.

    Returns:
        `~/.config/daily-summary/.env`.
    """
    return Path.home() / ".config" / "daily-summary" / ".env"


def read_env(path: Path) -> dict[str, str]:
    """Прочитать `.env`, не трогая `os.environ`.

    Args:
        path: Файл секретов. Отсутствие файла — пустой набор.

    Returns:
        Ключи и значения без кавычек вокруг значения.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def missing_keys(values: dict[str, str]) -> tuple[str, ...]:
    """Ключи, которых нет, они пустые или `TG_API_ID` не из цифр.

    Args:
        values: Уже прочитанный `.env`.

    Returns:
        Недостающие ключи в порядке `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`.
    """
    bad: list[str] = []
    for key in ENV_KEYS:
        value = values.get(key, "").strip()
        if not value or (key == "TG_API_ID" and not value.isdigit()):
            bad.append(key)
    return tuple(bad)


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Записать три секрета с правами 600.

    Файл читает pydantic с `extra=forbid`, поэтому чужие ключи сюда не пишем.

    Args:
        path: Файл `.env`.
        updates: Новые или заменённые значения.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    merged = {**read_env(path), **updates}
    body = "".join(f"{key}={merged[key]}\n" for key in ENV_KEYS if key in merged)
    _write_private(path, body)


def drop_session(path: Path) -> bool:
    """Удалить файл сессии. `log_out` не вызывается.

    Args:
        path: `session.session`.

    Returns:
        Был ли файл.
    """
    if not path.is_file():
        return False
    path.unlink()
    return True


def describe_account(me: object) -> str:
    """Как показать `get_me`: имя, @username и телефон.

    Args:
        me: Пользователь Telethon.

    Returns:
        Строка вида `Анна / @anna / +79990000000`.
    """
    first = getattr(me, "first_name", None)
    last = getattr(me, "last_name", None)
    name = " ".join(part for part in (first, last) if isinstance(part, str) and part)
    username = getattr(me, "username", None)
    handle = f"@{username}" if isinstance(username, str) and username else ""
    phone = getattr(me, "phone", None)
    number = f"+{phone}" if isinstance(phone, str) and phone else ""
    parts = [part for part in (name, handle, number) if part]
    if not parts:
        return "неизвестный аккаунт"
    return " / ".join(parts)


def run_init(config: Config, *, relogin: bool, ask: Callable[[str], str]) -> str:
    """Дописать только недостающие секреты и проверить сессию живым входом.

    Args:
        config: Настройки. Каталог сессии — `state.dir`.
        relogin: Удалить файл сессии перед входом.
        ask: Вопрос в терминале. Ключ — имя переменной.

    Returns:
        Описание аккаунта из `get_me`.

    Raises:
        SummaryError: Секреты так и не собраны или Telegram отклонил вход.
    """
    path = credentials_path()
    values = read_env(path)
    for key in missing_keys(values):
        values[key] = ask(key).strip()
    _require_complete(values)
    write_env(path, values)
    session = session_path(config.state.dir)
    if relogin:
        drop_session(session)
    api_id = int(values["TG_API_ID"])
    api_hash = values["TG_API_HASH"]
    phone = values["TG_PHONE"]
    if session.is_file():
        session.chmod(0o600)
        found = asyncio.run(verify_session(session, api_id, api_hash))
        if found is not None:
            return found
    session.parent.mkdir(parents=True, exist_ok=True)
    session.parent.chmod(0o700)
    account = asyncio.run(run_login(session, api_id=api_id, api_hash=api_hash, phone=phone))
    if session.is_file():
        session.chmod(0o600)
    return account


async def verify_session(session: Path, api_id: int, api_hash: str) -> str | None:
    """Живой `get_me`. Файл без авторизации — это не вход.

    Args:
        session: Файл сессии.
        api_id: Идентификатор приложения.
        api_hash: Секрет приложения.

    Returns:
        Описание аккаунта либо `None`, если сервер сессию не принимает.
    """
    client = tg.make_client(session, api_id, api_hash)
    try:
        with _translated_auth_errors():
            await client.connect()
            if not await client.is_user_authorized():
                return None
            return describe_account(await client.get_me())
    finally:
        await client.disconnect()


async def run_login(session: Path, *, api_id: int, api_hash: str, phone: str) -> str:
    """Интерактивный вход. Код и пароль 2FA спрашивает Telethon со stdin.

    Args:
        session: Куда положить сессию.
        api_id: Идентификатор приложения.
        api_hash: Секрет приложения.
        phone: Телефон в международном формате.

    Returns:
        Описание аккаунта после `start`.
    """
    client = tg.make_client(session, api_id, api_hash)
    try:
        with _translated_auth_errors():
            await client.start(phone=phone)
            return describe_account(await client.get_me())
    finally:
        await client.disconnect()


def _require_complete(values: dict[str, str]) -> None:
    missing = missing_keys(values)
    if not missing:
        return
    listed = ", ".join(missing)
    message = f"Не заданы учётные данные Telegram: {listed}."
    hint = f"Создайте приложение на {_CREDENTIALS_URL} и снова выполните {_SETUP}."
    raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS)


@contextmanager
def _translated_auth_errors() -> Iterator[None]:
    try:
        yield
    except ApiIdInvalidError:
        message = "Telegram отклонил TG_API_ID / TG_API_HASH."
        hint = f"Они должны быть от одного приложения на {_CREDENTIALS_URL}."
        raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS) from None
    except PhoneNumberInvalidError:
        message = "Telegram отклонил TG_PHONE."
        hint = "Нужен международный формат, например +79990000000."
        raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS) from None
    except PhoneNumberBannedError:
        message = "Этот номер заблокирован в Telegram."
        hint = "Нужен другой аккаунт."
        raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS) from None
    except FloodWaitError as exc:
        hint = f"Подождите и снова выполните {_SETUP}. Секреты на диске уже сохранены."
        raise flood_wait(exc.seconds, hint) from None


def _write_private(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
