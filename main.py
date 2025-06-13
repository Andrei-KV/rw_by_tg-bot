import calendar
import json
import logging
import os
import queue

# Библиотека для параллельных потоков
import threading
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from random import randint
from urllib.parse import quote

# import sqlite3
import flask
import psycopg2
import requests

# Импорт для бота
import telebot
from bs4 import BeautifulSoup

# Для парсинга страниц
from bs4.filter import SoupStrainer
from psycopg2 import pool  # Пул соединений
from telebot import apihelper, types

# Список станций
from all_stations_list import all_station_list, all_station_list_lower
from token_info import (
    db_host,
    db_name,
    db_password,
    db_port,
    db_user,
    stop_code,
    token,
    web_port,
    webhook_url,
)


# Класс ошибки для "Дата в прошлом"
class PastDateError(ValueError):
    pass


# Класс ошибки для "Дата в далеко в будущем"
class FutureDateError(ValueError):
    pass


# Класс ошибки для "Ошибка сайта"
class SiteResponseError(Exception):
    pass


# Словарь соответствия номер-название класса
seats_type_dict = {
    "0": "Без нумерации 🚶‍♂️",
    "1": "Общий 🚃",
    "2": "Сидячий 💺",
    "3": "Плацкартный 🛏️",
    "4": "Купейный 🚪🛏️",
    "5": "Мягкий 🛋️",
    "6": "СВ 👑",
}


# В начале кода создаем словарь для временного хранения вводимых данных
user_data = defaultdict(
    lambda: {}
)  # Ключ - chat_id, значение - словарь с данными

# Создание глобальной блокировки для БД и текущего запроса user_data
db_lock = threading.Lock()
user_data_lock = threading.Lock()


# Настройка логирования
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Минимальный уровень логирования

    # Формат сообщений
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Логирование в файл с ротацией
    file_handler = RotatingFileHandler(
        'train_bot.log', maxBytes=10 * 1024 * 1024, backupCount=3  # 10 MB
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Логирование в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


setup_logging()


def get_user_data(chat_id):
    logging.debug(f"Проверка доступа chat_id: {chat_id}")
    with user_data_lock:
        return deepcopy(user_data.get(chat_id, {}))
    # Возвращаем копию, чтобы избежать изменений без блокировки


def update_user_data(chat_id, key, value):
    with user_data_lock:
        if chat_id not in user_data:
            user_data[chat_id] = {}
        user_data[chat_id][key] = value


def set_user_data(chat_id, data_dict):
    with user_data_lock:
        user_data[chat_id] = deepcopy(data_dict)


def del_user_data(chat_id):
    with user_data_lock:
        user_data.pop(chat_id, None)
        logging.debug(f"user_data после удаления {chat_id}: {user_data}")


# -----------------------------------------------------------------------------
# # Создание БД и подключение


# Postgres Создание пула подключений
db_pool = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    dbname=db_name,
    user=db_user,
    password=db_password,
    host=db_host,
    port=db_port,
)


# Универсальная функция для подключений к БД
def execute_db_query(
    query, params=None, fetchone=False, fetchall=False, commit=False
):
    """
    Универсальный метод выполнения SQL-запросов
    с использованием пула соединений PostgreSQL.
    """
    conn = None
    cursor = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute(query, params or ())

        result = None
        if fetchone:
            result = cursor.fetchone()
        elif fetchall:
            result = cursor.fetchall()

        if commit:
            conn.commit()

        return result
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Database error in execute_db_query: {e}")
        raise
    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                db_pool.putconn(conn)
        except Exception as e:
            logging.error(
                f"Ошибка при закрытии соединения в execute_db_query: {e}"
            )
            raise


# Проверка подключения к БД
try:
    result = execute_db_query("SELECT 1", fetchone=True)
    logging.debug(f"Соединение c БД успешно: {result}")
except Exception as e:
    logging.debug(f"Ошибка соединения: {e}")
# ----------------------------------------------------------------------------
# Общая очередь задач для БД
db_queue = queue.Queue()


# Поток-обработчик БД, работает в отдельном потоке
def db_worker():
    while True:
        func, args, result_queue = db_queue.get()  # Получить задачу из очереди
        try:
            result = func(*args)  # Выполнить функцию с аргументами
            result_queue.put(result)  # Отправить результат обратно
        except Exception as e:
            logging.error(f"Ошибка в db_worker: {e}")
            result_queue.put(e)  # Отправить исключение
        db_queue.task_done()


# Запуск потока
threading.Thread(target=db_worker, daemon=True).start()


# Универсальная функция-обёртка добавления функций в очередь
def async_db_call(func, *args, **kwargs):
    # Создать очередь
    result_queue = queue.Queue()
    # Добавить функцию в очередь
    db_queue.put((func, args, result_queue))
    # Вернуть результат или исключение
    result = result_queue.get()
    if isinstance(result, Exception):
        raise result
    return result


# ----------------------------------------------------------------------------
# Функции для обращения к БД
def add_user_db(chat_id):
    try:
        execute_db_query(
            """
            INSERT INTO users (chat_id)
            VALUES (%s)
            ON CONFLICT (chat_id) DO NOTHING
        """,
            (chat_id,),
            commit=True,
        )
        logging.info(f"User {chat_id} added to database")
    except Exception as e:
        logging.error(f"Database error in add_user_db: {str(e)}")
        raise


def add_route_db(city_from, city_to, date, url):
    try:
        # Благодаря URL UNIQUE не будет повторной записи
        execute_db_query(
            """
            INSERT INTO routes (city_from, city_to, date, url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            """,
            (city_from, city_to, date, url),
            commit=True,
        )
        logging.info(f"Route {city_from}-{city_to}-{date} added to database")
    except Exception as e:
        logging.error(f"Database error in add_route_db: {str(e)}")
        raise


def add_train_db(train, time_depart, time_arriv, url):
    try:
        result = execute_db_query(
            "SELECT route_id FROM routes WHERE url = %s",
            (url,),
            fetchone=True,
        )

        if result is None:
            # Условие для избежания случайной ошибки.
            # В нормальном режиме невозможно
            logging.warning(f"No route found for URL: {url}")
            return

        route_id = result[0]
        execute_db_query(
            """
            INSERT INTO trains
            (route_id, train_number, time_depart, time_arriv)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT
            (route_id, train_number, time_depart, time_arriv) DO NOTHING
            """,
            (route_id, train, time_depart, time_arriv),
            commit=True,
        )

        logging.info(
            f"Train: {train} for route_id: {route_id} added to database"
        )
    except Exception as e:
        logging.error(f"Database error in add_train_db: {str(e)}")
        raise


def add_tracking_db(chat_id, train_selected, ticket_dict, url):

    try:
        result = execute_db_query(
            "SELECT route_id FROM routes WHERE url = %s", (url,), fetchone=True
        )
        if result is None:
            logging.warning(f"No route found for URL: {url}")
            return
        route_id = result[0]

        # Получить train_id по route_id и номеру поезда
        result = execute_db_query(
            """
            SELECT train_id FROM trains
            WHERE route_id = %s AND train_number = %s
            """,
            (route_id, train_selected),
            fetchone=True,
        )
        if not result:
            logging.warning(
                f"Train not found for route_id={route_id}, "
                f"number={train_selected}"
            )
            return
        train_id = result[0]

        # Преобразование словаря билетов в JSON
        json_ticket_dict = json.dumps(ticket_dict)

        # Вставка в список слежения
        execute_db_query(
            """
            INSERT INTO tracking (chat_id, train_id, json_ticket_dict)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, train_id) DO NOTHING
            """,
            (chat_id, train_id, json_ticket_dict),
            commit=True,
        )
        logging.info(f"Train_id: {train_id} added to db_tracking_list")
    except Exception as e:
        logging.error(f"Database error in add_tracking_db: {str(e)}")
        raise


# Получить список поездов по заданному маршруту из БД
def get_trains_list_db(url):
    try:
        # Получить route_id по URL
        result = execute_db_query(
            "SELECT route_id FROM routes WHERE url = %s", (url,), fetchone=True
        )
        if not result:
            logging.warning(f"No route found for URL: {url}")
            return []
        route_id = result[0]
        # Получить список поездов по route_id
        trains_list = execute_db_query(
            """
            SELECT train_number, time_depart, time_arriv FROM trains
            WHERE route_id = %s
            ORDER BY time_depart
            """,
            (route_id,),
            fetchall=True,
        )
    except Exception as e:
        logging.error(f"Database error in get_trains_list_db: {str(e)}")
        raise
    return trains_list


# Получить данные для цикла отслеживания поезда
def get_loop_data_list(chat_id, train_tracking, url):
    try:
        query = """
            SELECT r.route_id, t.train_id,
            EXISTS (
                SELECT 1 FROM tracking tr
                WHERE tr.train_id = t.train_id AND tr.chat_id = %s
            ) AS is_tracked
            FROM routes r
            JOIN trains t ON r.route_id = t.route_id
            WHERE r.url = %s AND t.train_number = %s
            LIMIT 1
        """
        resp = execute_db_query(
            query, (chat_id, url, train_tracking), fetchone=True
        )
        if not resp:
            logging.warning(
                f"No matching train or route found for chat_id: "
                f"{chat_id}, url: {url}, train: {train_tracking}"
            )
            return None
        # Отдельный запрос: сколько активных отслеживаний у пользователя
        count_result = execute_db_query(
            """
            SELECT COUNT(*) FROM tracking WHERE chat_id = %s
            """,
            (chat_id,),
            fetchone=True,
        )
        count = count_result[0] if count_result else 0
        result = {
            "route_id": resp[0],
            "train_id": resp[1],
            "status_exist": resp[2],
            "count": count,
        }
        return result
    except Exception as e:
        logging.error(f"Database error in get_loop_data_list: {str(e)}")
        raise


# Получение свежих данных из таблицы при отслеживании
def get_fresh_loop(
    chat_id,
    train_id,
):
    try:
        result = execute_db_query(
            """
            SELECT json_ticket_dict FROM tracking
            WHERE chat_id = %s AND train_id = %s
            """,
            (chat_id, train_id),
            fetchone=True,
        )
        if result:
            json_str = result[0]  # Распаковываем кортеж
            memory_ticket_dict = json.loads(json_str)  # Декодируем JSON строку
            logging.debug(
                f"FG2 memory_ticket_dict, chat_id, train_id:\n"
                f" {memory_ticket_dict, chat_id, train_id}"
            )
        else:
            # Обработка случая, когда запись не найдена
            memory_ticket_dict = {}
        return memory_ticket_dict
    except Exception as e:
        logging.error(f"Database error in get_fresh_loop: {str(e)}")
        raise


# Получение списка отслеживаемых поездов, т.к. используется
# для команд Отображения и Останова
def get_track_list(message):

    chat_id = message.chat.id

    try:
        track_list = execute_db_query(
            """
            SELECT tracking_id, t.train_number,
                   r.city_from, r.city_to, r.date, t.time_depart
            FROM tracking tr
            JOIN trains t ON tr.train_id = t.train_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE tr.chat_id = %s
            """,
            (chat_id,),
            fetchall=True,
        )
        return track_list

    except Exception as e:
        logging.error(f"Database error in get_track_list: {str(e)}")
        raise


# Удаление маршрута из списка отслеживания
def del_tracking_db(
    chat_id,
    train_id,
):
    try:
        execute_db_query(
            """
            DELETE FROM tracking
            WHERE chat_id = %s AND train_id = %s
            """,
            (chat_id, train_id),
            commit=True,
        )

    except Exception as e:
        logging.error(f"Database error in del_tracking_db: {str(e)}")
        raise


# Обновление таблицы отслеживания в цикле
def update_tracking_loop(
    json_ticket_dict,
    chat_id,
    train_id,
):
    try:
        execute_db_query(
            """
            UPDATE tracking
            SET json_ticket_dict = %s
            WHERE chat_id = %s AND train_id = %s
            """,
            (json_ticket_dict, chat_id, train_id),
            commit=True,
        )

    except Exception as e:
        logging.error(f"Database error in update_tracking_loop: {str(e)}")
        raise


# Проверка пользователя в БД
def check_user_exists(chat_id):
    try:
        result = execute_db_query(
            """
            SELECT EXISTS(SELECT 1 FROM users WHERE chat_id = %s)
            """,
            (chat_id,),
            fetchone=True,
        )
        return bool(result[0]) if result else False
    except Exception as e:
        logging.error(f"Database error in check_user_exists: {str(e)}")
        raise


# ----------------------------------------------------------------------------
# Декоратор: Проверка "start" для избежания ошибок
def ensure_start(func):
    def wrapper(message):
        try:
            chat_id = message.chat.id
        except AttributeError:
            chat_id = message.message.chat.id  # если проверяется callback
        # Проверка наличия пользователя в БД
        if not async_db_call(check_user_exists, chat_id):
            bot.send_message(chat_id, "Сначала введите /start")
            return
        return func(message)

    return wrapper


# Декоратор: перехват команд в ответах пользователя
def with_command_intercept(func):
    def wrapper(message):
        text = message.text or ""
        chat_id = message.chat.id
        if text.startswith("/add_train_last_route"):
            bot.clear_step_handler_by_chat_id(chat_id)
            show_train_list(message)
            return
        if text.startswith("/add_train_new_route"):
            bot.clear_step_handler_by_chat_id(chat_id)
            start(message)
            return
        if text.startswith("/stop_track_train"):
            bot.clear_step_handler_by_chat_id(chat_id)
            stop_track_train(message)
            return
        if text.startswith("/show_track_list"):
            bot.clear_step_handler_by_chat_id(chat_id)
            show_track_list(message)
            return
        if text.startswith("/stop"):
            bot.clear_step_handler_by_chat_id(chat_id)
            stop(message)
            return
        if text.startswith("/start"):
            bot.clear_step_handler_by_chat_id(chat_id)
            start(message)
            return
        if text.startswith(f"/{stop_code}"):
            bot.clear_step_handler_by_chat_id(chat_id)
            exit_admin(message)
            return
        # Добавить другие команды по мере надобности
        return func(message)

    return wrapper


# =============================================================================
# Подключение бота для ввода данных


# Создаётся объект бота, который умеет принимать сообщения от Telegram.
app = flask.Flask(__name__)
bot = telebot.TeleBot(token, threaded=True)  # type: ignore


# Обработка запроса от Telegram (webhook endpoint)
"""
Этот маршрут слушает POST-запросы по пути /<токен>
Telegram будет присылать сюда новые сообщения пользователей,
если правильно установить webhook (bot.set_webhook(...)).
URL /TOKEN — это защита от чужих запросов.

"""


@app.route(f'/{token}', methods=['POST'])
def webhook():
    try:
        json_str = flask.request.data.decode("utf-8")
        # превращает JSON-строку в объект telebot.types.Update:
        update = telebot.types.Update.de_json(json_str)
        logging.debug("FLAG Webhook получен!")
        if update is not None:
            # метод, который имитирует поведение polling, но вручную:
            bot.process_new_updates([update])  # только если не None
    except Exception as e:
        logging.warning(f"Ошибка обработки webhook: {e}")
    return "ok", 200  # Telegram требует подтверждение


"""
Это просто проверка, что сервер запущен.
Если перейти в браузере по https://your-domain.com/, то:
"Bot is alive" — значит Flask-приложение работает.
"""


@app.route("/", methods=["GET"])
def index():
    return "Bot is alive", 200


# ==============================================


# Запуск чата. Запрос города отправления
# (функции до старта отслеживания используют словарь user_data
# с текущей сессией ввода данных от пользователя)
@bot.message_handler(commands=["start"])
def start(message):
    try:
        chat_id = message.chat.id
        try:
            f_name = message.chat.get('first_name', None)
            l_name = message.chat.get('last_name', None)
        except AttributeError:
            f_name = 'continue'
            l_name = 'continue'
        logging.info(f"User {f_name} {l_name} {chat_id} started the bot")

        # Для отображения активности
        bot.send_chat_action(chat_id, 'typing')  # Show typing indicator
        time.sleep(1)  # Optional delay

        bot.send_message(chat_id, "Станция отправления: ")
        # Регистрация следующей функции для города отправления
        # "Вызвать next_step_handler после ответа пользователя"
        bot.register_next_step_handler(message, get_city_from)
        set_user_data(chat_id, {"step": "start"})

        # Добавить пользователя в БД
        async_db_call(add_user_db, chat_id)
    except Exception as e:
        logging.error(f"Error in start command: {str(e)}", exc_info=True)
        raise


# Чтение города отправления. Проверка наличия в списке станций
@with_command_intercept
def get_city_from(message):
    # if message.text.startswith('/stop'):
    #     # Останов бота
    #     bot.register_next_step_handler(message, stop)
    #     return
    chat_id = message.chat.id
    city_from = normalize_city_name(message.text)
    if city_from not in all_station_list:
        # Попытка найти варианты
        examples = '\n'.join(
            [x for x in all_station_list if x.startswith(city_from[:3])]
        )
        answer = (
            "✏️ Ошибка в названии.\n"
            + "Повторите ввод\n"
            + int(bool(examples)) * f"Варианты:\n {examples}"
        )

        bot.send_message(chat_id, answer)
        # Возврат при ошибке ввода
        bot.register_next_step_handler(message, get_city_from)
        return
    update_user_data(chat_id, "city_from", city_from)
    bot.send_message(chat_id, "Станция прибытия: ")
    # Регистрация следующей функции для города прибытия
    bot.register_next_step_handler(message, get_city_to)


# Чтение города прибытия. Проверка наличия в списке станций
@with_command_intercept
def get_city_to(message):
    # if message.text.startswith('/stop'):
    #     # Останов бота
    #     bot.register_next_step_handler(message, stop)
    #     return
    chat_id = message.chat.id
    city_to = normalize_city_name(message.text)
    if city_to not in all_station_list:
        # Попытка найти варианты
        examples = '\n'.join(
            [x for x in all_station_list if x.startswith(city_to[:3])]
        )
        answer = (
            "✏️ Ошибка в названии.\n"
            + "Повторите ввод\n"
            + int(bool(examples)) * f"Варианты:\n {examples}"
        )

        bot.send_message(chat_id, answer)
        # Возврат при ошибке ввода
        bot.register_next_step_handler(message, get_city_to)
        return
    update_user_data(chat_id, "city_to", city_to)

    # Отправляем календарь сразу
    # Отправляем календарь сразу
    msg = bot.send_message(
        chat_id,
        "📅 Выберите дату:",
        reply_markup=generate_calendar(),
    )
    # Регистрируем обработчик для ручного ввода
    bot.register_next_step_handler(msg, get_date)


# Обработка ответов по выбору даты


# Чтение даты отправления
@with_command_intercept
def get_date(message):
    chat_id = message.chat.id
    try:
        date = normalize_date(message.text)
        update_user_data(chat_id, "date", date)
        get_trains_list(message)
        return
    except (PastDateError, FutureDateError, ValueError) as e:
        logging.info(f"FLAG get_date   {e}")
        bot.send_message(chat_id, f"✏️ {e}.\nПовторите ввод даты")
        # Возврат при ошибке ввода
        bot.register_next_step_handler(message, get_date)
        return
    except Exception as e:
        logging.warning(f"Ошибка выполнения get_trains_list(): {e}")
        bot.send_message(chat_id, "❌ Ошибка сервера.\nПоробуйте позже")
        # Останов бота
        stop(message)
        return


# Функция получения поездов по маршруту
def get_trains_list(message):
    # Для отображения активности
    bot.send_chat_action(message.chat.id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay
    bot.send_message(message.chat.id, "Идёт поиск 🔍")  # Send your custom text
    chat_id = message.chat.id

    user_info = get_user_data(chat_id)
    if not user_info:
        logging.error(f"No user data for chat_id {chat_id}")
        raise ValueError("User data not found")

    try:
        q_from = quote(user_info["city_from"])
        q_to = quote(user_info["city_to"])
        date = user_info["date"]
    except KeyError as e:
        logging.error(f"Missing key in user data: {e}")
        raise ValueError(f"Incomplete user data: missing {e}")

    # Получение новой страницы "soup"
    url = f"https://pass.rw.by/ru/route/?from={q_from}&to={q_to}&date={date}"
    update_user_data(chat_id, "url", url)
    try:
        r = requests.get(url)
        logging.info(f"FLAG get_trains_list   {r.status_code}")
        if r.status_code != 200:
            error_msg = (
                f"Fail response in get_trains_list. Code {r.status_code}"
            )
            logging.debug(f"{error_msg}, for user {chat_id}")
            raise SiteResponseError(
                f"Ошибка ответа сайта. Код {r.status_code}"
            )

        only_span_div_tag = SoupStrainer(["span", "div"])
        soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)

        response_time = r.elapsed.total_seconds()  # время в секундах
        logging.info(
            f"Запрос на сайт \n{user_data[chat_id]}"
            f"выполнен за {response_time:.3f} секунд"
        )

    except Exception as e:
        logging.error(f"Server request error: {e}")
        bot.send_message(
            chat_id, "⚠️ Ошибка запроса на сервер.\nПовторите ввод маршрута"
        )
        # Возвращаемся к началу
        start(message)
        return
    # Добавляет маршрут в БД
    async_db_call(
        add_route_db,
        user_info["city_from"],
        user_info["city_to"],
        date,
        url,
    )

    # Обновление страницы
    update_user_data(chat_id, "soup", soup)

    train_id_list = [
        i.text for i in soup.find_all("span", class_="train-number")
    ]

    if not train_id_list:
        bot.send_message(
            chat_id,
            "❓🚆Поезда не найдены.\
                \nПовторите ввод маршрута",
        )
        start(message)
        return

    trains_list = []
    # Получение времени отправления и прибытия
    for train in train_id_list:
        try:
            time_depart = soup.select(
                f'[data-train-number^="{train}"] \
                                    [data-sort="departure"]'
            )[0].text.strip()
            time_arriv = soup.select(
                f'[data-train-number^="{train}"] \
                                    [data-sort="arrival"]'
            )[0].text.strip()
        except Exception:
            time_depart, time_arriv = (
                "Нет данных",
                "Нет данных",
            )
        trains_list.append([train, time_depart, time_arriv])
        # Добавить поезда в БД
        async_db_call(add_train_db, train, time_depart, time_arriv, url)
        # Отобразить список поездов
    show_train_list(message)


@ensure_start
def show_train_list(message):
    chat_id = message.chat.id
    try:
        url = user_data[chat_id]["url"]
    except KeyError:
        bot.send_message(
            chat_id,
            "❓Утерян последний маршрут.\
                \nПовторите ввод маршрута",
        )
        start(message)
        return

    trains_list = async_db_call(get_trains_list_db, url)
    markup = types.InlineKeyboardMarkup()
    # Отображение кнопок выбора поезда из доступного списка

    for train in trains_list:
        markup.row(
            types.InlineKeyboardButton(
                f"🚆 Поезд №{train[0]} 🕒 {train[1]} ➡️ {train[2]}",
                callback_data=f"{train[0]}_selected",
            )
        )

    bot.send_message(
        chat_id,
        "Список доступных поездов: ",
        reply_markup=markup,
    )


# Выбор конкретного поезда из списка, отображение наличия мест
@bot.callback_query_handler(
    func=lambda callback: callback.data.endswith("_selected")
)
@ensure_start
def select_train(callback):
    # Мнимый ответ в телеграм для исключения ошибки скорости
    bot.answer_callback_query(callback.id)

    train_selected = callback.data.split("_")[0]
    chat_id = callback.message.chat.id
    # Получаем из сессии здесь, т.к. дальше не передаётся объект message
    soup = user_data[chat_id]["soup"]
    # Вывод количества мест по классам или "Мест нет"
    ticket_dict = check_tickets_by_class(train_selected, soup, chat_id)

    # Добавляем в список поездов, но здесь статус отслеживания пока что False
    # Здесь, т.к. необходимо получить список мест для контроля изменений
    # Добавить поезд в список отслеживания

    # Кнопка включения слежения за поездом
    markup = types.InlineKeyboardMarkup()

    # Если 'Без нумерованных мест' возврат на выбор поезда
    if seats_type_dict["0"] in ticket_dict:
        btn_track = types.InlineKeyboardButton(
            "🔄 Назад к поездам",
            callback_data="re_get_trains_list",
        )
        markup.add(btn_track)

        bot.send_message(
            chat_id=callback.message.chat.id,
            text=f"🚆 Поезд №{train_selected}\n🔕 Отслеживания нет",
            reply_markup=markup,
        )
    # Проверка времени отправления
    elif check_depart_time(train_selected, soup, train_id=None) <= 0:
        btn_track = types.InlineKeyboardButton(
            "🔄 Назад к поездам",
            callback_data="re_get_trains_list",
        )
        markup.add(btn_track)

        bot.send_message(
            chat_id=callback.message.chat.id,
            text=f"🚆 Поезд №{train_selected}\n⏰ Уже отправился",
            reply_markup=markup,
        )
    else:
        btn_track = types.InlineKeyboardButton(
            "🔍 Отслеживать",
            callback_data=f"{train_selected}_start_tracking",
        )
        markup.add(btn_track)

        bot.send_message(
            chat_id=callback.message.chat.id,
            text=f"🚆 Поезд №{train_selected}\n{ticket_dict}",
            reply_markup=markup,
        )


# Обработка возврата к списку поездов, если поезд без нумерации мест
@bot.callback_query_handler(
    func=lambda callback: callback.data == "re_get_trains_list"
)
@ensure_start
def re_get_trains_list(callback):
    bot.answer_callback_query(callback.id)  # Для имитации ответа в Телеграм
    show_train_list(callback.message)
    pass


# Добавить поезд в список отслеживания
@bot.message_handler(commands=["add_train_last_route", "add_train_new_route"])
@ensure_start
def add_track_train(message):
    if message.text == "/add_train_new_route":
        start(message)
        pass
    elif message.text == "/add_train_last_route":
        show_train_list(message)
        pass


# Включение отслеживания, добавление поезда в лист слежения
# Включение отслеживания, добавление поезда в лист слежения


def tracking_loop(chat_id, train_tracking, train_id, route_id, url):
    logging.debug(f"Tracking train {train_tracking} for user {chat_id}")

    # Счётчик ошибок
    error_streak = 0
    max_errors = 10
    # # Максимально:  10 ошибок
    # # Ночное время
    # start_night = datetime(year=1, month=1, day=1, hour=1).time()
    # end_night = datetime(year=1, month=1, day=1, hour=7).time()
    while True:
        try:
            # Время обращения к БД от пользователя для debug
            start_time_db_loop = time.time()

            try:
                # Запоминание данных о билете
                memory_ticket_dict = async_db_call(
                    get_fresh_loop, chat_id, train_id
                )

                if not memory_ticket_dict:
                    logging.info(
                        f"Stopping tracking for train"
                        f"{train_tracking}, user {chat_id}"
                    )
                    return

                session = requests.Session()
                session.headers.update(
                    {
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"
                        + " AppleWebKit/537.36 (KHTML, like Gecko)"
                        + " Chrome/133.0.0.0 Safari/537.36",
                        "Accept": "*/*",
                        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,"
                        + "ru;q=0.7,it;q=0.6",
                        "Accept-Encoding": "gzip, deflate, br, zstd",
                        "Referer": f"{url}",
                        "X-Requested-With": "XMLHttpRequest",
                    }
                )
                r = session.get(url)
                if r.status_code != 200:
                    logging.warning(
                        f"Fail response. "
                        f"Code {r.status_code}, train {train_tracking} "
                        f"for user {chat_id}"
                    )
                    raise SiteResponseError(
                        f"Ошибка ответа сайта. Код {r.status_code}"
                    )

                only_span_div_tag = SoupStrainer(["span", "div"])
                soup = BeautifulSoup(
                    r.text, "lxml", parse_only=only_span_div_tag
                )

                # Проверка времени
                # (прекратить отслеживание за 15 мин до отправления)
                if check_depart_time(train_tracking, soup, train_id) < 1000:

                    # Удалить маршрут из списка отслеживания
                    async_db_call(
                        del_tracking_db,
                        chat_id,
                        train_id,
                    )
                    bot.send_message(
                        chat_id,
                        f"Отслеживание завершёно по расписанию"
                        f" отправления поезда {train_tracking}",
                    )
                    logging.info(
                        f"[thread exit] Поток завершён за 15 мин/"
                        f"до отпр.: {train_tracking} для {chat_id}"
                    )
                    return

                    # Получение более свежей информации по билетам
                ticket_dict = check_tickets_by_class(
                    train_tracking, soup, chat_id
                )

                # Выводить сообщение при появлении изменений в билетах
                #  + быстрая ссылка
                logging.debug(f"FLAG3  ticket_dict  {ticket_dict}")
                if ticket_dict != memory_ticket_dict:
                    markup_url = types.InlineKeyboardMarkup()  # объект кнопки
                    url_to_ticket = types.InlineKeyboardButton(
                        "На сайт", url=url
                    )
                    markup_url.row(url_to_ticket)
                    bot.send_message(
                        chat_id,
                        f"Обновление по {train_tracking}:\n" f"{ticket_dict}",
                        reply_markup=markup_url,
                    )

                    json_ticket_dict = json.dumps(ticket_dict)

                    # Обновление таблицы отслеживания в цикле
                    async_db_call(
                        update_tracking_loop,
                        json_ticket_dict,
                        chat_id,
                        train_id,
                    )
                end_time_db_loop = time.time()
                db_loop_time = end_time_db_loop - start_time_db_loop
                logging.debug(
                    f"Время к БД для {chat_id} в цикле loop \n\
                        {db_loop_time:.4f} сек"
                )

            except psycopg2.Error as e:
                error_msg = f"Database error in tracking loop: {str(e)}"
                logging.warning(f"{error_msg}, chat_id: {chat_id}")
                raise
                # При отсутствии нужной записи в БД
            except TypeError as e:
                error_msg = f"Database error in tracking loop: {str(e)}"
                logging.warning(f"{error_msg}, chat_id: {chat_id}")
                raise
            except SiteResponseError as e:
                error_msg = f"Site error in tracking loop: {str(e)}"
                r_c = r.status_code
                logging.warning(f"{error_msg},resp:{r_c}, chat_id: {chat_id}")
                # Запомнить html при ошибке бота
                logging.warning(f"Ответ при ошибке {r.text}")
                raise
            except requests.exceptions.SSLError as e:
                error_msg = f"SSL ошибка для поезда {train_tracking}: {str(e)}"
                logging.warning(f"{error_msg}, chat_id: {chat_id}")
                raise
            except requests.exceptions.RequestException as e:
                error_msg = f"Database error in tracking loop: {str(e)}"
                logging.warning(f"{error_msg}, chat_id: {chat_id}")
                raise

        except Exception as e:
            logging.warning(
                f"Tracking loop crashed for train {train_tracking}, \
                    error_streak = {error_streak} user {chat_id}: {str(e)}",
                exc_info=True,
            )
            # # Если исключение ночью - больше задержка
            # current_time = datetime.now().time()
            # if start_night < current_time < end_night:
            #     max_errors = 20
            # else:
            #     max_errors = 5

            if error_streak >= max_errors:
                # Удалить маршрут из списка отслеживания
                async_db_call(
                    del_tracking_db,
                    chat_id,
                    train_id,
                )
                error_msg = (
                    f"❗ Ошибка бота\n"
                    f"Отслеживание поезда {train_tracking} остановлено"
                )
                bot.send_message(chat_id, error_msg)
                return

            error_streak += 1
            time.sleep(error_streak * 600)
            continue

        error_streak = 0
        time.sleep(randint(600, 800))


@bot.callback_query_handler(
    func=lambda callback: callback.data.endswith("_start_tracking")
)
@ensure_start
def start_tracking_train(callback):

    bot.answer_callback_query(callback.id)  # Для имитации ответа в Телеграм

    train_tracking = callback.data.split("_")[0]
    chat_id = callback.message.chat.id

    # Для отображения активности
    bot.send_chat_action(chat_id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay

    url = user_data[chat_id]['url']

    # Изменение статуса в БД
    try:
        # Повторное получение инф-ции по билетам для внесения в таблицу отслеж.
        r = requests.get(url)
        if r.status_code != 200:
            error_msg = (
                f"Fail response in start_tracking_train. Code {r.status_code}"
            )
            logging.error(f"{error_msg}, for user {chat_id}")
            raise SiteResponseError(
                f"Ошибка ответа сайта. Код {r.status_code}"
            )

        only_span_div_tag = SoupStrainer(["span", "div"])
        soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)
        ticket_dict = check_tickets_by_class(train_tracking, soup, chat_id)

        loop_data_list = async_db_call(
            get_loop_data_list, chat_id, train_tracking, url
        )

        route_id = loop_data_list["route_id"]
        train_id = loop_data_list["train_id"]
        status_exist = loop_data_list["status_exist"]
        count = loop_data_list["count"]

        # Проверка отслеживания поезда, чтобы не запустить излишний поток
        if status_exist:
            bot.send_message(
                chat_id,
                f"Отслеживание поезда {train_tracking} уже запущено.",
            )
            return

        # Проверка ограничения не более 5 отслеживаний для одного чата
        if count >= 5:
            bot.send_message(chat_id, "Превышено число отслеживаний\n(max 5)")
            return

        # Вставка в список слежения
        async_db_call(
            add_tracking_db,
            chat_id,
            train_tracking,
            ticket_dict,
            url,
        )

    except psycopg2.Error as e:
        logging.error(f"Database error in start_tracking_train: {str(e)}")
        raise

    except Exception as e:
        logging.error(f"Server request error: {e}")
        bot.send_message(
            chat_id, "⚠️ Ошибка запроса на сервер.\nПовторите ввод маршрута"
        )
        # Возвращаемся к началу
        start(callback.message)
        return

    # Запуск отслеживания в параллельном потоке
    # Лучше передавать аргументы, а не использовать внешние
    # Регистрация и запуск параллельного потока с заданным именем
    # и аргументами, чтобы не быть в ситуации, когда
    # функция запустится через секунду-другую,
    # а к этому времени переменные уже будут другими.
    # Например, другой пользователь вызовет бота, и chat_id перезапишется,
    # а старый поток будет отслеживать не того юзера.
    thread = threading.Thread(
        target=tracking_loop,
        args=(chat_id, train_tracking, train_id, route_id, url),
        name=f"tracking_{train_tracking}_{chat_id}",
    )

    thread.start()
    bot.send_message(
        chat_id, f"Отслеживание поезда {train_tracking} запущено."
    )


# =============================================================================


# Отображение списка отслеживаемых поездов
@bot.message_handler(commands=["show_track_list"])
@ensure_start
def show_track_list(message):

    # Для отображения активности
    bot.send_chat_action(message.chat.id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay

    reply = "Список отслеживания пуст"  # по умолчанию
    track_list = async_db_call(get_track_list, message)
    # Список кортежей
    # 0-tracking_id -> int(),
    # 1-t.train_number -> str(),
    # 2-r.city_from,
    # 3-r.city_to,
    # 4-r.date -> str(),
    # 5-t.time_depart -> str()
    if track_list:

        reply_edit = []
        for x in track_list:
            date_obj = datetime.strptime(x[4], "%Y-%m-%d")
            f_date = date_obj.strftime("%d.%m.%y")
            reply_edit.append(
                f"🚆 {x[1]} {x[2]}➡️{x[3]}\n🕒 {x[5]} {f_date} \n{'-'*5}"
            )
        reply = "\n".join(reply_edit)
    bot.reply_to(message, f"{reply}")


# Останов отслеживания конкретного поезда
@bot.message_handler(commands=["stop_track_train"])
@ensure_start
def stop_track_train(message):
    track_list = async_db_call(get_track_list, message)
    # Список кортежей
    # 0-tracking_id -> int(),
    # 1-t.train_number -> str(),
    # 2-r.city_from,
    # 3-r.city_to,
    # 4-r.date -> str(),
    # 5-t.time_depart -> str()

    # Для отображения активности
    bot.send_chat_action(message.chat.id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay

    if track_list:
        markup = types.InlineKeyboardMarkup()
        for x in track_list:
            date_obj = datetime.strptime(x[4], "%Y-%m-%d")
            f_date = date_obj.strftime("%d.%m.%y")
            # Для отображения в сообщении
            reply = f"🚫 {x[1]} {x[2]}➡️{x[3]} 🕒 {x[5]} {f_date}"
            markup.row(
                types.InlineKeyboardButton(
                    f"{reply}",
                    callback_data=f"{x[0]}:{x[1]}:{x[4]}_stop_tracking",
                )
            )
        bot.reply_to(message, "Выбрать удаляемый поезд: ", reply_markup=markup)
    else:
        bot.reply_to(message, "Список отслеживания пуст")


# Обработка запроса на удаление поезда из списка отслеживания
@bot.callback_query_handler(
    func=lambda callback: callback.data.endswith("_stop_tracking")
)
@ensure_start
def stop_tracking_train_by_number(callback):
    bot.answer_callback_query(callback.id)
    # tracking_id, t.train_number, r.date
    train_stop_tracking = callback.data.split("_")[0].split(':')
    chat_id = callback.message.chat.id
    tracking_id = train_stop_tracking[0]
    train_number = train_stop_tracking[1]
    date = train_stop_tracking[2]

    try:
        async_db_call(
            _stop_tracking_logic,
            tracking_id,
        )
    except Exception:
        raise
    logging.info(
        f"Train_number: {train_number} date: {date}"
        f" stop tracking for chat_id: {chat_id},"
        f" tracking_id: {tracking_id}."
    )
    bot.send_message(
        chat_id, f"Отслеживание поезда {train_number}/{date} остановлено."
    )


# Для работы через очередь
def _stop_tracking_logic(
    tracking_id,
):
    try:
        execute_db_query(
            """
            DELETE FROM tracking WHERE tracking_id = %s;
            """,
            (tracking_id,),
            commit=True,
        )

    except Exception as e:
        logging.error(
            f"Database error in stop_tracking_train_by_number: {str(e)}"
        )
        raise


# ============================================================================
# Вспомогательные функции


# Функции для запуска потоков отслеживания при перезапуске приложения
def get_all_active_trackings():
    rows = execute_db_query(
        """
            SELECT
                t.chat_id,
                tr.train_number,
                t.train_id,
                tr.route_id,
                r.url
            FROM tracking t
            JOIN trains tr ON t.train_id = tr.train_id
            JOIN routes r ON tr.route_id = r.route_id

        """,
        fetchall=True,
        commit=True,
    )
    return rows


def restore_all_trackings():
    try:
        rows = async_db_call(get_all_active_trackings)
        if not rows:
            logging.info("Нет активных отслеживаний для восстановления.")
            return
        for row in rows:
            chat_id = row[0]
            train_tracking = row[1]
            train_id = row[2]
            route_id = row[3]
            url = row[4]

            thread = threading.Thread(
                target=tracking_loop,
                args=(chat_id, train_tracking, train_id, route_id, url),
                name=f"tracking_{train_tracking}_{chat_id}",
                daemon=True,
            )
            thread.start()
            logging.info(
                f"Восстановлено отслеживание: {train_tracking} для {chat_id}"
            )

    except Exception as e:
        logging.error(
            f"Ошибка восстановления отслеживания: {str(e)}", exc_info=True
        )


# Нормализация ввода города
def normalize_city_name(name):
    name = name.strip().lower()
    try:
        index = all_station_list_lower.index(name)
        name = all_station_list[index]
    except Exception:
        name = name.capitalize()
    return name


# -------------------------
# Функции для отображения календаря
def is_date_active(year, month, day):
    """Проверяет, активна ли дата (в пределах 59 дней от текущей)"""
    today = datetime.now().date()
    selected_date = datetime(year, month, day).date()
    max_date = today + timedelta(days=59)
    return today <= selected_date <= max_date


def generate_calendar(year=None, month=None):
    """Генерация inline-календаря с возможностью переключения месяцев"""
    now = datetime.now()
    today = now.date()
    max_date = today + timedelta(days=59)

    # Устанавливаем текущий месяц, если не указан
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    # Корректируем, если выбран прошедший месяц
    if year < now.year or (year == now.year and month < now.month):
        year, month = now.year, now.month

    markup = types.InlineKeyboardMarkup()

    # Заголовок (месяц и год)
    month_name = calendar.month_name[month]
    markup.row(
        types.InlineKeyboardButton(
            f"{month_name} {year}", callback_data="ignore"
        )
    )

    # Дни недели
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    markup.row(
        *[
            types.InlineKeyboardButton(day, callback_data="ignore")
            for day in week_days
        ]
    )

    # Ячейки календаря
    month_cal = calendar.monthcalendar(year, month)
    for week in month_cal:
        row = []
        for day in week:
            if day == 0:
                row.append(
                    types.InlineKeyboardButton(" ", callback_data="ignore")
                )
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                date_obj = datetime(year, month, day).date()
                if is_date_active(year, month, day):
                    # Активная дата
                    emoji = "🔹" if date_obj == today else ""
                    row.append(
                        types.InlineKeyboardButton(
                            f"{emoji}{day}", callback_data=f"select_{date_str}"
                        )
                    )
                else:
                    # Неактивная дата
                    row.append(
                        types.InlineKeyboardButton(
                            f"*{day}*", callback_data="ignore"
                        )
                    )
        markup.row(*row)

    # Кнопки навигации
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    nav_buttons = []

    # Кнопка "Назад" (показываем всегда, кроме самого раннего месяца)
    if not (year == now.year and month == now.month):
        nav_buttons.append(
            types.InlineKeyboardButton(
                "◀️", callback_data=f"change_{prev_year}_{prev_month}"
            )
        )

    # Кнопка "Сегодня"
    today_str = f"{now.year}-{now.month:02d}-{now.day:02d}"
    nav_buttons.append(
        types.InlineKeyboardButton(
            "Сегодня", callback_data=f"select_{today_str}"
        )
    )

    # Кнопка "Вперед" если есть будущие месяцы в пределах 59 дней
    if (next_year < max_date.year) or (
        next_year == max_date.year and next_month <= max_date.month
    ):
        nav_buttons.append(
            types.InlineKeyboardButton(
                "▶️", callback_data=f"change_{next_year}_{next_month}"
            )
        )

    if nav_buttons:
        markup.row(*nav_buttons)

    return markup


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(('select_', 'change_'))
)
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
        # Для отображения активности
        bot.send_chat_action(chat_id, 'typing')  # Show typing indicator
        # Смена месяца
        _, year, month = call.data.split('_')
        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=generate_calendar(int(year), int(month)),
        )

    bot.answer_callback_query(call.id)


def process_selected_date(chat_id, date_str):
    """Обработка выбранной даты"""
    try:
        # Создаем объект message для совместимости функцией get_date
        class Message:
            def __init__(self, chat_id, text):
                self.chat = type('Chat', (), {'id': chat_id})
                self.text = text
                self.message_id = None

        message = Message(chat_id, date_str)
        get_date(message)
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка обработки даты: {str(e)}")


# Нормализация ввода даты с контролем "сегодня и далее"
def normalize_date(date_str):

    formats = [
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d %m %Y",
        "%Y %m %d",
    ]
    today = datetime.today().date()
    if date_str == "Сегодня":
        return datetime.today().date()
    elif date_str == "Завтра":
        return datetime.today().date() + timedelta(days=1)
    if not date_str or not isinstance(date_str, str):
        raise ValueError(
            f"Неверный формат.\n\
Примеры: {today.strftime('%Y-%m-%d')}, \
{today.strftime('%d %m %Y')}, \
{today.strftime('%Y %m %d')}"
        )

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            # Дата в прошлом
            if dt.date() < today:
                raise PastDateError("Дата в прошлом")
            # До отправления более 59 суток
            if (dt.date() - today).days > 59:
                raise FutureDateError("Отслеживание доступно за 60 суток")
            # Возвращаем нормализованный формат
            return dt.strftime("%Y-%m-%d")
        # Для отлавливания "Дата в прошлом"
        except PastDateError as e:
            # Вывод ошибки в функцию ввода даты
            raise e

        except FutureDateError as e:
            # Вывод ошибки в функцию ввода даты
            raise e

        except ValueError:
            continue

    # Если ни один формат не подошёл:
    raise ValueError(
        f"Неверный формат.\n\
Примеры: {today.strftime('%Y-%m-%d')}, \
{today.strftime('%d %m %Y')}, \
{today.strftime('%Y %m %d')}"
    )


# Проверка наличия места
def check_tickets_by_class(train_number, soup, chat_id):
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"]'
    )
    try:
        selling_allowed = train_info[0]["data-ticket_selling_allowed"]
    except IndexError:
        selling_allowed = "none"

    if selling_allowed == "true":
        return get_tickets_by_class(train_number, soup)
    elif selling_allowed == "false":
        return "Мест нет либо закрыта продажа"
    else:
        return "Ошибка получения информации о поезде"


# Получение количества мест
def get_tickets_by_class(train_number, soup):
    # информация о наличии мест и классов вагонов
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"]'
    )
    # доступные классы вагонов и места
    class_names = train_info[0].find_all(
        class_="sch-table__t-quant js-train-modal dash"
    )
    # вывод словаря с заменой номера на имя класса обслуживания
    # и общего количества мест для каждого класса
    tickets_by_class = {}
    for class_n in class_names:
        name = seats_type_dict[class_n["data-car-type"]]  # type: ignore
        try:
            seats_num = int(class_n.select_one("span").text)  # type: ignore
        except ValueError:
            seats_num = "Без нумерации мест"
            tickets_by_class[name] = "\u221e"
            continue
        if name in tickets_by_class:
            tickets_by_class[name] += seats_num
        else:
            tickets_by_class[name] = seats_num
    return tickets_by_class


# Проверка времени (прекратить отслеживание за 15 минут до отправления)
def check_depart_time(train_number, soup, train_id):
    # информация о наличии мест и классов вагонов
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"] \
            div.sch-table__time.train-from-time'
    )
    logging.info(
        f"FG check_depart_time (train_number, train_id, train_info) \n"
        f"{train_number, ' ',  train_id, '\n', train_info}"
    )
    # Сравнение текущей даты с датой отправления
    # Если даты совпадают, а train_info пустой == ошибка сайта
    # Такие сложности из-за особености сайта: если поезд сегодня
    # но уже отправился, то будет время в секундах с минусом.
    # Если дата прошла, то данных не будет вовсе
    # Получение даты отправления (работает уже в цикле отслеживания
    # и не работает когда только начинается отслеживание - train_id=None):
    resp_db = execute_db_query(
        """
            SELECT r.date FROM trains t
            JOIN routes r ON t.route_id = r.route_id
            WHERE train_id = %s
            """,
        (train_id,),
        fetchone=True,
    )
    if resp_db:
        depart_time = datetime.strptime(resp_db[0], "%Y-%m-%d").date()
    else:
        depart_time = datetime(2000, 1, 1).date()
    today = datetime.today().date()

    # Если дата уже прошла вызвать 0. Если сбой информации не будет.

    if not train_info and (depart_time >= today):
        raise SiteResponseError('Ошибка получения данных поезда с сайта')
    elif not train_info and depart_time < today:
        # Условие важно особенно на стыке суток
        result = 0
    else:
        # время до отправления в секундах
        result = int(train_info[0]["data-value"])
    return result


# ============================================================================
# Фоновые задачи


# Удаление прошедших маршрутов из таблицы routes
def cleanup_expired_routes():
    while True:
        try:
            async_db_call(_cleanup_logic)
        except Exception:
            raise
        # Проверяем каждые 2 часа
        time.sleep(2 * 60 * 60)


def _cleanup_logic():
    try:
        # Текущая дата
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        # Находим маршруты с прошедшей датой
        expired_routes = execute_db_query(
            """
            SELECT route_id FROM routes
            WHERE date < %s
            """,
            (yesterday,),
            fetchall=True,
        )
        if expired_routes:
            # Удаляем связанные записи из tracking
            execute_db_query(
                """
                DELETE FROM tracking
                WHERE train_id IN (
                    SELECT train_id FROM trains
                    WHERE route_id IN (
                        SELECT route_id FROM routes
                        WHERE date < %s
                    )
                )
                """,
                (yesterday,),
                commit=True,
            )

            # Удаляем поезда
            execute_db_query(
                """
                DELETE FROM trains
                WHERE route_id IN (
                    SELECT route_id FROM routes
                    WHERE date < %s
                )
                """,
                (yesterday,),
                commit=True,
            )

            # Удаляем сами маршруты
            execute_db_query(
                """
                DELETE FROM routes
                WHERE date < %s
                """,
                (yesterday,),
                commit=True,
            )

            logging.info(f"Удалено {len(expired_routes)} устаревших маршрутов")
    except Exception as e:
        logging.error(f"Database error in cleanup_expired_routes: {str(e)}")
        raise


# Отслеживание работающих потоков каждые 30 мин
def monitor_threads_track():
    while True:
        active_threads = [
            f"Thread: {t.name}, ID: {t.ident}" for t in threading.enumerate()
        ]

        logging.info(f"Active threads: {len(active_threads)}")
        for t in active_threads:
            logging.info(f"Thread {t} is alive")
        time.sleep(1800)


def start_background_tasks():
    # Поток для очистки устаревших маршрутов
    cleanup_thread = threading.Thread(
        target=cleanup_expired_routes,
        name="route_cleanup",
        daemon=True,  # Поток завершится при завершении main-потока
    )
    cleanup_thread.start()

    # Отслеживание работающих потоков каждый час
    monitor_threads = threading.Thread(
        target=monitor_threads_track,
        name="monitor_threads",
        daemon=True,  # Поток завершится при завершении main-потока
    )
    monitor_threads.start()


# =============================================================================
# Останов бота и завершение программы


# Останов сессии для пользователя
# Универсальный фильтр для реакции на любое сообщение
@bot.message_handler(func=lambda message: message.text.startswith('/stop'))
def universal_stop_handler(message):
    stop(message)


@ensure_start
def stop(message):
    chat_id = message.chat.id

    # Запрашиваем подтверждение
    markup = types.InlineKeyboardMarkup()
    btn_yes = types.InlineKeyboardButton("Да", callback_data="confirm_stop")
    btn_no = types.InlineKeyboardButton("Нет", callback_data="cancel_stop")
    markup.add(btn_yes, btn_no)

    bot.send_message(
        chat_id,
        "❗ Вы уверены, что хотите остановить бота?",
        reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda call: call.data == "cancel_stop")
def cancel_stop(call):
    chat_id = call.message.chat.id

    # Удаляем сообщение с кнопками
    bot.delete_message(chat_id, call.message.message_id)
    bot.send_message(chat_id, "🟢 Бот в работе")


@bot.callback_query_handler(func=lambda call: call.data == "confirm_stop")
def confirm_stop(call):
    chat_id = call.message.chat.id

    # Для отображения активности
    bot.send_chat_action(chat_id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay

    # Удаляем сообщение с кнопками
    bot.delete_message(chat_id, call.message.message_id)

    # #!!!ДОБАВИТЬ
    # # Очищаем переписку
    # clear_chat_history(chat_id)

    # Останавливаем бота

    # для остановки параллельного потока необходимо перевести статус для
    # всех поездов в False
    # после остановки поездов, удалить всю сессию

    async_db_call(_confirm_stop_logic, chat_id)
    del_user_data(chat_id)
    bot.send_message(chat_id, "🛑 Бот остановлен")


def _confirm_stop_logic(chat_id):
    try:
        # Удалить все записи отслеживания пользователя
        execute_db_query(
            """
            DELETE FROM tracking
            WHERE chat_id = %s
            """,
            (chat_id,),
            commit=True,
        )

        # Удалить самого пользователя
        execute_db_query(
            """
            DELETE FROM users
            WHERE chat_id = %s
            """,
            (chat_id,),
            commit=True,
        )
        logging.info(
            f"Бот остановлен chat_id: {chat_id}." f"Список отслеживания очищен"
        )
    except Exception as e:
        logging.error(f"Database error in cleanup_expired_routes: {str(e)}")
        raise


# Выход из программы
@bot.message_handler(commands=[stop_code])  # type: ignore
def exit_admin(message):
    chat_id = message.chat.id

    with db_lock:
        try:
            conn = db_pool.getconn()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tracking WHERE chat_id = %s", (chat_id,)
            )
            cursor.execute("DELETE FROM users WHERE chat_id = %s", (chat_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    bot.send_message(chat_id, "Выход из ПО")

    def stop_bot():
        bot.stop_polling()
        os._exit(0)  # Принудительный выход

    threading.Thread(target=stop_bot).start()


# =============================================================================
# Запуск бота в режиме непрерывной работы
if __name__ == "__main__":
    # Запуск существующих отслеживаний
    restore_all_trackings()
    # Проверка устаревших маршрутов и отслеживание потоков
    start_background_tasks()

    try:
        try:
            bot.remove_webhook()  # Попытка удалить существующий webhook
            time.sleep(2)  # Пауза для обработки запроса сервером Telegram
            success = bot.set_webhook(url=f"{webhook_url}/{token}")
            if success:
                logging.info(f"Webhook установлен: {webhook_url}")
            else:
                logging.error("Ошибка установки webhook")
            app.run(host=db_host, port=web_port)

        except apihelper.ApiTelegramException as e:
            # Игнорирование ошибки "webhook не установлен"
            if "webhook is not set" not in str(e):
                logging.error(f"Webhook deletion failed: {e}")
            else:
                raise  # Проброс других ошибок API

        # Ошибка запроса
    except requests.exceptions.ReadTimeout as e:
        logging.error(f"Timeout error: {e}.")

    # Остальные ошибки
    except Exception as e:
        logging.error(f"Attempt failed: {str(e)}")
