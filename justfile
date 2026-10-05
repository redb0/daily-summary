default: lint types test

# Проверка линтером и форматированием
lint:
    uv run ruff check .
    uv run ruff format --check .

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
