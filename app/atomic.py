"""Замена файла целиком: обрыв не оставляет обрезанный текст."""

import os
import tempfile
from pathlib import Path


def replace_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Записать текст через временный файл и `os.replace`.

    Args:
        path: Куда положить готовый текст.
        text: Содержимое.
        mode: Права нового файла. `None` — оставить режим существующего
            или `0o644`, если файла ещё нет.

    Raises:
        OSError: Каталог недоступен или замена не удалась.
    """
    chosen = _mode(path) if mode is None else mode
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        os.fchmod(fd, chosen)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _mode(path: Path) -> int:
    if path.is_file():
        return path.stat().st_mode & 0o777
    return 0o644
