# 02 — Конфиг, модели и ошибки

Status: resolved
Blocked by: 01

Конфиг и схема сырого дампа. Всё остальное от них зависит, поэтому идёт вторым.

## Задача

### `app/errors.py`

`SummaryError(message, hint, code)`. Домен ничего не печатает — формат вывода
выбирает `cli.py`. Коды: `NO_CREDENTIALS`, `NO_SESSION`, `FLOOD_WAIT`,
`CANNOT_RESOLVE`, `NOT_A_MEMBER`, `NO_NOTES_DIR`.

Это нужно ради агента: получив `NO_SESSION`, он обязан вернуть задачу человеку, а
не логиниться сам.

### `app/config.py`

Чтение `~/.config/daily-summary/config.toml` через pydantic-settings, секреты из
`~/.config/daily-summary/.env`. Путь к конфигу переопределяется флагом
`--config`.

```toml
[notes]
daily_dir = ""                      # пустой по умолчанию: писать некуда
template = ""
filename_format = "%Y-%m-%d"
timezone = "Europe/Moscow"

[state]
dir = "~/.local/state/daily-summary"
raw_retention_days = 14

[git]
roots = ["~/projects"]
max_depth = 3
authors = [
    "vvoronov@mwnts.ru",
    "vvoronov@sila.ru",
    "30475117+redb0@users.noreply.github.com",
]
include_dirty = true
exclude_globs = [
    "*.min.js", "*.lock", "*.sum", "*.pb.go", "*_pb2.py",
    "*.svg", "*.png", "vendor/*", "dist/*",
]
max_diff_lines_per_file = 400
max_diff_lines_per_commit = 2000
max_diff_lines_per_day = 6000

[transcripts]
roots = ["~/.cursor/projects"]
head_messages = 10
tail_messages = 10

[telegram]
enabled = true
max_messages_per_chat = 500
page_size = 100
flood_wait_retry_seconds = 60

[[telegram.chats]]
id = -1001234567890
name = "пример: НТР / FM core"

[summary]
two_stage_threshold_bytes = 100_000
```

Рядом — `config.example.toml` в репозитории.

### Схема сырого дампа

`app/summary/models.py`, pydantic. Версионируем с первого дня: `schema_version`
понадобится, когда появится недельная агрегация, читающая старые дампы.

```json
{
  "schema_version": 1,
  "date": "2026-10-05",
  "window": {"from": "2026-10-05T00:00:00+03:00", "to": "2026-10-05T19:40:00+03:00"},
  "generated_at": "2026-10-05T19:40:12+03:00",
  "sources": {
    "git":         {"status": "ok",          "repos": []},
    "transcripts": {"status": "ok",          "sessions": []},
    "telegram":    {"status": "unavailable", "code": "FLOOD_WAIT", "reason": "…", "chats": []}
  },
  "stats": {"commits": 0, "messages": 0, "sessions": 0, "bytes": 0},
  "truncations": []
}
```

`status` одного из: `ok`, `empty`, `disabled`, `unavailable`. Поле
`truncations` — список человекочитаемых записей вида «diff файла X усечён до 400
строк»: они попадают в блок заметки, чтобы усечение не выглядело как отсутствие
работы.

### Маскирование секретов

`app/summary/masking.py`: regex по токенам, ключам, `Bearer`, приватным ключам,
строкам подключения с паролями. Применяется к собранному тексту до записи дампа,
а не при чтении: в дампе секретов тоже быть не должно.

## Готово, когда

- конфиг читается, значения по умолчанию работают без файла на диске;
- отсутствующий `notes.daily_dir` даёт `SummaryError` с кодом `NO_NOTES_DIR` и
  внятной подсказкой — но только на записи, не на сборе;
- есть тесты на маскирование и на round-trip схемы дампа.
