"""Сессия Telethon и перевод ошибок подключения.

Проверка, отвергающая личный диалог (`User`), не перенесена: личка входит в саммари.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from app.config import Config
from app.errors import ErrorCode, SummaryError

_SETUP = "day-recap init"
FLOOD_HINT = "Повторите сбор позже. Короткое ожидание инструмент пережидает сам."


def session_path(state_dir: Path) -> Path:
    """Файл сессии одного аккаунта на пользователя.

    Args:
        state_dir: Каталог `state.dir`.

    Returns:
        Путь `session.session` внутри каталога состояния.
    """
    return state_dir / "session.session"


def require_credentials(config: Config) -> tuple[int, str, str]:
    """Вернуть api id, hash и телефон либо отказ с `NO_CREDENTIALS`.

    Args:
        config: Настройки, куда `.env` уже подмешан.

    Returns:
        Три значения для Telethon.

    Raises:
        SummaryError: Не хватает одного из `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`.
    """
    missing = [
        name
        for name, present in (
            ("TG_API_ID", config.tg_api_id is not None),
            ("TG_API_HASH", bool(config.tg_api_hash)),
            ("TG_PHONE", bool(config.tg_phone)),
        )
        if not present
    ]
    if missing:
        listed = ", ".join(missing)
        message = f"Не заданы учётные данные Telegram: {listed}."
        hint = f"Выполните {_SETUP} в своём терминале: api_id и api_hash вводятся там, не в чате."
        raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS)
    api_id = config.tg_api_id
    api_hash = config.tg_api_hash
    phone = config.tg_phone
    if api_id is None or api_hash is None or phone is None:
        message = "Не заданы учётные данные Telegram."
        hint = f"Выполните {_SETUP} в своём терминале."
        raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS)
    return api_id, api_hash, phone


def make_client(session_file: Path, api_id: int, api_hash: str) -> TelegramClient:
    """Собрать клиент, не подключаясь.

    Args:
        session_file: Файл сессии.
        api_id: Идентификатор приложения.
        api_hash: Секрет приложения.

    Returns:
        Клиент Telethon.
    """
    return TelegramClient(str(session_file), api_id, api_hash)


def require_session(session_file: Path) -> None:
    """Отказать, если файла сессии нет. Вход из агента завис бы на QR.

    Args:
        session_file: Ожидаемый файл сессии.

    Raises:
        SummaryError: Код `NO_SESSION`.
    """
    if session_file.is_file():
        return
    message = f"Сессия Telegram не найдена: {session_file}"
    hint = (
        f"Выполните {_SETUP} в своём терминале и выберите qr или code. В чате агента вход зависнет."
    )
    raise SummaryError(message, hint, ErrorCode.NO_SESSION)


def revoked_session(session_file: Path) -> SummaryError:
    """Отказ, когда файл на месте, а сервер сессию уже не принимает.

    Args:
        session_file: Файл, который нельзя считать доказательством входа.

    Returns:
        Ошибка `NO_SESSION`.
    """
    message = f"Сессия Telegram недействительна: {session_file}"
    hint = f"Выполните {_SETUP} в своём терминале: отозванная сессия оставляет файл на месте."
    return SummaryError(message, hint, ErrorCode.NO_SESSION)


def resolve_failure(chat_id: int, exc: Exception) -> SummaryError:
    """Перевести ошибку открытия чата. `User` здесь не ошибка.

    Args:
        chat_id: Id из конфига.
        exc: `ChannelPrivateError`, `ValueError` или ошибка username.

    Returns:
        `NOT_A_MEMBER` либо `CANNOT_RESOLVE`.

    Raises:
        Exception: Ошибка, которую эта функция не переводит.
    """
    if isinstance(exc, ChannelPrivateError):
        message = f"Нет доступа к чату {chat_id}."
        hint = "Вступите в чат или проверьте, что этот аккаунт в нём состоит."
        return SummaryError(message, hint, ErrorCode.NOT_A_MEMBER)
    if not isinstance(exc, (ValueError, UsernameInvalidError, UsernameNotOccupiedError)):
        raise exc
    message = f"Не удалось открыть чат {chat_id}."
    hint = "Проверьте id. Приватный чат открывается, только если этот аккаунт его уже видел."
    return SummaryError(message, hint, ErrorCode.CANNOT_RESOLVE)


def prepare_connection(config: Config) -> tuple[int, str, Path]:
    """Проверить секреты и файл сессии до любого сетевого вызова.

    Args:
        config: Настройки с секретами и каталогом состояния.

    Returns:
        api id, api hash и путь сессии с правами 600.

    Raises:
        SummaryError: Нет секретов или файла сессии.
    """
    api_id, api_hash, _phone = require_credentials(config)
    path = session_path(config.state.dir)
    require_session(path)
    path.chmod(0o600)
    return api_id, api_hash, path


def flood_wait(seconds: int, hint: str) -> SummaryError:
    """Отказ, когда Telegram просит ждать дольше порога.

    Args:
        seconds: Поле `FloodWaitError.seconds`.
        hint: Что сделать человеку.

    Returns:
        Ошибка `FLOOD_WAIT`.
    """
    message = f"Telegram просит подождать {seconds} с."
    return SummaryError(message, hint, ErrorCode.FLOOD_WAIT)


async def retry_flood(
    make: Callable[[], Awaitable[object]],
    *,
    retry_seconds: int,
    sleep: Callable[[float], Awaitable[None]],
    hint: str,
) -> object:
    """Подождать короткий FloodWait и повторить. Длинный — `FLOOD_WAIT`.

    Args:
        make: Один сетевой вызов.
        retry_seconds: Порог из конфига. Равен ему или больше — не ждём.
        sleep: Чем ждать. В тестах подменяется.
        hint: Подсказка, если порог превышен.

    Returns:
        Результат `make`.
    """
    while True:
        try:
            return await make()
        except FloodWaitError as exc:
            if exc.seconds >= retry_seconds:
                raise flood_wait(exc.seconds, hint) from None
            await sleep(exc.seconds)


@asynccontextmanager
async def connected(config: Config) -> AsyncIterator[TelegramClient]:
    """Подключиться и отключиться. Клиент не выходит за пределы этого блока.

    Args:
        config: Настройки с секретами и каталогом состояния.

    Yields:
        Авторизованный клиент.
    """
    api_id, api_hash, path = prepare_connection(config)
    client = make_client(path, api_id, api_hash)
    await _retry(client.connect, retry_seconds=config.telegram.flood_wait_retry_seconds)
    try:
        authorized = await _retry(
            client.is_user_authorized,
            retry_seconds=config.telegram.flood_wait_retry_seconds,
        )
        if not authorized:
            raise revoked_session(path)
        yield client
    finally:
        await client.disconnect()


async def _retry(make: Callable[[], Awaitable[object]], *, retry_seconds: int) -> object:
    return await retry_flood(
        make,
        retry_seconds=retry_seconds,
        sleep=asyncio.sleep,
        hint=FLOOD_HINT,
    )
