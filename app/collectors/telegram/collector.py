"""Чтение белого списка чатов за окно дня."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict
from telethon.errors import (
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from app.collectors.telegram.messages import dump_messages
from app.collectors.telegram.tg import (
    FLOOD_HINT,
    connected,
    resolve_failure,
    retry_flood,
)
from app.config import Config, TelegramChat
from app.summary.models import TelegramChatLog, Window

# Пауза между чатами, чтобы не выгребать историю пачкой запросов.
_PAUSE_BETWEEN_CHATS = 1.0
_UNREADABLE = (ChannelPrivateError, ValueError, UsernameInvalidError, UsernameNotOccupiedError)


@dataclass(frozen=True)
class _Pace:
    retry_seconds: int
    sleep: Callable[[float], Awaitable[None]]
    page_size: int
    message_limit: int
    window: Window


class CollectedTelegram(BaseModel):
    """Результат сборщика. Запись дампа и маскирование — не его работа."""

    model_config = ConfigDict(extra="forbid")

    chats: list[TelegramChatLog]
    truncations: list[str]
    unlisted_active: int


class TelegramReader(Protocol):
    """Чтение Telethon, которое сборщик подменяет в тестах."""

    async def get_entity(self, chat_id: int) -> object:
        """Открыть чат по id, в том числе личный диалог."""
        ...

    async def get_messages(
        self,
        entity: object,
        *,
        limit: int,
        offset_id: int = 0,
        offset_date: datetime | None = None,
    ) -> Sequence[object]:
        """Страница сообщений: старше `offset_id` и раньше `offset_date`."""
        ...

    def iter_dialogs(self) -> AsyncIterator[object]:
        """Диалоги аккаунта. Счётчик чатов вне конфига смотрит только дату."""
        ...


def collect_telegram(
    config: Config,
    window: Window,
    *,
    client: TelegramReader | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> CollectedTelegram:
    """Прочитать чаты из конфига. Тип сущности не фильтруется: личка нужна так же, как группа.

    Без `client` открывается сессия из `state.dir`. Её отсутствие — `NO_SESSION`,
    пустые секреты — `NO_CREDENTIALS`. Топики форума не отделяются: чат читается целиком.

    Args:
        config: Настройки. Белый список, лимиты и секреты берутся отсюда.
        window: Закрытый интервал сбора. Сообщения вне него не входят.
        client: Уже открытый клиент. `None` — подключиться самим.
        sleep: Пауза между чатами и короткое `FloodWait`. `None` — `asyncio.sleep`.

    Returns:
        Чаты с сообщениями внутри окна, пометки об усечении и число чатов вне конфига.
    """
    pause = asyncio.sleep if sleep is None else sleep
    if client is None:
        return asyncio.run(_collect_live(config, window, sleep=pause))
    return asyncio.run(_collect(config, window, client=client, sleep=pause))


async def _collect_live(
    config: Config,
    window: Window,
    *,
    sleep: Callable[[float], Awaitable[None]],
) -> CollectedTelegram:
    async with connected(config) as client:
        return await _collect(config, window, client=client, sleep=sleep)


async def _collect(
    config: Config,
    window: Window,
    *,
    client: TelegramReader,
    sleep: Callable[[float], Awaitable[None]],
) -> CollectedTelegram:
    chats: list[TelegramChatLog] = []
    notes: list[str] = []
    pace = _Pace(
        retry_seconds=config.telegram.flood_wait_retry_seconds,
        sleep=sleep,
        page_size=config.telegram.page_size,
        message_limit=config.telegram.max_messages_per_chat,
        window=window,
    )
    for index, chat in enumerate(config.telegram.chats):
        if index:
            await sleep(_PAUSE_BETWEEN_CHATS)
        log, note = await _chat(client, chat, pace=pace)
        if note is not None:
            notes.append(note)
        if log.messages:
            chats.append(log)
    unlisted = await _unlisted(
        client,
        known={chat.id for chat in config.telegram.chats},
        pace=pace,
    )
    return CollectedTelegram(chats=chats, truncations=notes, unlisted_active=unlisted)


async def _chat(
    client: TelegramReader,
    chat: TelegramChat,
    *,
    pace: _Pace,
) -> tuple[TelegramChatLog, str | None]:
    try:
        entity = await retry_flood(
            lambda: client.get_entity(chat.id),
            retry_seconds=pace.retry_seconds,
            sleep=pace.sleep,
            hint=FLOOD_HINT,
        )
    except _UNREADABLE as exc:
        raise resolve_failure(chat.id, exc) from None
    raw, truncated = await _pages(client, entity, pace=pace)
    log = TelegramChatLog(id=chat.id, name=chat.name, messages=dump_messages(raw))
    if truncated:
        return log, f"{chat.name}: оставлены последние {pace.message_limit} сообщений"
    return log, None


async def _pages(
    client: TelegramReader,
    entity: object,
    *,
    pace: _Pace,
) -> tuple[list[object], bool]:
    raw: list[object] = []
    offset_id = 0
    offset_date: datetime | None = pace.window.to
    while True:
        page = await _page(client, entity, offset_id=offset_id, offset_date=offset_date, pace=pace)
        if not page:
            return raw, False
        offset_id = _message_id(page[-1])
        offset_date = None
        filled = _consume(page, raw, limit=pace.message_limit, window=pace.window)
        if filled is not None:
            return raw, filled


async def _page(
    client: TelegramReader,
    entity: object,
    *,
    offset_id: int,
    offset_date: datetime | None,
    pace: _Pace,
) -> list[object]:
    loaded = await retry_flood(
        lambda: client.get_messages(
            entity,
            limit=pace.page_size,
            offset_id=offset_id,
            offset_date=offset_date,
        ),
        retry_seconds=pace.retry_seconds,
        sleep=pace.sleep,
        hint=FLOOD_HINT,
    )
    return list(cast("Sequence[object]", loaded))


def _consume(
    page: Sequence[object],
    raw: list[object],
    *,
    limit: int,
    window: Window,
) -> bool | None:
    for message in page:
        if _older(message, window):
            return False
        if not _in_window(message, window) or getattr(message, "action", None) is not None:
            continue
        if len(raw) == limit:
            return True
        raw.append(message)
    return None


def _message_id(message: object) -> int:
    value = getattr(message, "id", 0)
    if isinstance(value, int):
        return value
    return 0


def _older(message: object, window: Window) -> bool:
    sent_at = getattr(message, "date", None)
    return isinstance(sent_at, datetime) and sent_at < window.from_


def _in_window(message: object, window: Window) -> bool:
    sent_at = getattr(message, "date", None)
    if not isinstance(sent_at, datetime):
        return False
    return window.from_ <= sent_at <= window.to


async def _unlisted(
    client: TelegramReader,
    *,
    known: set[int],
    pace: _Pace,
) -> int:
    count = 0
    async for dialog in _dialogs(client, pace):
        dialog_id = getattr(dialog, "id", None)
        if not isinstance(dialog_id, int) or dialog_id in known:
            continue
        if _in_window(dialog, pace.window):
            count += 1
    return count


async def _dialogs(client: TelegramReader, pace: _Pace) -> AsyncIterator[object]:
    iterator = client.iter_dialogs().__aiter__()
    while True:
        try:
            dialog = await retry_flood(
                iterator.__anext__,
                retry_seconds=pace.retry_seconds,
                sleep=pace.sleep,
                hint=FLOOD_HINT,
            )
        except StopAsyncIteration:
            return
        yield dialog
