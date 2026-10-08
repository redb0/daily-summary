# experience

Накопление рабочего опыта: что сделано и какие решения приняты.

## daily-summary

CLI, который собирает проделанную за день работу из локальных репозиториев git,
транскриптов агентов Cursor и чатов Telegram, после чего скилл
`/daily-summary` уточняет детали и дописывает блок в ежедневную заметку
Obsidian.

Дизайн и порядок работ: [`.scratch/daily-summary/`](.scratch/daily-summary/).

### Скилл

Файл `SKILL.md` положите в `~/.agents/skills/daily-summary/SKILL.md`.
В чате агента вызов явный:

```text
/daily-summary
/daily-summary yesterday
/daily-summary 2026-10-05
```

Дата — `today`, `yesterday` или `YYYY-MM-DD`. Без даты окно — с локальной
полуночи до момента запуска.

Один раз до этого:

1. Команда `daily-summary` в PATH (`uv tool install --editable .` ниже).
2. Конфиг, как в разделе ниже.
3. В своём терминале, не в чате агента: `daily-summary init`.
   Команда спросит `qr` или `code`. Флаги `--login qr` и `--login code`
   пропускают вопрос. `qr` печатает код для сканирования:
   Настройки → Устройства → Подключить устройство.
   `code` ждёт код из чата «Telegram» на уже открытом аккаунте.
   `api_id` и `api_hash` в чат не вводить.
4. Там же: `daily-summary chats`. Команда печатает таблицу
   (id, название, тип, последнее сообщение) и готовые блоки
   `[[telegram.chats]]`. Нужные блоки вставьте в
   `~/.config/daily-summary/config.toml` в секцию `[telegram]`
   вместо примера из `config.example.toml`. В дамп читаются только они.

В диалоге агент собирает день и показывает черновик «Итоги дня». Сначала
нужен ответ, что решили и какую альтернативу отбросили, либо «добавлять
нечего». Запись — отдельное следующее «да» уже после показа итогового блока.
Пустой день заметку не меняет.
Если сессии Telegram нет, итог по остальным источникам всё равно собирается,
а в блоке будет строка «Telegram недоступен, чаты не учтены»; вход снова
через `daily-summary init` в терминале.

### Конфиг

По умолчанию CLI читает `~/.config/daily-summary/config.toml`. Файла может
не быть: тогда остаются значения из кода, те же, что в `config.example.toml`.
Свой toml — флаг `--config PATH` у `collect`, `write`, `init` и `chats`;
такой файл обязан существовать. Секреты при любом `--config` читаются из
`~/.config/daily-summary/.env`. Неизвестный ключ в toml — ошибка чтения.
Пустая строка в пути значит «не задано», `~` разворачивается.

```sh
mkdir -p ~/.config/daily-summary
cp config.example.toml ~/.config/daily-summary/config.toml
```

`[notes]` — куда писать заметку. Пока `daily_dir` пустой, `collect` дамп
собирает, а `write` останавливается. `template` нужен, только если файла за
день ещё нет: в шаблоне подставляются `{{date:YYYY-MM-DD}}` и
`{{date:dddd, Do of MMMM, YYYY}}`. `filename_format` — strftime, по умолчанию
`%Y-%m-%d`, заметка `2026-10-05.md`. `timezone` задаёт границы дня
(`Europe/Moscow`).

`[git]` — `roots` и `max_depth` задают обход репозиториев, `authors` — чьи
коммиты брать. `include_dirty` добавляет незакоммиченное в сбор без даты
и в сегодняшний календарный день; конец уже захваченного окна «до сейчас»
эти файлы не отсекает. Названный прошедший день их не включает.
`exclude_globs` и три `max_diff_lines_*`
отсекают шум и длинные diff.

`[transcripts]` — корни JSONL Cursor (`~/.cursor/projects`) и сколько реплик
брать с начала и с конца сессии (`head_messages`, `tail_messages`).

`[opencode]` — та же обрезка для базы OpenCode
(`~/.local/share/opencode/opencode.db`). `enabled = false` источник
выключает. Нет файла базы — день собирается без этих сессий.

`[telegram]` — `enabled` и белый список `[[telegram.chats]]` с `id` и `name`.
Список и готовые блоки даёт `daily-summary chats` после `init`; в toml
вставляются только те блоки, которые должны попасть в дамп. Пример
`id` в `config.example.toml` — заглушка, его заменяют. Чаты вне списка
в дамп не читаются: если в окне была активность, в блок попадает только
их число.
Пока `collect` обходит чаты и диалоги, ход пишется в stderr
(`telegram: чат 1/N`, `telegram: диалоги K`). Путь к дампу — первая строка stdout.
`api_id`, `api_hash` и телефон в toml не пишутся: их спрашивает
`daily-summary init` и кладёт в `.env` (`TG_API_ID`, `TG_API_HASH`,
`TG_PHONE`) с правами 600. Повторный вход: `daily-summary init --relogin`.

`[state]` — сырые дампы вне vault, по умолчанию
`~/.local/state/daily-summary`, хранение `raw_retention_days` (14). Дамп
старше ретенции не сохраняется.

`[summary]` — `two_stage_threshold_bytes` (100000): больше этого объёма скилл
сначала сжимает каждый источник отдельно, затем собирает общую картину.

### Разработка

```sh
just lint    # ruff
just fmt     # ruff format
just types   # mypy
just test    # pytest
```

Установка команды в PATH из исходников:

```sh
uv tool install --editable .
```
