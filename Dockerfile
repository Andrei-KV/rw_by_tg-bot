
# Базовый образ: Python 3.12
FROM python:3.12-slim

# Отключаем .pyc файлы и буферизацию вывода
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Установка зависимостей для PostgreSQL
RUN apt-get update && \
    apt-get install -y --no-install-recommends --reinstall \
    ca-certificates \
    libpq-dev \
    gcc \
    && update-ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*



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


# Теперь копируем весь остальной код проекта
COPY . /app


# RUN adduser ...: Создаёт пользователя ,
# который будет работать внутри контейнера.
# Это рекомендуется для безопасности, чтобы не запускать контейнер от имени root.

# USER appuser: Говорит Docker запускать контейнер от имени этого пользователя.


# Создаём пользователя (без прав root)
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# Порт, на котором слушает Flask
EXPOSE 8080

# Для деплоя Flask-приложение запускается через Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "src.main:app"]
