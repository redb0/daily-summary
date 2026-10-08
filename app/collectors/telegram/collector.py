"""Чтение белого списка чатов за окно дня."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict
from telethon.errors import (
    ChannelPrivateError,
    RPCError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.messages import GetForumTopicsRequest

from app.collectors.telegram.messages import dump_messages, forward_peer_id, sender_fields
from app.collectors.telegram.tg import (
    FLOOD_HINT,
    connected,
    resolve_failure,
    retry_flood,
)
from app.config import Config, TelegramChat
from app.errors import ErrorCode, SummaryError
from app.summary.models import TelegramChatLog, Window

# Пауза между чатами, чтобы не выгребать историю пачкой запросов.
_PAUSE_BETWEEN_CHATS = 1.0
_TOPIC_PAGE = 100
_UNREADABLE = (ChannelPrivateError, ValueError, UsernameInvalidError, UsernameNotOccupiedError)


@dataclass(frozen=True)
class _Pace:
    retry_seconds: int
    sleep: Callable[[float], Awaitable[None]]
    page_size: int
    message_limit: int
    window: Window
    reply_to: int | None = None


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
        reply_to: int | None = None,
    ) -> Sequence[object]:
        """Страница сообщений: старше `offset_id` и раньше `offset_date`.

        `reply_to` — топик форума. Без него читается общая лента.
        """
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
    progress: Callable[[str], None] | None = None,
) -> CollectedTelegram:
    """Прочитать чаты из конфига. Тип сущности не фильтруется: личка нужна так же, как группа.

    Без `client` открывается сессия из `state.dir`. Её отсутствие — `NO_SESSION`,
    пустые секреты — `NO_CREDENTIALS`. Топики форума не отделяются: чат читается целиком.
    `progress` получает ход без имён чатов: `telegram: чат i/N`, затем `telegram: диалоги`
    и `telegram: диалоги K`. Строка `telegram: диалоги` уходит до первого запроса списка.

    Args:
        config: Настройки. Белый список, лимиты и секреты берутся отсюда.
        window: Закрытый интервал сбора. Сообщения вне него не входят.
        client: Уже открытый клиент. `None` — подключиться самим.
        sleep: Пауза между чатами и короткое `FloodWait`. `None` — `asyncio.sleep`.
        progress: Куда писать ход. `None` — молчать. Имена чатов сюда не попадают.

    Returns:
        Чаты с сообщениями внутри окна, пометки об усечении и число чатов вне конфига.
    """
    pause = asyncio.sleep if sleep is None else sleep
    if client is None:
        return asyncio.run(_collect_live(config, window, sleep=pause, progress=progress))
    return asyncio.run(_collect(config, window, client=client, sleep=pause, progress=progress))


async def _collect_live(
    config: Config,
    window: Window,
    *,
    sleep: Callable[[float], Awaitable[None]],
    progress: Callable[[str], None] | None,
) -> CollectedTelegram:
    async with connected(config) as client:
        return await _collect(config, window, client=client, sleep=sleep, progress=progress)


async def _collect(
    config: Config,
    window: Window,
    *,
    client: TelegramReader,
    sleep: Callable[[float], Awaitable[None]],
    progress: Callable[[str], None] | None,
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
    total = len(config.telegram.chats)
    for index, chat in enumerate(config.telegram.chats):
        _step(progress, f"telegram: чат {index + 1}/{total}")
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
        progress=progress,
    )
    return CollectedTelegram(chats=chats, truncations=notes, unlisted_active=unlisted)


def _step(progress: Callable[[str], None] | None, line: str) -> None:
    if progress is not None:
        progress(line)


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
        raw, truncated = await _read_chat(client, entity, pace)
    except _UNREADABLE as exc:
        return _empty(chat), resolve_failure(chat.id, exc).message
    except RPCError:
        return _empty(chat), f"{chat.name}: Telegram прервал чтение чата."
    names = await _forward_names(client, raw, pace)
    log = TelegramChatLog(
        id=chat.id,
        name=chat.name,
        messages=dump_messages(raw, forward_names=names),
    )
    if truncated:
        return log, f"{chat.name}: оставлены последние {pace.message_limit} сообщений"
    return log, None


def _empty(chat: TelegramChat) -> TelegramChatLog:
    return TelegramChatLog(id=chat.id, name=chat.name, messages=[])


async def _read_chat(
    client: TelegramReader,
    entity: object,
    pace: _Pace,
) -> tuple[list[object], bool]:
    topics = await _topic_ids(client, entity, pace)
    if topics is None:
        return await _pages(client, entity, pace=pace, limit=pace.message_limit)
    return await _forum(client, entity, topics, pace=pace)


async def _forum(
    client: TelegramReader,
    entity: object,
    topics: list[int],
    *,
    pace: _Pace,
) -> tuple[list[object], bool]:
    merged: dict[int, object] = {}
    for index, topic_id in enumerate(topics):
        if index:
            await pace.sleep(_PAUSE_BETWEEN_CHATS)
        raw, _ = await _pages(client, entity, pace=replace(pace, reply_to=topic_id), limit=None)
        for message in raw:
            merged.setdefault(_message_id(message), message)
    ordered = sorted(merged.values(), key=_newest_key, reverse=True)
    if len(ordered) <= pace.message_limit:
        return ordered, False
    return ordered[: pace.message_limit], True


async def _pages(
    client: TelegramReader,
    entity: object,
    *,
    pace: _Pace,
    limit: int | None,
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
        filled = _consume(page, raw, limit=limit, window=pace.window)
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
            reply_to=pace.reply_to,
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
    limit: int | None,
    window: Window,
) -> bool | None:
    for message in page:
        if _older(message, window):
            return False
        if not _in_window(message, window) or getattr(message, "action", None) is not None:
            continue
        if limit is not None and len(raw) == limit:
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
    progress: Callable[[str], None] | None,
) -> int:
    count = 0
    seen = 0
    _step(progress, "telegram: диалоги")
    async for dialog in _dialogs(client, pace):
        seen += 1
        _step(progress, f"telegram: диалоги {seen}")
        dialog_id = getattr(dialog, "id", None)
        if not isinstance(dialog_id, int) or dialog_id in known:
            continue
        if _in_window(dialog, pace.window):
            count += 1
            continue
        if _after_window(dialog, pace.window) and await _active_before_latest(
            client,
            dialog_id,
            pace,
        ):
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
        except RPCError as exc:
            raise _rpc_failure(exc) from None
        yield dialog


def _newest_key(message: object) -> tuple[datetime, int]:
    sent_at = getattr(message, "date", None)
    if not isinstance(sent_at, datetime):
        sent_at = datetime.fromtimestamp(0, tz=UTC)
    return sent_at, _message_id(message)


async def _topic_ids(client: TelegramReader, entity: object, pace: _Pace) -> list[int] | None:
    if not getattr(entity, "forum", False):
        return None
    listed = getattr(client, "forum_topics", None)
    if listed is not None:
        loaded = await retry_flood(
            lambda: listed(entity),
            retry_seconds=pace.retry_seconds,
            sleep=pace.sleep,
            hint=FLOOD_HINT,
        )
        found = cast("Sequence[object]", loaded)
        return [topic_id for topic_id in found if isinstance(topic_id, int)]
    return await _telethon_topics(client, entity, pace)


def _invoke(client: TelegramReader) -> Callable[[object], Awaitable[object]] | None:
    if not callable(client):
        return None
    return cast("Callable[[object], Awaitable[object]]", client)


async def _telethon_topics(client: TelegramReader, entity: object, pace: _Pace) -> list[int]:
    invoke = _invoke(client)
    if invoke is None:
        return []
    ids: list[int] = []
    cursor: tuple[int, int, datetime | None] = (0, 0, None)
    while True:
        page = await _topic_page(invoke, entity, pace, cursor=cursor)
        if not page or len(page) < _TOPIC_PAGE:
            _append_topic_ids(ids, page)
            return ids
        _append_topic_ids(ids, page)
        nxt = _next_topic_cursor(page[-1])
        if nxt is None:
            return ids
        cursor = nxt


async def _topic_page(
    invoke: Callable[[object], Awaitable[object]],
    entity: object,
    pace: _Pace,
    *,
    cursor: tuple[int, int, datetime | None],
) -> Sequence[object]:
    offset_topic, offset_id, offset_date = cursor
    request = GetForumTopicsRequest(
        peer=entity,
        offset_date=offset_date,
        offset_id=offset_id,
        offset_topic=offset_topic,
        limit=_TOPIC_PAGE,
    )

    async def _send() -> object:
        return await invoke(request)

    result = await retry_flood(
        _send,
        retry_seconds=pace.retry_seconds,
        sleep=pace.sleep,
        hint=FLOOD_HINT,
    )
    topics = getattr(result, "topics", None)
    if isinstance(topics, Sequence):
        return topics
    return []


def _append_topic_ids(ids: list[int], topics: Sequence[object]) -> None:
    for topic in topics:
        topic_id = getattr(topic, "id", None)
        if isinstance(topic_id, int):
            ids.append(topic_id)


def _next_topic_cursor(topic: object) -> tuple[int, int, datetime | None] | None:
    topic_id = getattr(topic, "id", None)
    if not isinstance(topic_id, int):
        return None
    top_message = getattr(topic, "top_message", None)
    moment = getattr(topic, "date", None)
    offset_id = top_message if isinstance(top_message, int) else 0
    offset_date = moment if isinstance(moment, datetime) else None
    return topic_id, offset_id, offset_date


def _after_window(dialog: object, window: Window) -> bool:
    sent_at = getattr(dialog, "date", None)
    return isinstance(sent_at, datetime) and sent_at > window.to


async def _active_before_latest(client: TelegramReader, dialog_id: int, pace: _Pace) -> bool:
    await pace.sleep(_PAUSE_BETWEEN_CHATS)
    try:
        entity = await retry_flood(
            lambda: client.get_entity(dialog_id),
            retry_seconds=pace.retry_seconds,
            sleep=pace.sleep,
            hint=FLOOD_HINT,
        )
        page = await _page(client, entity, offset_id=0, offset_date=pace.window.to, pace=pace)
    except (RPCError, SummaryError, ValueError, KeyError):
        return False
    return any(_in_window(message, pace.window) for message in page)


async def _forward_names(
    client: TelegramReader,
    messages: list[object],
    pace: _Pace,
) -> dict[int, str]:
    names: dict[int, str] = {}
    for message in messages:
        peer_id = forward_peer_id(message)
        if peer_id is None or peer_id in names:
            continue
        name = await _entity_name(client, peer_id, pace)
        if name is not None:
            names[peer_id] = name
    return names


async def _entity_name(client: TelegramReader, peer_id: int, pace: _Pace) -> str | None:
    try:
        entity = await retry_flood(
            lambda: client.get_entity(peer_id),
            retry_seconds=pace.retry_seconds,
            sleep=pace.sleep,
            hint=FLOOD_HINT,
        )
    except (RPCError, SummaryError, ValueError, KeyError):
        return None
    _peer, name, _username = sender_fields(SimpleNamespace(sender=entity))
    return name


def _rpc_failure(_exc: RPCError) -> SummaryError:
    message = "Telegram прервал чтение."
    hint = "Повторите сбор позже."
    return SummaryError(message, hint, ErrorCode.TELEGRAM)
