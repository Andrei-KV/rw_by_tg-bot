
# Базовый образ: Python 3.12
FROM python:3.12-slim

# Отключаем .pyc файлы и буферизацию вывода
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Установка зависимостей для PostgreSQL
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Poetry
RUN pip install poetry sqlite-web
# RUN pip install --no-cache-dir sqlite-web

# Копируем только файлы зависимостей
COPY pyproject.toml poetry.lock* /app/

# Устанавливаем рабочую директорию
# Следующие команды будут отталкиваться от неё
WORKDIR /app

# Устанавливаем зависимости в system site-packages
RUN poetry config virtualenvs.create false \
  && poetry install --no-root

# Установим sqlite3
# RUN apt update && apt install -y sqlite3 && rm -rf /var/lib/apt/lists/*


# Теперь копируем весь остальной код проекта
COPY . /app

# Копируем скрипт запуска
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# RUN adduser ...: Создаёт пользователя ,
# который будет работать внутри контейнера.
# Это рекомендуется для безопасности, чтобы не запускать контейнер от имени root.

# USER appuser: Говорит Docker запускать контейнер от имени этого пользователя.


# Создаём пользователя (без прав root)
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# Порт, на котором слушает Flask
EXPOSE 8080

CMD ["python", "main.py"]
# # Запуск приложения через entrypoint
# ENTRYPOINT ["/app/entrypoint.sh"]
