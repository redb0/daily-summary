"""Ошибки домена. Печатает их CLI, не этот модуль."""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Машинный код, по которому агент решает, звать ли человека."""

    NO_CREDENTIALS = "NO_CREDENTIALS"
    NO_SESSION = "NO_SESSION"
    FLOOD_WAIT = "FLOOD_WAIT"
    TELEGRAM = "TELEGRAM"
    CANNOT_RESOLVE = "CANNOT_RESOLVE"
    NOT_A_MEMBER = "NOT_A_MEMBER"
    NO_NOTES_DIR = "NO_NOTES_DIR"
    NO_TEMPLATE = "NO_TEMPLATE"
    BROKEN_MARKERS = "BROKEN_MARKERS"


class SummaryError(Exception):
    """Ошибка с текстом для человека и кодом для агента.

    Attributes:
        message: Что случилось.
        hint: Что сделать человеку.
        code: Стабильный код, не текст.
    """

    def __init__(self, message: str, hint: str, code: ErrorCode) -> None:
        """Запомнить сообщение, подсказку и код.

        Args:
            message: Что случилось.
            hint: Что сделать человеку.
            code: Стабильный код.
        """
        self.message = message
        self.hint = hint
        self.code = code
        super().__init__(message)
