import json
import logging
import os
import queue
import sqlite3

# Библиотека для параллельных потоков
import threading
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from random import randint
from urllib.parse import quote

import requests

# Импорт для бота
import telebot
from bs4 import BeautifulSoup

# Для парсинга страниц
from bs4.filter import SoupStrainer
from telebot import apihelper, types

# Список станций
from all_stations_list import all_station_list
from token_info import stop_code, token

q_from = quote("Минск")
q_to = quote("Лида")
date = "2025-05-14"


    # Получение новой страницы "soup"
url = f"https://pass.rw.by/ru/route/?from={q_from}&to={q_to}&date={date}"
r = requests.get(url)

only_span_div_tag = SoupStrainer(["span", "div"])
soup = BeautifulSoup(
    r.text, "lxml", parse_only=only_span_div_tag
)
def check_depart_time(train_number, soup):
    # информация о наличии мест и классов вагонов
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"] \
            div.sch-table__time.train-from-time'
    )
    # время до отправления в секундах
    print(train_info)

    return int(train_info[0]["data-value"])

a = check_depart_time("687Б", soup)
print(a)