
# Базовый образ: Python 3.12
FROM python:3.12-slim

# Отключаем .pyc файлы и буферизацию вывода
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем Poetry
RUN pip install poetry

# Копируем только файлы зависимостей
COPY pyproject.toml poetry.lock* /app/

# Устанавливаем рабочую директорию
# Следующие команды будут отталкиваться от неё
WORKDIR /app

# Устанавливаем зависимости в system site-packages
RUN poetry config virtualenvs.create false \
  && poetry install --no-root

# Установим sqlite3
RUN apt update && apt install -y sqlite3 && rm -rf /var/lib/apt/lists/*


# Теперь копируем весь остальной код проекта
COPY . /app

# RUN adduser ...: Создаёт пользователя ,
# который будет работать внутри контейнера.
# Это рекомендуется для безопасности, чтобы не запускать контейнер от имени root.

# USER appuser: Говорит Docker запускать контейнер от имени этого пользователя.


# Создаём пользователя (без прав root)
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py", "sqlite_web", "/app/tracking_train.sqlite3", "--host", "0.0.0.0"]
