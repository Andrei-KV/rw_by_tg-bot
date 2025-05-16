# import json
# import logging
# import os
# import queue
# import sqlite3

# import calendar
# # Библиотека для параллельных потоков
# import threading
# import time
# from collections import defaultdict
# from copy import deepcopy
# from datetime import datetime, timedelta
# from logging.handlers import RotatingFileHandler
# from random import randint
# from urllib.parse import quote

# import requests

# # Импорт для бота
# import telebot
# from bs4 import BeautifulSoup

# # Для парсинга страниц
# from bs4.filter import SoupStrainer
# from telebot import apihelper, types

# url = f"https://pass.rw.by/ru/route/?from=Минск&to=Полоцк&date=2025-06-18"
# r = requests.get(url)

# only_span_div_tag = SoupStrainer(["span", "div"])
# soup = BeautifulSoup(
#     r.text, "lxml", parse_only=only_span_div_tag
# )
# print(r.status_code)
# print(type(r.status_code))
# # Проверка времени (прекратить отслеживание за 15 минут до отправления)
# def check_depart_time(train_number, soup):
#     # информация о наличии мест и классов вагонов
#     train_info = soup.select(
#         f'div.sch-table__row[data-train-number^="{train_number}"] \
#             div.sch-table__time.train-from-time'
#     )
#     # Если дата уже прошла, информации не будет. Вызвать 0
#     print(train_info)
#     if not train_info:
#         result = 0
#     # время до отправления в секундах
#     result = int(train_info[0]["data-value"])
#     return result

# check_depart_time('869Б', soup)


a = 0
while True:
    a += 1
    if a == 5:
        continue
    if a == 10:
        break
    print(a)

















    @bot.callback_query_handler(func=lambda call: call.data.startswith(('select_', 'change_')))
def handle_calendar_callback(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if call.data.startswith('select_'):
        # Выбрана дата
        selected_date = call.data[7:]
        bot.delete_message(chat_id, message_id)
        
        # Отменяем все предыдущие обработчики
        bot.clear_step_handler_by_chat_id(chat_id)
        
        process_selected_date(chat_id, selected_date)
        
    elif call.data.startswith('change_'):
        # Смена месяца
        _, year, month = call.data.split('_')
        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=generate_calendar(int(year), int(month)),
        )
    
    bot.answer_callback_query(call.id)