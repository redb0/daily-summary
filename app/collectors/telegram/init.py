"""Учётные данные и вход в Telegram. Спрашивает терминал, не этот модуль.

Секреты лежат в `~/.config/daily-summary/.env`, сессия — в каталоге состояния.
`--relogin` удаляет файл сессии и не вызывает `log_out`: на отозванной сессии
серверный выход упал бы и оставил мёртвый файл.
"""

import asyncio
import getpass
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol, cast

import qrcode
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from app.atomic import replace_text
from app.collectors.telegram import tg
from app.collectors.telegram.tg import flood_wait, session_path
from app.config import Config
from app.errors import ErrorCode, SummaryError

type LoginMethod = Literal["qr", "code"]

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


def run_init(
    config: Config,
    *,
    relogin: bool,
    login: str | None,
    ask: Callable[[str], str],
    choose: Callable[[], str],
) -> str:
    """Дописать только недостающие секреты и проверить сессию живым входом.

    Args:
        config: Настройки. Каталог сессии — `state.dir`.
        relogin: Удалить файл сессии перед входом.
        login: `qr` или `code`. `None` — спросить, когда входа ещё нет.
        ask: Вопрос в терминале. Ключ — имя переменной.
        choose: Ответ на `qr` или `code`, если флаг не передан.

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
    if session.is_file():
        session.chmod(0o600)
        found = asyncio.run(verify_session(session, api_id, api_hash))
        if found is not None:
            return found
    session.parent.mkdir(parents=True, exist_ok=True)
    session.parent.chmod(0o700)
    account = asyncio.run(
        run_login(
            session,
            api_id=api_id,
            api_hash=api_hash,
            phone=values["TG_PHONE"],
            method=_login_method(login, choose),
        ),
    )
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


async def run_login(
    session: Path,
    *,
    api_id: int,
    api_hash: str,
    phone: str,
    method: LoginMethod,
) -> str:
    """Войти выбранным способом: QR с телефона или код в уже открытый Telegram.

    Args:
        session: Куда положить сессию.
        api_id: Идентификатор приложения.
        api_hash: Секрет приложения.
        phone: Телефон для входа кодом.
        method: `qr` или `code`.

    Returns:
        Описание аккаунта после входа.
    """
    client = tg.make_client(session, api_id, api_hash)
    try:
        with _translated_auth_errors():
            if method == "qr":
                await client.connect()
                await _qr_sign_in(client)
            else:
                await _code_sign_in(client, phone)
            return describe_account(await client.get_me())
    finally:
        await client.disconnect()


def _login_method(login: str | None, choose: Callable[[], str]) -> LoginMethod:
    picked = login if login is not None else choose().strip().lower()
    methods: dict[str, LoginMethod] = {"qr": "qr", "code": "code"}
    found = methods.get(picked)
    if found is not None:
        return found
    message = f"Неизвестный способ входа: {picked}."
    hint = "Укажите qr или code."
    raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS)


class _QrCode(Protocol):
    url: str

    async def wait(self) -> object: ...


class _QrClient(Protocol):
    async def qr_login(self) -> _QrCode: ...

    async def sign_in(self, password: str) -> object: ...


class _CodeClient(Protocol):
    send_code_request: Callable[..., Awaitable[object]]

    async def start(
        self,
        phone: str,
        *,
        password: Callable[[], str],
        code_callback: Callable[[], str],
    ) -> object: ...


async def _code_sign_in(client: _CodeClient, phone: str) -> None:
    _watch_delivery(client)

    def _code() -> str:
        return input("Код из Telegram: ")

    def _password() -> str:
        return getpass.getpass("Пароль 2FA: ")

    await client.start(phone, password=_password, code_callback=_code)


def describe_sent_code(sent: object) -> str:
    """Куда Telegram положил код. Хеш кода в строку не входит.

    Args:
        sent: Ответ `auth.sendCode`.

    Returns:
        Одна строка для stderr.
    """
    kind = getattr(sent, "type", None)
    if kind is None:
        return f"Telegram не описал отправку кода: {type(sent).__name__}."
    template = _SENT_CODE.get(type(kind).__name__)
    if template is None:
        return f"Telegram ответил типом {type(kind).__name__}."
    return template.format_map(_sent_fields(kind))


def _watch_delivery(client: object) -> None:
    request = getattr(client, "send_code_request", None)
    if not callable(request):
        return
    send = cast("Callable[..., Awaitable[object]]", request)

    async def _logged(phone: str, **kwargs: object) -> object:
        sent = await send(phone, **kwargs)
        sys.stderr.write(f"{describe_sent_code(sent)}\n")
        return sent

    cast("_CodeClient", client).send_code_request = _logged


def _sent_fields(kind: object) -> dict[str, object]:
    fields = ("length", "email_pattern", "url", "pattern", "prefix", "beginning")
    return {name: getattr(kind, name, None) or "" for name in fields}


_SENT_CODE = {
    "SentCodeTypeApp": "Код в приложении, чат «Telegram». Длина {length}.",
    "SentCodeTypeSms": "Код по SMS. Длина {length}.",
    "SentCodeTypeCall": "Код продиктует звонок. Длина {length}.",
    "SentCodeTypeFirebaseSms": "Код через Firebase SMS. Длина {length}.",
    "SentCodeTypeEmailCode": "Код на почту {email_pattern}. Длина {length}.",
    "SentCodeTypeSetUpEmailRequired": "Код не отправлен: для входа нужна почта.",
    "SentCodeTypeFragmentSms": "Код через Fragment: {url}.",
    "SentCodeTypeFlashCall": "Код входящим звонком, шаблон {pattern}.",
    "SentCodeTypeMissedCall": "Код пропущенным звонком, префикс {prefix}, длина {length}.",
    "SentCodeTypeSmsWord": "Код — слово в SMS, начало «{beginning}».",
    "SentCodeTypeSmsPhrase": "Код — фраза в SMS, начало «{beginning}».",
}


async def _qr_sign_in(client: _QrClient) -> None:
    login = await client.qr_login()
    _show_qr(login.url)
    try:
        await login.wait()
    except TimeoutError:
        message = "QR-код истёк до сканирования."
        hint = f"Снова выполните {_SETUP} и отсканируйте код с телефона."
        raise SummaryError(message, hint, ErrorCode.NO_SESSION) from None
    except SessionPasswordNeededError:
        await client.sign_in(password=getpass.getpass("Пароль 2FA: "))


def _show_qr(url: str) -> None:
    sys.stderr.write(
        "На телефоне, где уже открыт этот аккаунт: "
        "Настройки → Устройства → Подключить устройство.\n"
        f"{url}\n",
    )
    image = qrcode.QRCode(border=1)
    image.add_data(url)
    image.make(fit=True)
    image.print_ascii(out=sys.stderr, invert=True)


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
    except PasswordHashInvalidError:
        message = "Telegram отклонил пароль 2FA."
        hint = f"Снова выполните {_SETUP} и введите облачный пароль."
        raise SummaryError(message, hint, ErrorCode.NO_CREDENTIALS) from None
    except FloodWaitError as exc:
        hint = f"Подождите и снова выполните {_SETUP}. Секреты на диске уже сохранены."
        raise flood_wait(exc.seconds, hint) from None


def _write_private(path: Path, text: str) -> None:
    replace_text(path, text, mode=0o600)
