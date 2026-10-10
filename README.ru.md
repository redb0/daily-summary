# daily-summary

[English](README.md)

[![tests](https://github.com/redb0/daily-summary/actions/workflows/test.yaml/badge.svg?branch=main)](https://github.com/redb0/daily-summary/actions/workflows/test.yaml)
[![coverage](https://codecov.io/gh/redb0/daily-summary/branch/main/graph/badge.svg)](https://codecov.io/gh/redb0/daily-summary)
![python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

Собирает проделанное за день из локальных репозиториев git, транскриптов
Cursor, сессий OpenCode и белого списка Telegram. В ежедневную заметку
Obsidian попадает только подтверждённый блок.

Дамп лежит вне хранилища. Пустой день заметку не меняет.

```markdown
## 🤖 Итоги дня

### Сделано
- **billing** — сузил окно повторной оплаты до одного счёта

### Решения и обоснования
Оставили повтор по idempotency-key. Повтор всей корзины отбросили: он списывал деньги второй раз.
```

## Установка и конфигурация

Из каталога этого репозитория:

```sh
uv tool install --editable .
mkdir -p ~/.config/daily-summary
cp config.example.toml ~/.config/daily-summary/config.toml
```

`daily-summary init` запускайте в своём терминале, не в чате агента. `api_id` и
`api_hash` в чат не вводите. `init` спрашивает `qr` или `code`. `qr` печатает
код для сканирования: Настройки → Устройства → Подключить устройство. `code`
ждёт код из чата «Telegram» на уже открытом аккаунте. `--login qr` или
`--login code` пропускают вопрос. `--relogin` входит заново.

`daily-summary chats` печатает таблицу и готовые блоки `[[telegram.chats]]`.
Нужные блоки вставьте в секцию `[telegram]`. В дамп читаются только они.

| Секция | Что задаёт |
| --- | --- |
| `[notes]` | Куда писать ежедневную заметку. |
| `[state]` | Где лежат дампы. |
| `[git]` | Какие каталоги обходить и чьи коммиты брать. |
| `[transcripts]` | Корни JSONL Cursor и обрезка реплик. |
| `[opencode]` | База OpenCode и та же обрезка. |
| `[telegram]` | Включён ли источник и белый список. |
| `[summary]` | Порог. |

Ключи и значения по умолчанию — в [`config.example.toml`](config.example.toml).

- Порог — 100000 байт.
- Дамп хранится 14 дней.
- `~/.config/daily-summary/.env` создаётся с правами 600.

## В агенте

Положите [`SKILL.md`](SKILL.md) в `~/.agents/skills/daily-summary/SKILL.md`.

```text
/daily-summary
/daily-summary yesterday
/daily-summary 2026-10-05
```

Дата — `today`, `yesterday` или `YYYY-MM-DD`. Без даты окно идёт с локальной
полуночи до момента запуска.

Скилл показывает черновик и спрашивает, что решили и что отбросили. Заметка
меняется только после отдельного согласия. Пустой день заметку не меняет.
Если сессии Telegram нет, остальные источники всё равно собираются, а в блоке
есть строка «Telegram недоступен, чаты не учтены».

## Дополнительные команды

`collect` сохраняет дамп. `show` печатает сводку, а с именем источника — его
тело. Источник необязателен: `git`, `transcripts`, `opencode` или `telegram`.
Обе команды принимают `--date` (`today`, `yesterday` или `YYYY-MM-DD`) и
`--config PATH`.

`write` читает блок из stdin. Ей нужны `--date YYYY-MM-DD` и `--body -`,
принимает `--config PATH`. Без `--apply` заметка не меняется.

```sh
daily-summary collect --date yesterday
daily-summary show git --date 2026-10-05
daily-summary write --date 2026-10-05 --body - --apply
```

## Разработка

```sh
just lint    # ruff
just fmt     # ruff format
just types   # mypy
just test    # pytest
```
