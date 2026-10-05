# experience

Накопление рабочего опыта: что сделано и какие решения приняты.

## daily-summary

CLI, который собирает проделанную за день работу из локальных репозиториев git,
транскриптов агентов Cursor и чатов Telegram, после чего скилл
`/daily-summary` уточняет детали и дописывает блок в ежедневную заметку
Obsidian.

Дизайн и порядок работ: [`.scratch/daily-summary/`](.scratch/daily-summary/).

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
