"""Подстановка блока итогов в ежедневную заметку."""

import difflib
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.config import Config, require_notes_dir
from app.errors import ErrorCode, SummaryError

_START = "<!-- auto:start -->"
_END = "<!-- auto:end -->"
_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


class NoteWrite(BaseModel):
    """Итоговый текст заметки и diff. Без `apply` на диск ничего не пишется."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    text: str
    diff: str


def write_note(config: Config, day: date, body: str, *, apply: bool = False) -> NoteWrite:
    """Подставить тело между маркерами и вернуть текст с diff.

    Без `apply` файл не меняется. Маркеров нет — блок дописывается в конец.
    Пустое тело и блок без текста подсекций не записываются даже с `apply`.
    Пустые подсекции `###` не попадают в блок. Если файла нет, он собирается
    из шаблона: `{{date:YYYY-MM-DD}}` и длинная дата по-русски.

    Args:
        config: Настройки. Каталог заметок обязателен.
        day: День заметки. Имя файла — `filename_format` плюс `.md`.
        body: Текст между маркерами, без самих маркеров.
        apply: Записать итоговый текст на диск.

    Returns:
        Путь, итоговый текст и unified diff.

    Raises:
        SummaryError: `NO_NOTES_DIR`, если каталог не задан.
            `NO_TEMPLATE`, если заметки ещё нет, а шаблон не задан.
            `BROKEN_MARKERS`, если пара маркеров сломана.
    """
    daily_dir = require_notes_dir(config)
    path = daily_dir / f"{day.strftime(config.notes.filename_format)}.md"
    body = _omit_empty_sections(body)
    if body.strip() == "":
        text = _read_text(path) if path.is_file() else ""
        return NoteWrite(path=path, text=text, diff="")
    original = _read_note(path, config, day)
    updated = _splice(original, _render_block(body))
    diff = _unified_diff(path.name, original, updated)
    if apply and updated != original:
        _atomic_write(path, updated)
    return NoteWrite(path=path, text=updated, diff=diff)


def _read_text(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _read_note(path: Path, config: Config, day: date) -> str:
    if path.is_file():
        return _read_text(path)
    template = config.notes.template
    if template is not None and template.is_file():
        return _render_dates(_read_text(template), day)
    raise _missing_template(template)


def _render_dates(template: str, day: date) -> str:
    long_date = f"{_WEEKDAYS[day.weekday()]}, {day.day} {_MONTHS[day.month - 1]} {day.year}"
    rendered = template.replace("{{date:YYYY-MM-DD}}", day.isoformat())
    return rendered.replace("{{date:dddd, Do of MMMM, YYYY}}", long_date)


def _missing_template(template: Path | None) -> SummaryError:
    if template is None:
        message = "Не задан шаблон ежедневной заметки."
        hint = "Укажите notes.template в ~/.config/daily-summary/config.toml."
    else:
        message = "Файл шаблона ежедневной заметки не найден."
        hint = f"Проверьте путь notes.template: {template}."
    return SummaryError(message, hint, ErrorCode.NO_TEMPLATE)


def _splice(original: str, block: str) -> str:
    start = original.find(_START)
    if start == -1:
        return _append(original, block)
    end_at = original.find(_END, start + len(_START))
    if end_at == -1:
        message = "В заметке есть <!-- auto:start -->, но нет <!-- auto:end -->."
        hint = "Добавьте <!-- auto:end --> или уберите <!-- auto:start -->."
        raise SummaryError(message, hint, ErrorCode.BROKEN_MARKERS)
    suffix_from = end_at + len(_END)
    if original.startswith("\n", suffix_from):
        suffix_from += 1
    return original[:start] + block + original[suffix_from:]


def _append(original: str, block: str) -> str:
    if original.endswith("\n") or original == "":
        return original + block
    return f"{original}\n{block}"


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o777 if path.is_file() else 0o644
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _omit_empty_sections(body: str) -> str:
    parts = re.split(r"(?=^### )", body, flags=re.MULTILINE)
    kept = [part for part in parts[1:] if _section_has_content(part)]
    if kept:
        return parts[0] + "".join(kept)
    if _heading_only(parts[0]):
        return ""
    return parts[0]


def _section_has_content(part: str) -> bool:
    _, _, rest = part.partition("\n")
    return rest.strip() != ""


def _heading_only(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    return all(line.startswith("#") for line in lines)


def _render_block(body: str) -> str:
    inner = body if body.endswith("\n") else f"{body}\n"
    return f"{_START}\n{inner}{_END}\n"


def _unified_diff(name: str, original: str, updated: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=name,
            tofile=name,
        ),
    )
