"""Маскирование секретов в тексте до записи дампа и до отправки в модель."""

import re

_REDACTED = "[REDACTED]"

# Блок целиком: внутри могут быть другие шаблоны, их уже не разбираем.
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Схема и пользователь остаются: по ним видно, какое подключение усекли.
_CONNECTION_PASSWORD = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:\s/@]+:)([^@\s]+)@")

_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}")

_KNOWN_TOKEN = re.compile(
    r"ghp_[A-Za-z0-9]{36}"
    r"|gho_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}",
)

# Имя может быть составным: secret_key, client_secret, aws_secret_access_key.
# Граница слова вокруг всего имени, иначе secret_key не совпадёт с `secret`.
_ASSIGNMENT_KEY = (
    r"(?:[A-Za-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?key|secret|token|password|passwd)"
    r"(?:[_-][A-Za-z0-9]+)*"
)
_ASSIGNED_SECRET = re.compile(rf"(?i)\b({_ASSIGNMENT_KEY})\b(\s*[:=]\s*)\S+")


def _mask_connection(match: re.Match[str]) -> str:
    """Убрать пароль из строки подключения.

    Args:
        match: Совпадение со схемой и пользователем в первой группе.

    Returns:
        Та же строка с паролем, заменённым на маркер.
    """
    return f"{match.group(1)}{_REDACTED}@"


def _mask_bearer(match: re.Match[str]) -> str:
    """Убрать токен после `Bearer`, оставив само слово.

    Args:
        match: Совпадение, где первая группа — `Bearer` и пробел.

    Returns:
        `Bearer` и маркер.
    """
    return f"{match.group(1)}{_REDACTED}"


def _mask_assignment(match: re.Match[str]) -> str:
    """Убрать значение присваивания, оставив имя поля.

    Args:
        match: Совпадение с именем и разделителем в первых двух группах.

    Returns:
        Имя, разделитель и маркер.
    """
    return f"{match.group(1)}{match.group(2)}{_REDACTED}"


def mask_secrets(text: str) -> str:
    """Заменить секреты в уже собранном тексте.

    Вызывается до записи дампа: в файле секретов тоже быть не должно.

    Args:
        text: Сырой текст источника.

    Returns:
        Тот же текст с секретами, заменёнными на `[REDACTED]`.
    """
    masked = _PRIVATE_KEY.sub(_REDACTED, text)
    masked = _CONNECTION_PASSWORD.sub(_mask_connection, masked)
    masked = _BEARER.sub(_mask_bearer, masked)
    masked = _KNOWN_TOKEN.sub(_REDACTED, masked)
    return _ASSIGNED_SECRET.sub(_mask_assignment, masked)
