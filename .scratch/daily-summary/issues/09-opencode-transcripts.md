# 09 — Сборщик транскриптов OpenCode

Status: resolved
Blocked by: 04

Сессии OpenCode лежат не в JSONL, а в SQLite, поэтому в тикет 04 это не
вливается. На машине автора база — `~/.local/share/opencode/opencode.db`:
336 сессий, 19 проектов. Сообщения — таблицы `message` и `part`.

## Задача

`app/collectors/opencode.py`. В дамп попадает то же, что уже умеет схема:
`TranscriptSession` и `TranscriptMessage`.

- путь к базе из конфига, по умолчанию `~/.local/share/opencode/opencode.db`;
- открывать только на чтение (`file:…?mode=ro`). Читать только `session`,
  `project`, `message`, `part`. Таблицы `account`, `credential` и
  `session_share` не трогать: там токены доступа;
- зависимость не добавлять, хватает `sqlite3` из стандартной библиотеки;
- роль реплики — `message.data.role` (`user` или `assistant`);
- текст — части `part.data` с `type = "text"` и без `synthetic = 1`.
  `synthetic` — вставки самого OpenCode, не слова пользователя;
- отбрасывать части `tool`, `reasoning`, `patch`, `file`, `step-start`,
  `step-finish`, `compaction`, `subtask`. `patch` дублирует diff, который
  уже собирает git-сборщик;
- в окно дня попадают реплики по `message.time_created` (миллисекунды).
  Погрешность «весь файл по mtime» из тикета 04 здесь не действует;
- `project` в сессии — `session.directory`, настоящий путь к каталогу;
- длинная сессия усекается до `head_messages` первых и `tail_messages`
  последних реплик, запись об усечении — в `truncations`;
- нет файла базы или `enabled = false` — источник `disabled` или
  `unavailable`, сбор Cursor от этого не падает.

## Конфиг

Отдельная секция, лимиты не делятся с `[transcripts]`:

```toml
[opencode]
enabled = true
db = "~/.local/share/opencode/opencode.db"
head_messages = 10
tail_messages = 10
```

## Схема дампа

В `Sources` добавить поле `opencode: TranscriptsSource` со значением по
умолчанию. `schema_version` остаётся 1: старые дампы без ключа читаются,
а недоступная база не роняет остальные источники.

## Скилл

Тикет 06 на этот не блокируется: первая рабочая версия саммари должна
собираться и без OpenCode. Когда скилл пишется или уже написан, он читает
`sources.transcripts` и `sources.opencode` одинаково.

## Готово, когда

- тесты поднимают временную базу в `tmp_path`. Живой `opencode.db` в
  фикстуры не копируется;
- в результат попадают только `user` и `assistant` с несинтетическим текстом;
- реплика вне окна дня не попадает, даже если сессия начата раньше;
- усечение длинной сессии пишется в `truncations`;
- отсутствующий файл базы даёт `unavailable` и не роняет сбор.

## Answer

`collect_opencode` читает `session`, `message` и `part` из базы только на чтение. В дамп попадают реплики `user` и `assistant` с несинтетическим текстом внутри окна по `time_created`. `sources.opencode` по умолчанию `disabled`, поэтому старый дамп без ключа читается. Нет файла — источник `unavailable`, `enabled = false` — `disabled`, сбор Cursor и git при этом продолжается.
