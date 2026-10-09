"""Сборщик Telegram через подменённый клиент: сеть не вызывается."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import MessageMediaPhoto, User

from app.collectors.telegram.collector import CollectedTelegram, collect_telegram
from app.config import (
    Config,
    GitConfig,
    NotesConfig,
    OpencodeConfig,
    StateConfig,
    TelegramChat,
    TelegramConfig,
    TranscriptsConfig,
)
from app.errors import ErrorCode, SummaryError
from app.summary.collect import collect_and_store
from app.summary.models import SourceStatus, TelegramChatLog, TelegramMessage, Window

_MOSCOW = ZoneInfo("Europe/Moscow")
_DAY = date(2026, 10, 5)
_AT = datetime(2026, 10, 5, 15, 0, tzinfo=_MOSCOW)


def test_private_dialog_is_collected_like_a_group(tmp_path: Path) -> None:
    client = _Client(
        entities={
            7: User(id=7, first_name="Коллега"),
            8: SimpleNamespace(id=8),
        },
        messages={
            7: [_message(1, "созвон в 15", "Коллега")],
            8: [_message(2, "созвон в 15", "Коллега")],
        },
    )

    collected = collect_telegram(
        _config(tmp_path, [(7, "личка"), (8, "группа")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected == CollectedTelegram(
        chats=[
            TelegramChatLog(
                id=7,
                name="личка",
                messages=[
                    TelegramMessage(sent_at=_AT, author="Коллега", text="созвон в 15"),
                ],
            ),
            TelegramChatLog(
                id=8,
                name="группа",
                messages=[
                    TelegramMessage(sent_at=_AT, author="Коллега", text="созвон в 15"),
                ],
            ),
        ],
        truncations=[],
        unlisted_active=0,
    )


def test_day_messages_keep_caption_forward_and_album(tmp_path: Path) -> None:
    album_at = datetime(2026, 10, 5, 10, 0, tzinfo=_MOSCOW)
    forwarded_at = datetime(2026, 10, 5, 11, 0, tzinfo=_MOSCOW)
    photo_at = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    service_at = datetime(2026, 10, 5, 13, 0, tzinfo=_MOSCOW)
    reply_at = datetime(2026, 10, 5, 14, 0, tzinfo=_MOSCOW)
    client = _Client(
        entities={3: User(id=3, first_name="Коллега")},
        messages={
            3: [
                _message(6, "ок", "Коллега", at=reply_at, reactions=SimpleNamespace(results=[])),
                _message(5, "Коллега присоединился", "Коллега", at=service_at, action=object()),
                _message(4, "снимок", "Коллега", at=photo_at, media=MessageMediaPhoto()),
                _message(
                    3,
                    "смотри",
                    "Коллега",
                    at=forwarded_at,
                    fwd_from=SimpleNamespace(from_name="Аня"),
                ),
                _message(2, "", "Коллега", at=album_at, media=MessageMediaPhoto(), grouped_id=9),
                _message(
                    1,
                    "альбом",
                    "Коллега",
                    at=album_at,
                    media=MessageMediaPhoto(),
                    grouped_id=9,
                ),
            ],
        },
    )

    collected = collect_telegram(
        _config(tmp_path, [(3, "личка")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected.chats == [
        TelegramChatLog(
            id=3,
            name="личка",
            messages=[
                TelegramMessage(sent_at=album_at, author="Коллега", text="альбом\nphoto, photo"),
                TelegramMessage(
                    sent_at=forwarded_at,
                    author="Коллега",
                    text="переслано от Аня: смотри",
                ),
                TelegramMessage(sent_at=photo_at, author="Коллега", text="снимок\nphoto"),
                TelegramMessage(sent_at=reply_at, author="Коллега", text="ок"),
            ],
        ),
    ]


def test_over_limit_keeps_the_latest_messages(tmp_path: Path) -> None:
    early = datetime(2026, 10, 5, 10, 0, tzinfo=_MOSCOW)
    middle = datetime(2026, 10, 5, 11, 0, tzinfo=_MOSCOW)
    late = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    client = _Client(
        entities={3: User(id=3)},
        messages={
            3: [
                _message(3, "поздно", "Коллега", at=late),
                _message(2, "середина", "Коллега", at=middle),
                _message(1, "рано", "Коллега", at=early),
            ],
        },
    )

    collected = collect_telegram(
        _config(tmp_path, [(3, "личка")], max_messages=2, page_size=1),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected == CollectedTelegram(
        chats=[
            TelegramChatLog(
                id=3,
                name="личка",
                messages=[
                    TelegramMessage(sent_at=middle, author="Коллега", text="середина"),
                    TelegramMessage(sent_at=late, author="Коллега", text="поздно"),
                ],
            ),
        ],
        truncations=["личка: оставлены последние 2 сообщений"],
        unlisted_active=0,
    )


def test_unlisted_chats_are_a_count_without_text(tmp_path: Path) -> None:
    inside = datetime(2026, 10, 5, 16, 0, tzinfo=_MOSCOW)
    outside = datetime(2026, 10, 4, 16, 0, tzinfo=_MOSCOW)
    later = datetime(2026, 10, 6, 9, 0, tzinfo=_MOSCOW)
    client = _Client(
        entities={
            7: User(id=7),
            10: SimpleNamespace(id=10),
            11: SimpleNamespace(id=11),
        },
        messages={
            7: [_message(1, "в конфиге", "Коллега")],
            10: [_message(2, "внутри окна", "Коллега", at=inside)],
            11: [_message(3, "уже следующий день", "Коллега", at=later)],
        },
        dialogs=[
            SimpleNamespace(id=7, date=inside),
            SimpleNamespace(id=8, date=inside),
            SimpleNamespace(id=9, date=outside),
            SimpleNamespace(id=10, date=datetime(2026, 10, 6, 12, 0, tzinfo=_MOSCOW)),
            SimpleNamespace(id=11, date=datetime(2026, 10, 6, 12, 0, tzinfo=_MOSCOW)),
        ],
    )

    collected = collect_telegram(
        _config(tmp_path, [(7, "личка")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected == CollectedTelegram(
        chats=[
            TelegramChatLog(
                id=7,
                name="личка",
                messages=[TelegramMessage(sent_at=_AT, author="Коллега", text="в конфиге")],
            ),
        ],
        truncations=[],
        unlisted_active=2,
    )


def test_progress_counts_chats_then_dialogs(tmp_path: Path) -> None:
    client = _Client(
        entities={7: User(id=7), 8: User(id=8)},
        messages={
            7: [_message(1, "а", "Коллега")],
            8: [_message(2, "б", "Коллега")],
        },
        dialogs=[
            SimpleNamespace(id=7, date=_AT),
            SimpleNamespace(id=9, date=_AT),
            SimpleNamespace(id=10, date=_AT),
        ],
    )
    lines: list[str] = []

    collect_telegram(
        _config(tmp_path, [(7, "личка"), (8, "группа")]),
        _window(),
        client=client,
        sleep=_no_sleep,
        progress=lines.append,
    )

    assert lines == [
        "telegram: чат 1/2",
        "telegram: чат 2/2",
        "telegram: диалоги",
        "telegram: диалоги 1",
        "telegram: диалоги 2",
        "telegram: диалоги 3",
    ]


def test_forum_topics_are_one_chat(tmp_path: Path) -> None:
    general_at = datetime(2026, 10, 5, 10, 0, tzinfo=_MOSCOW)
    topic_at = datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)
    client = _ForumClient(
        entities={3: SimpleNamespace(id=3, forum=True)},
        messages={3: []},
    )
    client.topics = {
        3: {
            1: [_message(1, "общее", "Коллега", at=general_at)],
            2: [_message(2, "топик", "Коллега", at=topic_at)],
        },
    }

    collected = collect_telegram(
        _config(tmp_path, [(3, "форум")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected.chats == [
        TelegramChatLog(
            id=3,
            name="форум",
            messages=[
                TelegramMessage(sent_at=general_at, author="Коллега", text="общее"),
                TelegramMessage(sent_at=topic_at, author="Коллега", text="топик"),
            ],
        ),
    ]


def test_forward_uses_the_resolved_author(tmp_path: Path) -> None:
    client = _Client(
        entities={
            3: User(id=3, first_name="Коллега"),
            4: User(id=4, first_name="Борис"),
        },
        messages={
            3: [
                _message(
                    1,
                    "смотри",
                    "Коллега",
                    fwd_from=SimpleNamespace(from_id=SimpleNamespace(user_id=4)),
                ),
            ],
        },
    )

    collected = collect_telegram(
        _config(tmp_path, [(3, "личка")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected.chats[0].messages == [
        TelegramMessage(sent_at=_AT, author="Коллега", text="переслано от Борис: смотри"),
    ]


def test_rpc_error_on_one_chat_keeps_the_other(tmp_path: Path) -> None:
    class _Broken(_Client):
        async def get_entity(self, chat_id: int) -> object:
            if chat_id == 7:
                raise RPCError(None, "fail", 500)
            return await super().get_entity(chat_id)

    client = _Broken(
        entities={8: User(id=8, first_name="Коллега")},
        messages={8: [_message(1, "осталось", "Коллега")]},
    )

    collected = collect_telegram(
        _config(tmp_path, [(7, "сломанный"), (8, "открытый")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert (collected.chats[0].id, collected.truncations) == (
        8,
        ["сломанный: Telegram прервал чтение чата."],
    )


def test_rpc_error_while_listing_dialogs_is_telegram(
    tmp_path: Path,
) -> None:
    class _Raising:
        def __aiter__(self) -> "_Raising":
            return self

        async def __anext__(self) -> SimpleNamespace:
            raise RPCError(None, "fail", 500)

    class _Dialogs(_Client):
        def iter_dialogs(self) -> AsyncIterator[SimpleNamespace]:
            return _Raising()

    client = _Dialogs(entities={7: User(id=7)}, messages={7: [_message(1, "есть", "Коллега")]})

    with pytest.raises(SummaryError) as exc_info:
        collect_telegram(
            _config(tmp_path, [(7, "личка")]),
            _window(),
            client=client,
            sleep=_no_sleep,
        )

    assert exc_info.value.code == ErrorCode.TELEGRAM


def test_short_flood_wait_is_slept_off_and_the_message_kept(tmp_path: Path) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    client = _Client(
        entities={7: User(id=7)},
        messages={7: [_message(1, "после паузы", "Коллега")]},
        floods=[59],
    )

    collected = collect_telegram(
        _config(tmp_path, [(7, "личка")]),
        _window(),
        client=client,
        sleep=_sleep,
    )

    assert (collected.chats[0].messages[0].text, slept) == ("после паузы", [59])


def test_long_flood_wait_stops_the_collector(tmp_path: Path) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    client = _Client(
        entities={7: User(id=7)},
        messages={7: [_message(1, "не дошли", "Коллега")]},
        floods=[60],
    )

    with pytest.raises(SummaryError) as exc_info:
        collect_telegram(
            _config(tmp_path, [(7, "личка")]),
            _window(),
            client=client,
            sleep=_sleep,
        )

    assert (exc_info.value.code, exc_info.value.message, slept) == (
        ErrorCode.FLOOD_WAIT,
        "Telegram просит подождать 60 с.",
        [],
    )


def test_missing_credentials_are_no_credentials(tmp_path: Path) -> None:
    config = Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        state=StateConfig(dir=tmp_path / "state"),
        telegram=TelegramConfig(chats=[TelegramChat(id=1, name="личка")]),
    )

    with pytest.raises(SummaryError) as exc_info:
        collect_telegram(config, _window())

    assert (exc_info.value.code, exc_info.value.message) == (
        ErrorCode.NO_CREDENTIALS,
        "Не заданы учётные данные Telegram: TG_API_ID, TG_API_HASH, TG_PHONE.",
    )


def test_unreadable_chat_is_skipped_and_the_other_chat_stays(tmp_path: Path) -> None:
    class _Missing(_Client):
        async def get_entity(self, chat_id: int) -> object:
            if chat_id == 7:
                message = str(chat_id)
                raise ValueError(message)
            return await super().get_entity(chat_id)

    client = _Missing(
        entities={8: User(id=8, first_name="Коллега")},
        messages={8: [_message(1, "осталось", "Коллега")]},
    )

    collected = collect_telegram(
        _config(tmp_path, [(7, "закрытый"), (8, "открытый")]),
        _window(),
        client=client,
        sleep=_no_sleep,
    )

    assert collected == CollectedTelegram(
        chats=[
            TelegramChatLog(
                id=8,
                name="открытый",
                messages=[TelegramMessage(sent_at=_AT, author="Коллега", text="осталось")],
            ),
        ],
        truncations=["Не удалось открыть чат 7."],
        unlisted_active=0,
    )


def test_flood_while_listing_dialogs_stops_the_collector(tmp_path: Path) -> None:
    client = _Client(
        entities={7: User(id=7)},
        messages={7: [_message(1, "не дошли", "Коллега")]},
        floods=[60],
        flood_at="dialogs",
    )

    with pytest.raises(SummaryError) as exc_info:
        collect_telegram(
            _config(tmp_path, [(7, "личка")]),
            _window(),
            client=client,
            sleep=_no_sleep,
        )

    assert (exc_info.value.code, exc_info.value.message) == (
        ErrorCode.FLOOD_WAIT,
        "Telegram просит подождать 60 с.",
    )


def test_flood_on_connect_is_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = tmp_path / "state" / "session.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"session")
    monkeypatch.setattr(
        "app.collectors.telegram.tg.make_client",
        lambda _session, _api_id, _api_hash: _ConnectFlood(),
    )

    with pytest.raises(SummaryError) as exc_info:
        collect_telegram(_config(tmp_path, []), _window())

    assert (exc_info.value.code, exc_info.value.message) == (
        ErrorCode.FLOOD_WAIT,
        "Telegram просит подождать 60 с.",
    )


def test_stored_dump_counts_telegram_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _Client(
        entities={7: User(id=7)},
        messages={7: [_message(1, "созвон в 15", "Коллега")]},
        dialogs=[SimpleNamespace(id=8, date=_AT)],
    )

    @asynccontextmanager
    async def _open(_config: Config) -> AsyncIterator[_Client]:
        yield client

    monkeypatch.setattr("app.collectors.telegram.collector.connected", _open)
    repos = tmp_path / "repos"
    transcripts = tmp_path / "transcripts"
    repos.mkdir()
    transcripts.mkdir()
    config = Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        state=StateConfig(dir=tmp_path / "state"),
        git=GitConfig(roots=[repos], authors=["nobody@example.com"]),
        transcripts=TranscriptsConfig(roots=[transcripts]),
        opencode=OpencodeConfig(enabled=False),
        telegram=TelegramConfig(chats=[TelegramChat(id=7, name="личка")]),
    )

    _path, dump = collect_and_store(config, "2026-10-05")

    assert (
        dump.sources.telegram.status,
        dump.sources.telegram.unlisted_active,
        [(message.text) for chat in dump.sources.telegram.chats for message in chat.messages],
        dump.stats.messages,
    ) == (SourceStatus.OK, 1, ["созвон в 15"], 1)


def _window() -> Window:
    start = datetime.combine(_DAY, time.min, tzinfo=_MOSCOW)
    end = datetime.combine(_DAY, time.max, tzinfo=_MOSCOW)
    return Window.model_validate({"from": start, "to": end})


def _config(
    root: Path,
    chats: list[tuple[int, str]],
    *,
    max_messages: int = 500,
    page_size: int = 100,
) -> Config:
    return Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        state=StateConfig(dir=root / "state"),
        telegram=TelegramConfig(
            chats=[TelegramChat(id=chat_id, name=name) for chat_id, name in chats],
            max_messages_per_chat=max_messages,
            page_size=page_size,
        ),
        tg_api_id=1,
        tg_api_hash="a" * 32,
        tg_phone="+79990000000",
    )


def _message(message_id: int, text: str, author: str, **fields: object) -> SimpleNamespace:
    sent_at = fields.get("at", _AT)
    if not isinstance(sent_at, datetime):
        sent_at = _AT
    return SimpleNamespace(
        id=message_id,
        date=sent_at,
        message=text,
        grouped_id=fields.get("grouped_id"),
        media=fields.get("media"),
        sender=SimpleNamespace(id=1, first_name=author, last_name="", username=None),
        from_id=None,
        fwd_from=fields.get("fwd_from"),
        action=fields.get("action"),
        reactions=fields.get("reactions"),
    )


async def _no_sleep(_seconds: float) -> None:
    return None


class _ConnectFlood:
    async def connect(self) -> None:
        raise FloodWaitError(None, capture=60)

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True


class _Client:
    def __init__(
        self,
        *,
        entities: dict[int, object],
        messages: dict[int, list[SimpleNamespace]],
        dialogs: list[SimpleNamespace] | None = None,
        floods: list[int] | None = None,
        flood_at: str = "messages",
    ) -> None:
        self.entities = entities
        self.messages = messages
        self.topics: dict[int, dict[int, list[SimpleNamespace]]] = {}
        self.dialogs = [] if dialogs is None else dialogs
        self.floods = [] if floods is None else floods
        self.flood_at = flood_at
        self._resolved: dict[int, int] = {}

    async def get_entity(self, chat_id: int) -> object:
        entity = self.entities[chat_id]
        self._resolved[id(entity)] = chat_id
        return entity

    async def get_messages(
        self,
        entity: object,
        *,
        limit: int,
        offset_id: int = 0,
        offset_date: datetime | None = None,
        reply_to: int | None = None,
    ) -> Sequence[object]:
        if self.flood_at == "messages":
            self._flood()
        chat_id = self._resolved[id(entity)]
        pool = self._pool(chat_id, reply_to)
        chosen: list[SimpleNamespace] = []
        for message in pool:
            if offset_id and message.id >= offset_id:
                continue
            if offset_date is not None and message.date >= offset_date:
                continue
            chosen.append(message)
            if len(chosen) == limit:
                break
        return chosen

    def _flood(self) -> None:
        if self.floods:
            seconds = self.floods.pop(0)
            raise FloodWaitError(None, capture=seconds)

    def _pool(self, chat_id: int, reply_to: int | None) -> list[SimpleNamespace]:
        if reply_to is None:
            return self.messages[chat_id]
        return self.topics.get(chat_id, {}).get(reply_to, [])

    async def iter_dialogs(self) -> AsyncIterator[SimpleNamespace]:
        if self.flood_at == "dialogs":
            self._flood()
        for dialog in self.dialogs:
            yield dialog


class _ForumClient(_Client):
    async def __call__(self, request: object) -> object:
        if not isinstance(request, GetForumTopicsRequest):
            message = type(request).__name__
            raise TypeError(message)
        return SimpleNamespace(
            topics=[
                SimpleNamespace(id=1, top_message=1, date=_AT),
                SimpleNamespace(id=2, top_message=2, date=_AT),
            ],
        )
