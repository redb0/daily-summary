"""Маскирование секретов в собранном тексте до записи дампа."""

import pytest

from app.summary.masking import mask_secrets


def test_masks_github_personal_access_token() -> None:
    raw = "в diff попал ghp_abcdefghijklmnopqrstuvwxyz0123456789 и всё"

    assert mask_secrets(raw) == "в diff попал [REDACTED] и всё"


@pytest.mark.parametrize(
    "token",
    [
        "gho_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_11ABCDEFGHIJKLMNOPQR",
        "glpat-abcdefghijklmnopqrst",
        "AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_masks_known_token_and_key_shapes(token: str) -> None:
    assert mask_secrets(f"утекло {token} в логе") == "утекло [REDACTED] в логе"


def test_masks_bearer_token() -> None:
    raw = "Authorization: Bearer ya29.a0AfH6SMCexample.token"

    assert mask_secrets(raw) == "Authorization: Bearer [REDACTED]"


def test_masks_private_key_block() -> None:
    raw = (
        "ключ:\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXkBAAAA\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
        "дальше текст"
    )

    assert mask_secrets(raw) == "ключ:\n[REDACTED]\nдальше текст"


def test_masks_password_in_connection_string() -> None:
    assert (
        mask_secrets("postgres://app:s3cret@db.internal:5432/daily")
        == "postgres://app:[REDACTED]@db.internal:5432/daily"
    )
    assert (
        mask_secrets("mongodb+srv://user:p%40ss@host.example/db")
        == "mongodb+srv://user:[REDACTED]@host.example/db"
    )


def test_masks_assigned_secret() -> None:
    assert mask_secrets("api_key=sk-test-1234567890") == "api_key=[REDACTED]"
    assert mask_secrets('password = "s3cret-value"') == "password = [REDACTED]"
    assert mask_secrets("secret_key=abc12345") == "secret_key=[REDACTED]"
    assert mask_secrets("client_secret=abc12345") == "client_secret=[REDACTED]"
    assert mask_secrets("aws_secret_access_key=abc12345") == "aws_secret_access_key=[REDACTED]"


def test_leaves_ordinary_text() -> None:
    raw = (
        "Решили оставить слово Bearer и token в обсуждении. "
        "Ссылка https://example.com/path без пароля."
    )

    assert mask_secrets(raw) == raw


def test_masks_every_secret_in_one_text() -> None:
    raw = "ghp_abcdefghijklmnopqrstuvwxyz0123456789 и postgres://app:s3cret@db.internal/daily"

    assert mask_secrets(raw) == ("[REDACTED] и postgres://app:[REDACTED]@db.internal/daily")
