"""Запись блока в ежедневную заметку. Все пути — tmp_path, vault не трогаем."""

from datetime import date
from pathlib import Path

import pytest

from app.config import Config, NotesConfig
from app.errors import SummaryError
from app.notes.writer import NoteWrite, write_note

_DAY = date(2026, 10, 5)
_BODY = """\
## 🤖 Итоги дня

### Сделано
- собрал сборщик
"""
_OUTSIDE = """\
---
date: 2026-10-05
---
# день

## 🎯 Сегодня в фокусе
- [ ] **Задача 1**

## ✅ На сегодня
- [ ] пункт
"""
_OLD_BLOCK = """\
<!-- auto:start -->
## 🤖 Итоги дня

### Сделано
- старое
<!-- auto:end -->
"""
_NEW_BLOCK = """\
<!-- auto:start -->
## 🤖 Итоги дня

### Сделано
- собрал сборщик
<!-- auto:end -->
"""
_DIFF = (
    "--- 2026-10-05.md\n"
    "+++ 2026-10-05.md\n"
    "@@ -12,5 +12,5 @@\n"
    " ## 🤖 Итоги дня\n"
    " \n"
    " ### Сделано\n"
    "-- старое\n"
    "+- собрал сборщик\n"
    " <!-- auto:end -->\n"
)


def _config(daily: Path) -> Config:
    return Config(notes=NotesConfig(daily_dir=daily, timezone="Europe/Moscow"))


def test_dry_run_replaces_block_and_leaves_disk(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    original = (_OUTSIDE + _OLD_BLOCK).encode()
    note.write_bytes(original)

    result = write_note(_config(tmp_path), _DAY, _BODY)

    assert (result, note.read_bytes()) == (
        NoteWrite(path=note, text=_OUTSIDE + _NEW_BLOCK, diff=_DIFF),
        original,
    )


def test_apply_appends_block_when_file_has_no_markers(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    note.write_bytes(_OUTSIDE.encode())

    result = write_note(_config(tmp_path), _DAY, _BODY, apply=True)

    assert (result, note.read_bytes()) == (
        NoteWrite(
            path=note,
            text=_OUTSIDE + _NEW_BLOCK,
            diff=(
                "--- 2026-10-05.md\n"
                "+++ 2026-10-05.md\n"
                "@@ -8,3 +8,9 @@\n"
                " \n"
                " ## ✅ На сегодня\n"
                " - [ ] пункт\n"
                "+<!-- auto:start -->\n"
                "+## 🤖 Итоги дня\n"
                "+\n"
                "+### Сделано\n"
                "+- собрал сборщик\n"
                "+<!-- auto:end -->\n"
            ),
        ),
        (_OUTSIDE + _NEW_BLOCK).encode(),
    )


def test_apply_keeps_existing_permissions(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    note.write_bytes(_OUTSIDE.encode())
    note.chmod(0o644)

    write_note(_config(tmp_path), _DAY, _BODY, apply=True)

    assert note.stat().st_mode & 0o777 == 0o644


def test_missing_end_marker_raises_and_keeps_the_tail(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    original = (
        _OUTSIDE + "<!-- auto:start -->\n## 🤖 Итоги дня\n" + "## 🌙 Рефлексия дня\n- кофе\n"
    ).encode()
    note.write_bytes(original)

    with pytest.raises(SummaryError) as exc_info:
        write_note(_config(tmp_path), _DAY, _BODY, apply=True)

    error = exc_info.value
    assert (error.code, error.message, note.read_bytes()) == (
        "BROKEN_MARKERS",
        "В заметке есть <!-- auto:start -->, но нет <!-- auto:end -->.",
        original,
    )


def test_blank_body_does_not_touch_file_even_with_apply(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    original = _OUTSIDE.encode()
    note.write_bytes(original)

    result = write_note(_config(tmp_path), _DAY, " \n", apply=True)

    assert (result, note.read_bytes()) == (NoteWrite(path=note, text=_OUTSIDE, diff=""), original)


def test_missing_note_without_template_raises(tmp_path: Path) -> None:
    with pytest.raises(SummaryError) as exc_info:
        write_note(_config(tmp_path), _DAY, _BODY, apply=True)

    assert (exc_info.value.code, exc_info.value.message, list(tmp_path.iterdir())) == (
        "NO_TEMPLATE",
        "Не задан шаблон ежедневной заметки.",
        [],
    )


_TEMPLATE = Path(__file__).parent / "fixtures" / "notes" / "Шаблон для ежедневных заметок.md"
_RENDERED = Path(__file__).parent / "fixtures" / "notes" / "rendered-2026-09-11.md"


def test_apply_creates_note_from_template(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    daily.mkdir()
    config = Config(
        notes=NotesConfig(daily_dir=daily, template=_TEMPLATE, timezone="Europe/Moscow"),
    )
    note = daily / "2026-09-11.md"
    expected = _RENDERED.read_text(encoding="utf-8") + _NEW_BLOCK

    preview = write_note(config, date(2026, 9, 11), _BODY)

    assert (preview.text, note.exists()) == (expected, False)

    written = write_note(config, date(2026, 9, 11), _BODY, apply=True)

    assert (written.text, note.read_text(encoding="utf-8")) == (expected, expected)


def test_empty_subsections_are_left_out(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    note.write_bytes(_OUTSIDE.encode())
    body = """\
## 🤖 Итоги дня

### Сделано
- собрал сборщик

### Решения и обоснования

### План на завтра
"""

    result = write_note(_config(tmp_path), _DAY, body, apply=True)

    expected = (
        _OUTSIDE + "<!-- auto:start -->\n## 🤖 Итоги дня\n\n### Сделано\n- собрал сборщик\n\n"
        "<!-- auto:end -->\n"
    )
    assert (result.text, note.read_bytes()) == (expected, expected.encode())


def test_headings_without_activity_do_not_touch_file(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    original = _OUTSIDE.encode()
    note.write_bytes(original)
    body = """\
## 🤖 Итоги дня

### Сделано

### План на завтра
"""

    result = write_note(_config(tmp_path), _DAY, body, apply=True)

    assert (result, note.read_bytes()) == (NoteWrite(path=note, text=_OUTSIDE, diff=""), original)


def test_second_apply_does_not_add_another_block(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    note.write_bytes(_OUTSIDE.encode())
    config = _config(tmp_path)

    write_note(config, _DAY, _BODY, apply=True)
    result = write_note(config, _DAY, _BODY, apply=True)

    assert (result, note.read_bytes()) == (
        NoteWrite(path=note, text=_OUTSIDE + _NEW_BLOCK, diff=""),
        (_OUTSIDE + _NEW_BLOCK).encode(),
    )


def test_text_after_markers_stays_byte_for_byte(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    tail = "## 🌙 Рефлексия дня\n- [ ] кофе\n"
    note.write_bytes((_OUTSIDE + _OLD_BLOCK + tail).encode())

    result = write_note(_config(tmp_path), _DAY, _BODY, apply=True)

    expected = _OUTSIDE + _NEW_BLOCK + tail
    assert (result.text, note.read_bytes()) == (expected, expected.encode())


def test_text_on_the_end_marker_line_stays(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    original = _OUTSIDE + "<!-- auto:start -->\n- старое\n<!-- auto:end --> хвост\n"
    note.write_bytes(original.encode())

    result = write_note(_config(tmp_path), _DAY, _BODY, apply=True)

    expected = _OUTSIDE + _NEW_BLOCK + " хвост\n"
    assert (result.text, note.read_bytes()) == (expected, expected.encode())


def test_prose_drops_following_empty_subsections(tmp_path: Path) -> None:
    note = tmp_path / "2026-10-05.md"
    note.write_bytes(_OUTSIDE.encode())
    body = "Обсудили сборщик.\n\n### Сделано\n\n### План на завтра\n"

    result = write_note(_config(tmp_path), _DAY, body, apply=True)

    expected = _OUTSIDE + "<!-- auto:start -->\nОбсудили сборщик.\n\n<!-- auto:end -->\n"
    assert (result.text, note.read_bytes()) == (expected, expected.encode())


def test_missing_template_file_raises(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    daily.mkdir()
    missing = tmp_path / "missing-template.md"
    config = Config(notes=NotesConfig(daily_dir=daily, template=missing))

    with pytest.raises(SummaryError) as exc_info:
        write_note(config, _DAY, _BODY, apply=True)

    assert (exc_info.value.code, exc_info.value.message, list(daily.iterdir())) == (
        "NO_TEMPLATE",
        "Файл шаблона ежедневной заметки не найден.",
        [],
    )
