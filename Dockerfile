# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    postgresql-client \
    gcc \
    curl \
    nodejs \
    npm \
    build-essential \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install --no-cache-dir poetry

WORKDIR /app

# Copy poetry files first (cache)
COPY pyproject.toml poetry.lock* /app/

# Install app deps into system python (no venv)
RUN poetry config virtualenvs.create false \
 && poetry install --no-root --no-interaction

# Copy code
COPY . /app

# Install localtunnel globally for npx/lt
RUN npm install -g localtunnel

# Entrypoint (wait for DB etc)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "main.py"]
