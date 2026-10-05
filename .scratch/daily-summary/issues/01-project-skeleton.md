# 01 — Каркас проекта

Status: resolved

Первый код в репозитории `experience`, поэтому issue задаёт форму на будущее.

## Задача

- `git init` в корне репозитория: сейчас это не git-репозиторий;
- `pyproject.toml`: имя `daily-summary`, Python 3.12+, сборка через `uv_build`,
  `[project.scripts] daily-summary = "app.cli:main"`;
- пакет `app/` с `__init__.py`, `py.typed` и заглушкой `cli.py` на argparse,
  отвечающей на `--help` и `--version`;
- линтеры по решению Q31: ruff с `lint.select = ["ALL"]`, `line-length = 100`,
  `pydocstyle` в стиле google, mypy в strict, pytest. Без
  `wemake-python-styleguide` и без `import-linter`;
- commitizen с conventional commits, как в рабочих проектах;
- `justfile` с рецептами `lint`, `fmt`, `types`, `test`;
- `.gitignore`: `.venv`, кэши ruff/mypy/pytest, `__pycache__`, `*.egg-info`,
  `.env`. Состояние инструмента лежит в `~/.local/state/`, но строка на случай
  запуска из каталога проекта не повредит;
- `tests/` с одним дымовым тестом на `--help`.

## Детали

Группы зависимостей через PEP 735 `[dependency-groups]`, а не
`[project.optional-dependencies]`: группа — это понятие времени разработки,
которое бэкенд сборки не пишет в метаданные колеса, и pytest не дотянется до
того, кто установит пакет.

`per-file-ignores` для `tests/*`: `S101` (assert).

## Готово, когда

- `just lint`, `just types`, `just test` проходят на чистом дереве;
- `uv tool install --editable .` даёт работающую команду `daily-summary --help`
  из любого каталога;
- первый коммит оформлен по conventional commits.
