#!/bin/bash

# Запускаем main.py в фоне
python main.py &

# Запускаем sqlite_web для просмотра базы данных
sqlite_web /app/tracking_train.sqlite3 --host 0.0.0.0 --port 3000
