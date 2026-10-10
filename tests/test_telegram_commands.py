"""Подкоманды `init` и `chats`: клиент подменён, сеть не вызывается."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from telethon.errors import SessionPasswordNeededError

from app.cli import main

_MOSCOW = ZoneInfo("Europe/Moscow")
_HASH = "hash-secret-value"
_PHONE = "+79990000000"


def test_init_asks_only_for_the_missing_phone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _write_env(home, f"TG_API_ID=1\nTG_API_HASH={_HASH}\n")
    monkeypatch.setattr("builtins.input", lambda _prompt: _PHONE)
    monkeypatch.setattr("getpass.getpass", _refuse_getpass)
    monkeypatch.setattr("app.collectors.telegram.tg.make_client", _client_factory(live=False))
    config = _config(tmp_path)

    exit_code = main(["init", "--login", "qr", "--config", str(config)])

    env = (home / ".config" / "day-recap" / ".env").read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert (exit_code, env, out) == (
        0,
        f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n",
        "Вошли как Анна / @anna / +79990000000\n",
    )


def test_init_from_scratch_writes_a_private_env_and_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    answers = iter(("1", _PHONE))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda _prompt: _HASH)
    monkeypatch.setattr("app.collectors.telegram.tg.make_client", _client_factory(live=False))
    config = _config(tmp_path)

    exit_code = main(["init", "--login", "qr", "--config", str(config)])

    env_path = home / ".config" / "day-recap" / ".env"
    session = tmp_path / "state" / "session.session"
    out = capsys.readouterr().out
    assert (
        exit_code,
        env_path.read_text(encoding="utf-8"),
        oct(env_path.stat().st_mode & 0o777),
        oct(env_path.parent.stat().st_mode & 0o777),
        oct(session.stat().st_mode & 0o777),
        session.read_bytes(),
        out,
    ) == (
        0,
        f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n",
        "0o600",
        "0o700",
        "0o600",
        b"new",
        "Вошли как Анна / @anna / +79990000000\n",
    )


def test_init_keeps_a_live_session_without_asking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _write_env(home, f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n")
    session = tmp_path / "state" / "session.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"live")
    monkeypatch.setattr("builtins.input", _refuse_input)
    monkeypatch.setattr("getpass.getpass", _refuse_getpass)
    monkeypatch.setattr("app.collectors.telegram.tg.make_client", _client_factory(live=True))

    exit_code = main(["init", "--config", str(_config(tmp_path))])

    assert (exit_code, session.read_bytes(), capsys.readouterr().out) == (
        0,
        b"live",
        "Вошли как Анна / @anna / +79990000000\n",
    )


def test_relogin_deletes_the_session_file_instead_of_log_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _write_env(home, f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n")
    session = tmp_path / "state" / "session.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"old")
    monkeypatch.setattr("builtins.input", _refuse_input)
    monkeypatch.setattr("getpass.getpass", _refuse_getpass)
    monkeypatch.setattr(
        "app.collectors.telegram.tg.make_client",
        _client_factory(live=True, relogin=True),
    )

    exit_code = main(["init", "--relogin", "--login", "qr", "--config", str(_config(tmp_path))])

    assert (exit_code, capsys.readouterr().out) == (
        0,
        "Вошли как Новая / @new / +79990000000\n",
    )


def test_qr_login_asks_for_the_cloud_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _write_env(home, f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n")
    monkeypatch.setattr("builtins.input", _refuse_input)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "cloud")
    monkeypatch.setattr(
        "app.collectors.telegram.tg.make_client",
        _client_factory(live=False, cloud="cloud"),
    )

    exit_code = main(["init", "--login", "qr", "--config", str(_config(tmp_path))])

    assert (exit_code, capsys.readouterr().out) == (
        0,
        "Вошли как Анна / @anna / +79990000000\n",
    )


def test_init_without_flag_uses_the_typed_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _write_env(home, f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n")
    monkeypatch.setattr("builtins.input", lambda _prompt: "code")
    monkeypatch.setattr("getpass.getpass", _refuse_getpass)
    monkeypatch.setattr("app.collectors.telegram.tg.make_client", _client_factory(live=False))

    exit_code = main(["init", "--config", str(_config(tmp_path))])

    session = tmp_path / "state" / "session.session"
    assert (exit_code, session.read_bytes(), capsys.readouterr().out) == (
        0,
        b"code",
        "Вошли как Анна / @anna / +79990000000\n",
    )


def test_chats_prints_private_and_group_with_toml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    _isolate_home(monkeypatch, home)
    _write_env(home, f"TG_API_ID=1\nTG_API_HASH={_HASH}\nTG_PHONE={_PHONE}\n")
    session = tmp_path / "state" / "session.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"live")
    when = datetime(2026, 10, 5, 18, 30, tzinfo=_MOSCOW)
    monkeypatch.setattr(
        "app.collectors.telegram.tg.make_client",
        lambda _session, _api_id, _api_hash: _Dialogs(when),
    )

    exit_code = main(["chats", "--config", str(_config(tmp_path))])

    assert (exit_code, capsys.readouterr().out) == (
        0,
        (
            "id\tназвание\tтип\tпоследнее\n"
            f'7\tЛичка "Аня"\tличный\t{when.isoformat()}\n'
            f"-100\tРабота\tгруппа\t{when.isoformat()}\n"
            "\n"
            "[[telegram.chats]]\n"
            "id = 7\n"
            'name = "Личка \\"Аня\\""\n'
            "\n"
            "[[telegram.chats]]\n"
            "id = -100\n"
            'name = "Работа"\n'
        ),
    )


def _refuse_input(_prompt: str) -> str:
    message = "input"
    raise AssertionError(message)


def _refuse_getpass(_prompt: str) -> str:
    message = "getpass"
    raise AssertionError(message)


def _isolate_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)


def _write_env(home: Path, text: str) -> None:
    path = home / ".config" / "day-recap" / ".env"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def _config(root: Path) -> Path:
    path = root / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[notes]",
                'daily_dir = ""',
                'timezone = "Europe/Moscow"',
                "",
                "[state]",
                f'dir = "{root / "state"}"',
            ],
        ),
        encoding="utf-8",
    )
    return path


def _client_factory(*, live: bool, relogin: bool = False, cloud: str | None = None) -> object:
    def make(session: Path, _api_id: int, _api_hash: str) -> _Login:
        return _Login(session, live=live, relogin=relogin, cloud=cloud)

    return make


class _PendingQr:
    url = "tg://login?token=test"

    def __init__(self, login: "_Login") -> None:
        self.login = login

    async def wait(self) -> None:
        login = self.login
        if login.cloud is not None and not login.signed_in:
            raise SessionPasswordNeededError(request=None)


class _Login:
    def __init__(
        self,
        session: Path,
        *,
        live: bool,
        relogin: bool,
        cloud: str | None = None,
    ) -> None:
        self.session = session
        self.live = live
        self.relogin = relogin
        self.cloud = cloud
        self.started = False
        self.signed_in = cloud is None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        if self.started:
            return True
        return self.live and self.session.is_file()

    async def qr_login(self) -> _PendingQr:
        self.started = True
        self.session.parent.mkdir(parents=True, exist_ok=True)
        self.session.write_bytes(b"new")
        return _PendingQr(self)

    async def start(self, phone: str, **_kwargs: object) -> None:
        if phone != _PHONE:
            message = phone
            raise RuntimeError(message)
        self.started = True
        self.session.parent.mkdir(parents=True, exist_ok=True)
        self.session.write_bytes(b"code")

    async def sign_in(self, password: str) -> None:
        if password != self.cloud:
            message = "пароль"
            raise RuntimeError(message)
        self.signed_in = True

    async def get_me(self) -> SimpleNamespace:
        if self.cloud is not None and not self.signed_in:
            message = "нет пароля"
            raise RuntimeError(message)
        if self.started and self.relogin:
            return SimpleNamespace(
                first_name="Новая",
                last_name=None,
                username="new",
                phone="79990000000",
            )
        if self.started or self.live:
            return SimpleNamespace(
                first_name="Анна",
                last_name=None,
                username="anna",
                phone="79990000000",
            )
        message = "нет сессии"
        raise RuntimeError(message)

    async def log_out(self) -> None:
        message = "log_out"
        raise AssertionError(message)


class _Dialogs:
    def __init__(self, when: datetime) -> None:
        self.when = when

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True

    async def iter_dialogs(self) -> object:
        yield SimpleNamespace(
            id=7,
            name='Личка "Аня"',
            date=self.when,
            is_user=True,
            is_group=False,
            is_channel=False,
        )
        yield SimpleNamespace(
            id=-100,
            name="Работа",
            date=self.when,
            is_user=False,
            is_group=True,
            is_channel=True,
        )
