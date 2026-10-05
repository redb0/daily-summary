default: lint types test

# Проверка линтером
lint:
    uv run ruff check .

# Автоформатирование и автоисправления
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Проверка типов
types:
    uv run mypy app tests

# Тесты
test:
    uv run pytest

# Установить команду в PATH из исходников
install:
    uv tool install --editable .
