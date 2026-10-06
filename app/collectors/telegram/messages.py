"""Поля сообщения Telethon без сети и без файла сессии.

Порт `messages.py` из Lancetnik/slop-writer, Apache-2.0:
https://github.com/Lancetnik/slop-writer/blob/main/src/slop_writer/messages.py
Реакции считаются здесь и в дамп не попадают: сборщик их не записывает.
"""

from datetime import datetime

from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    ReactionPaid,
)

from app.summary.models import TelegramMessage


def dump_messages(messages: list[object]) -> list[TelegramMessage]:
    """Собрать сообщения дня: альбом — одна запись, служебные события пропущены.

    Порядок — от раннего к позднему. Пересланное получает пометку об авторстве.
    Подпись к медиа остаётся текстом, описание вложения — следующей строкой.

    Args:
        messages: Сообщения Telethon в любом порядке.

    Returns:
        Записи дампа без пустых и служебных сообщений.
    """
    visible = [message for message in messages if not _is_service(message)]
    rendered: list[TelegramMessage] = []
    for group in _chronological(group_albums(visible)):
        item = _render_group(group)
        if item is not None:
            rendered.append(item)
    return rendered


def media_type(msg: object) -> str | None:
    """Вид вложения: `photo`, `document` или имя типа Telethon.

    Args:
        msg: Сообщение.

    Returns:
        Короткая метка либо `None`, если вложения нет.
    """
    media = getattr(msg, "media", None)
    if not media:
        return None
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        return "document"
    return type(media).__name__


def media_desc(msg: object) -> str | None:
    """Одна строка про вложение. Файл не скачивается.

    Args:
        msg: Сообщение.

    Returns:
        Описание либо `None`, если вложения нет.
    """
    kind = media_type(msg)
    if kind is None:
        return None
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaDocument):
        return _document_desc(media, fallback=kind)
    return kind


def sender_fields(msg: object) -> tuple[int | None, str | None, str | None]:
    """Идентификатор, имя и username отправителя.

    Args:
        msg: Сообщение.

    Returns:
        Тройка `(user_id, имя, username)`.
    """
    sender = getattr(msg, "sender", None)
    if sender is None:
        return _peer_id(getattr(msg, "from_id", None)), None, None
    return _sender_id(sender), _sender_name(sender), _username(sender)


def count_reactions(msg: object) -> tuple[int, int]:
    """Число обычных реакций и звёзд. Сборщик дня это число не пишет в дамп.

    Args:
        msg: Сообщение.

    Returns:
        Пара `(реакции, звёзды)`.
    """
    results = getattr(getattr(msg, "reactions", None), "results", None)
    if not results:
        return 0, 0
    reactions = stars = 0
    for item in results:
        count = getattr(item, "count", 0)
        if not isinstance(count, int):
            continue
        if isinstance(getattr(item, "reaction", None), ReactionPaid):
            stars += count
        else:
            reactions += count
    return reactions, stars


def group_albums(messages: list[object]) -> list[list[object]]:
    """Сгруппировать альбом по `grouped_id`. Одиночные сообщения — группы из одного.

    Args:
        messages: Сообщения в исходном порядке.

    Returns:
        Сначала одиночные, затем альбомы.
    """
    groups: dict[int, list[object]] = {}
    standalone: list[list[object]] = []
    for message in messages:
        grouped_id = getattr(message, "grouped_id", None)
        if isinstance(grouped_id, int):
            groups.setdefault(grouped_id, []).append(message)
        else:
            standalone.append([message])
    return standalone + list(groups.values())


def _chronological(groups: list[list[object]]) -> list[list[object]]:
    return sorted(groups, key=lambda group: min(_sort_key(message) for message in group))


def _render_group(group: list[object]) -> TelegramMessage | None:
    ordered = sorted(group, key=_sort_key)
    body = _body(ordered)
    if body == "":
        return None
    return TelegramMessage(
        sent_at=_sent_at(ordered[0]),
        author=_author(ordered[0]),
        text=body,
    )


def _body(messages: list[object]) -> str:
    caption = next((_plain(message) for message in messages if _plain(message)), "")
    medias = [desc for desc in (media_desc(message) for message in messages) if desc]
    text = caption
    if medias:
        line = ", ".join(medias)
        text = f"{text}\n{line}" if text else line
    forwarded = next((_forward_name(message) for message in messages if _forward_name(message)), "")
    if forwarded:
        return f"переслано от {forwarded}: {text}" if text else f"переслано от {forwarded}"
    return text


def _is_service(msg: object) -> bool:
    return getattr(msg, "action", None) is not None


def _plain(msg: object) -> str:
    text = getattr(msg, "message", None)
    if not isinstance(text, str):
        return ""
    return text.strip()


def _forward_name(msg: object) -> str:
    forwarded = getattr(msg, "fwd_from", None)
    if forwarded is None:
        return ""
    for attr in ("from_name", "post_author"):
        value = getattr(forwarded, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "неизвестного автора"


def _author(msg: object) -> str | None:
    _user_id, name, _username = sender_fields(msg)
    return name


def _sort_key(msg: object) -> tuple[datetime, int]:
    message_id = getattr(msg, "id", 0)
    if not isinstance(message_id, int):
        message_id = 0
    return _sent_at(msg), message_id


def _sent_at(msg: object) -> datetime:
    sent_at = getattr(msg, "date", None)
    if not isinstance(sent_at, datetime):
        message = "у сообщения нет даты"
        raise TypeError(message)
    return sent_at


def _document_desc(media: MessageMediaDocument, *, fallback: str) -> str:
    document = media.document
    name = _file_name(document)
    parts = [name or fallback]
    size = getattr(document, "size", None)
    if isinstance(size, int) and size:
        parts.append(f"({size:,} bytes)")
    return " ".join(parts)


def _file_name(document: object) -> str | None:
    attributes = getattr(document, "attributes", None)
    if not attributes:
        return None
    for attr in attributes:
        file_name = getattr(attr, "file_name", None)
        if isinstance(file_name, str) and file_name:
            return file_name
    return None


def _peer_id(peer: object) -> int | None:
    user_id = getattr(peer, "user_id", None)
    if isinstance(user_id, int):
        return user_id
    return None


def _sender_id(sender: object) -> int | None:
    sender_id = getattr(sender, "id", None)
    if isinstance(sender_id, int):
        return sender_id
    return None


def _sender_name(sender: object) -> str | None:
    first = getattr(sender, "first_name", "") or ""
    last = getattr(sender, "last_name", "") or ""
    if not isinstance(first, str):
        first = ""
    if not isinstance(last, str):
        last = ""
    name = f"{first} {last}".strip()
    if name:
        return name
    title = getattr(sender, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _username(sender: object) -> str | None:
    username = getattr(sender, "username", None)
    if isinstance(username, str) and username:
        return username
    return None
