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


# Класс ошибки для "Дата в прошлом"
class PastDateError(ValueError):
    pass


# Класс ошибки для "Дата в далеко в будущем"
class FutureDateError(ValueError):
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
# Создание БД и подключение
with db_lock:
    conn = sqlite3.connect('tracking_train.sqlite3')

    # Курсор для выполнения команд
    cursor = conn.cursor()

    # Создание таблицы пользователей c текущей информацией
    # (словарь)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY
    )
    """
    )

    # Создание таблицы маршрутов
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS routes (
        route_id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_from TEXT,
        city_to TEXT,
        date TEXT,
        url TEXT UNIQUE
    )
    """
    )

    # Создание таблицы поездов по каждому маршруту
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS trains (
        train_id INTEGER PRIMARY KEY AUTOINCREMENT,
        route_id INTEGER NOT NULL,
        train_number TEXT,
        time_depart TEXT,
        time_arriv TEXT,
        FOREIGN KEY (route_id) REFERENCES routes(route_id)
        UNIQUE (route_id, train_number, time_depart, time_arriv)
    )
    """
    )

    # Создание таблицы отслеживания пользователем поезда
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS tracking (
        tracking_id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        train_id INTEGER NOT NULL,
        json_ticket_dict TEXT,
        status INTEGER DEFAULT 0,
        FOREIGN KEY (chat_id) REFERENCES users(chat_id),
        FOREIGN KEY (train_id) REFERENCES trains(train_id)
        UNIQUE (chat_id, train_id)
    )
    """
    )

    # Синхронизация именений
    conn.commit()
    # Закрыть соединение с таблицей
    cursor.close()
    # Закрыть соединение с БД
    conn.close()


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


# Универсальная функция-обёртка
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
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO users (chat_id)
            VALUES (?)
        """,
            (chat_id,),
        )
        conn.commit()
        logging.info(f"User {chat_id} added to database")
    except sqlite3.Error as e:
        logging.error(f"Database error in add_user_db: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


def add_route_db(city_from, city_to, date, url):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Благодаря URL UNIQUE не будет повторной записи
        cursor.execute(
            """
            INSERT OR IGNORE INTO routes (city_from, city_to, date, url)
            VALUES (?, ?, ? , ?)
            """,
            (city_from, city_to, date, url),
        )
        conn.commit()
        logging.info(f"Route {city_from}-{city_to}-{date} added to database")
    except sqlite3.Error as e:
        logging.error(f"Database error in add_route_db: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


def add_train_db(train, time_depart, time_arriv, url):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Выбор соответствующего маршрута
        cursor.execute("SELECT route_id FROM routes WHERE url = ?", (url,))
        route_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT OR IGNORE INTO trains
            (route_id, train_number, time_depart, time_arriv)
            VALUES (?, ?, ?, ?)
            """,
            (route_id, train, time_depart, time_arriv),
        )
        conn.commit()
        logging.info(
            f"Train: {train} for route_id: {route_id} added to database"
        )
    except sqlite3.Error as e:
        logging.error(f"Database error in add_train_db: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


def add_tracking_db(chat_id, train_selected, ticket_dict, url, status=True):

    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Получить route_id по известному URL
        cursor.execute("SELECT route_id FROM routes WHERE url = ?", (url,))
        route_id = cursor.fetchone()[0]

        # Получить train_id по route_id и train_selected
        cursor.execute(
            """
            SELECT train_id FROM trains
            WHERE route_id = ? AND train_number = ?
            """,
            (route_id, train_selected),
        )
        train_id = cursor.fetchone()[0]

        # Преобразование словаря билетов в JSON
        json_ticket_dict = json.dumps(ticket_dict)

        # Вставка в список слежения с выбранным статусом
        cursor.execute(
            """
            INSERT OR IGNORE INTO tracking
            (chat_id, train_id, json_ticket_dict, status)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, train_id, json_ticket_dict, status),
        )
        conn.commit()
        logging.info(f"Train_id: {train_id} added to db_tracking_list")
    except sqlite3.Error as e:
        logging.error(f"Database error in add_tracking_db: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# Получить список поездов по заданному маршруту из БД
def get_trains_list_db(url):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Получить route_id по известному URL
        cursor.execute("SELECT route_id FROM routes WHERE url = ?", (url,))
        route_id = cursor.fetchone()[0]
        # Получить trains_list по route_id
        cursor.execute(
            """
        SELECT train_number, time_depart, time_arriv FROM trains
        WHERE route_id = ?
        ORDER BY time_depart""",
            (route_id,),
        )
        trains_list = cursor.fetchall()
        conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Database error in get_trains_list_db: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise
    return trains_list


# Получить данные для цикла отслеживания поезда
def get_loop_data_list(chat_id, train_tracking, url):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT r.route_id, t.train_id,
            EXISTS (
                SELECT 1 FROM tracking tr
                WHERE tr.train_id = t.train_id AND tr.chat_id = ?
            ) AS is_tracked
            FROM routes r
            JOIN trains t ON r.route_id = t.route_id
            LEFT JOIN tracking tr
            ON tr.train_id = t.train_id AND tr.chat_id = ?
            WHERE r.url = ? AND t.train_number = ?
            """,
            (chat_id, chat_id, url, train_tracking),
        )
        resp = cursor.fetchone()
        # Отдельный запрос: сколько активных отслеживаний у пользователя
        cursor.execute(
            """
            SELECT COUNT(*) FROM tracking
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        count = cursor.fetchone()[0]
        conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Database error in get_loop_data_list: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise

    result = {
        "route_id": resp[0],
        "train_id": resp[1],
        "status_exist": resp[2],
        "count": count,
    }
    return result


# Получение свежих данных из таблицы при отслеживании
def get_fresh_loop(
    chat_id,
    train_id,
):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        cursor.execute(
            """SELECT json_ticket_dict, status FROM tracking
            WHERE chat_id = ? AND train_id = ?
                """,
            (
                chat_id,
                train_id,
            ),
        )
        conn.commit()
        result = cursor.fetchone()
        if result:
            json_str, status = result  # Распаковываем кортеж
            memory_ticket_dict = json.loads(json_str)  # Декодируем JSON строку
            status = bool(int(status))
            logging.debug(f"FG2 memory_ticket_dict {memory_ticket_dict}")
        else:
            # Обработка случая, когда запись не найдена
            memory_ticket_dict = {}
            status = False
        return memory_ticket_dict, status
    except sqlite3.Error as e:
        logging.error(f"Database error in get_fresh_loop: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# Получение списка отслеживаемых поездов, т.к. используется
# для команд Отображения и Останова
def get_track_list(message):

    chat_id = message.chat.id

    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Получить route_id по известному URL
        cursor.execute(
            """
            SELECT  tracking_id, t.train_number,
            r.city_from, r.city_to, r.date, status, t.time_depart
            FROM tracking tr
            JOIN trains t ON tr.train_id = t.train_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE tr.chat_id = ?
        """,
            (chat_id,),
        )
        track_list = cursor.fetchall()

        conn.commit()

    except sqlite3.Error as e:
        logging.error(f"Database error in get_track_list: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise

    return track_list  # для функции удаления из списка отслеживания


# Удаление маршрута из списка отслеживани
def del_tracking_db(
    chat_id,
    train_id,
):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM tracking
            WHERE chat_id = ? AND train_id = ?
            """,
            (
                chat_id,
                train_id,
            ),
        )
        conn.commit()

    except sqlite3.Error as e:
        logging.error(f"Database error in del_tracking_db: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# Обновление таблицы отслеживания в цикле
def update_tracking_loop(
    json_ticket_dict,
    chat_id,
    train_id,
):
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE tracking
            SET json_ticket_dict = ?
            WHERE chat_id = ? AND train_id = ?
            """,
            (
                json_ticket_dict,
                chat_id,
                train_id,
            ),
        )
        conn.commit()

    except sqlite3.Error as e:
        logging.error(f"Database error in update_tracking_loop: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# Проверка пользователя в БД
def check_user_exists(chat_id):
    try:
        conn = sqlite3.connect("tracking_train.sqlite3")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM users WHERE chat_id = ?) LIMIT 1;",
            (chat_id,),
        )
        result = cursor.fetchone()[0]
        return bool(result)
    except sqlite3.Error as e:
        logging.error(f"Database error in check_user_exists: {str(e)}")
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
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
        if text.startswith("/stop"):
            stop(message)
            return
        if text.startswith("/start"):
            start(message)
            return
        if text.startswith(f"/{stop_code}"):
            exit_admin(message)
            return
        # Добавить другие команды по мере надобности
        return func(message)

    return wrapper


# =============================================================================
# Подключение бота для ввода данных


# Создаётся объект бота, который умеет принимать сообщения от Telegram.
bot = telebot.TeleBot(token, threaded=True)  # type: ignore


# Запуск чата. Запрос города отправления
# (функции до старта отслеживания используют словарь user_data
# с текущей сессией ввода данных от пользователя)
@bot.message_handler(commands=["start"])
def start(message):
    try:
        chat_id = message.chat.id
        logging.info(f"User {chat_id} started the bot")

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
    markup = types.ReplyKeyboardMarkup(
        one_time_keyboard=True, resize_keyboard=True, row_width=2
    )
    today_btn = types.KeyboardButton("Сегодня")
    tomorrow_btn = types.KeyboardButton("Завтра")
    markup.add(today_btn, tomorrow_btn)

    bot.send_message(
        chat_id, "📅Выберите дату или введите вручную в формате гггг-мм-дд: "
    )
    # Регистрация следующей функции для даты
    bot.register_next_step_handler(message, get_date)


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

    only_span_div_tag = SoupStrainer(["span", "div"])
    soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)

    # Обновление страницы
    update_user_data(chat_id, "soup", soup)

    train_id_list = [
        i.text for i in soup.find_all("span", class_="train-number")
    ]

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


def show_train_list(message):
    chat_id = message.chat.id
    try:
        url = user_data[chat_id]["url"]
    except KeyError:
        bot.send_message(
            chat_id,
            "❓Маршрут не найден или ошибка серевера.\
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
    if not trains_list:
        bot.send_message(
            chat_id,
            "❓🚆Поезда не найдены либо ошибка сервера.\
                \nПовторите ввод маршрута",
        )
        start(message)
        return
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
    elif check_depart_time(train_selected, soup) < 0:
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

    # Повторное получение инф-ции по билетам для внесения в таблицу отслеж.
    r = requests.get(url)

    only_span_div_tag = SoupStrainer(["span", "div"])
    soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)
    ticket_dict = check_tickets_by_class(train_tracking, soup, chat_id)
    # Изменение статуса в БД
    try:

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
            status=False,
        )

    except sqlite3.Error as e:
        logging.error(f"Database error in start_tracking_train: {str(e)}")
        raise

    # Запуск отслеживания в параллельном потоке
    # Лучше передавать аргументы, а не использовать внешние
    def tracking_loop(chat_id, train_tracking, train_id, route_id, url):
        logging.debug(f"Tracking train {train_tracking} for user {chat_id}")
        try:
            while True:
                # Время обращения к БД от пользователя для debug
                start_time_db_loop = time.time()

                try:
                    memory_ticket_dict, status = async_db_call(
                        get_fresh_loop, chat_id, train_id
                    )

                    if not status:
                        logging.info(
                            f"Stopping tracking for train"
                            f"{train_tracking}, user {chat_id}"
                        )
                        return

                    r = requests.get(url)

                    only_span_div_tag = SoupStrainer(["span", "div"])
                    soup = BeautifulSoup(
                        r.text, "lxml", parse_only=only_span_div_tag
                    )

                    # Проверка времени
                    # (прекратить отслеживание за 15 мин до отправления)
                    if check_depart_time(train_tracking, soup) < 1000:

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
                        markup_url = (
                            types.InlineKeyboardMarkup()
                        )  # объект кнопки
                        url_to_ticket = types.InlineKeyboardButton(
                            "На сайт", url=user_data[chat_id]["url"]
                        )
                        markup_url.row(url_to_ticket)
                        bot.send_message(
                            chat_id,
                            f"Обновление по {train_tracking}:\n"
                            f"{ticket_dict}",
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

                except sqlite3.Error as e:
                    logging.error(f"Database error in tracking loop: {str(e)}")
                    time.sleep(60)  # Попытка через 1 мин
                    continue
                    # При отсутствии нужной записи в БД
                except TypeError as e:
                    logging.error(f"Database error in tracking loop: {str(e)}")
                    raise
                except requests.exceptions.RequestException as e:
                    logging.error(f"Database error in tracking loop: {str(e)}")
                    raise
                end_time_db_loop = time.time()
                db_loop_time = end_time_db_loop - start_time_db_loop
                logging.debug(
                    f"Время к БД для {chat_id} в цикле loop \n\
                    {db_loop_time:.4f} сек"
                )
                time.sleep(randint(300, 600))
        except Exception as e:
            logging.error(
                f"Tracking loop crashed for train {train_tracking}, \
                          user {chat_id}: {str(e)}",
                exc_info=True,
            )
            error_msg = (
                "❗ Ошибка бота\nПроверить отслеживание или начать заново"
            )
            bot.send_message(chat_id, error_msg)
            # start(callback.message)  # Возвращаемся к началу

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
    track_list = list(
        filter(lambda x: x[5] == 1, async_db_call(get_track_list, message))
    )
    # tracking_id -> int(),
    # t.train_number -> str(),
    # r.city_from, r.city_to, r.date -> str(),
    # status -> int(),
    # t.time_depart -> str()
    if track_list:

        reply_edit = []
        for x in track_list:
            date_obj = datetime.strptime(x[4], "%Y-%m-%d")
            f_date = date_obj.strftime("%d.%m.%y")
            reply_edit.append(
                f"🚆 {x[1]} {x[2]}➡️{x[3]}\n🕒 {x[6]} {f_date} \n{'-'*5}"
            )
        reply = "\n".join(reply_edit)
    bot.reply_to(message, f"{reply}")


# Останов отслеживания конкретного поезда
@bot.message_handler(commands=["stop_track_train"])
@ensure_start
def stop_track_train(message):
    track_list = list(
        filter(lambda x: x[5] == 1, async_db_call(get_track_list, message))
    )
    # tracking_id -> int(),
    # t.train_number -> str(),
    # r.city_from, r.city_to, r.date -> str(),
    # status -> int()
    # t.time_depart -> str()

    # Для отображения активности
    bot.send_chat_action(message.chat.id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay

    if track_list:
        markup = types.InlineKeyboardMarkup()
        for x in track_list:
            date_obj = datetime.strptime(x[4], "%Y-%m-%d")
            f_date = date_obj.strftime("%d.%m.%y")
            # Для отображения в сообщении
            reply = f"🚫 {x[1]} {x[2]}➡️{x[3]} 🕒 {x[6]} {f_date}"
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
    logging.debug(f"!!! FLAG10 {tracking_id}")
    try:
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Удалить из списка
        cursor.execute(
            """
            DELETE FROM tracking WHERE tracking_id = ?;
        """,
            (tracking_id,),
        )
        conn.commit()

    except sqlite3.Error as e:
        logging.error(
            f"Database error in stop_tracking_train_by_number: {str(e)}"
        )
        conn.rollback()
        raise
    finally:
        # Если соединение не открылось
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# ============================================================================
# Вспомогательные функции


# Нормализация ввода города
def normalize_city_name(name):
    return name.strip().lower().capitalize()


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
        return "Мест нет"
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
def check_depart_time(train_number, soup):
    # информация о наличии мест и классов вагонов
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"] \
            div.sch-table__time.train-from-time'
    )
    # Если дата уже прошла, информации не будет. Вызвать 0
    if not train_info:
        result = 0
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
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        # Текущая дата
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        # Находим маршруты с прошедшей датой
        cursor.execute(
            """
            SELECT route_id FROM routes
            WHERE date < ?
        """,
            (yesterday,),
        )
        expired_routes = cursor.fetchall()
        if expired_routes:
            # Удаляем связанные записи из tracking
            cursor.execute(
                """
                DELETE FROM tracking
                WHERE train_id IN (
                SELECT train_id FROM trains
                WHERE route_id IN (
                SELECT route_id FROM routes
                WHERE date < ?
                    )
                )
                """,
                (yesterday,),
            )

            # Удаляем поезда
            cursor.execute(
                """
                DELETE FROM trains
                WHERE route_id IN (
                SELECT route_id FROM routes
                WHERE date < ?
                )
            """,
                (yesterday,),
            )

            # Удаляем сами маршруты
            cursor.execute(
                """
                DELETE FROM routes
                WHERE date < ?
            """,
                (yesterday,),
            )
            conn.commit()
            logging.info(f"Удалено {len(expired_routes)} устаревших маршрутов")
    except sqlite3.Error as e:
        logging.error(f"Database error in cleanup_expired_routes: {str(e)}")
        raise
    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# Отслеживание работающих потоков каждый час
def monitor_threads_track():
    while True:
        active_threads = [
            f"Thread: {t.name}, ID: {t.ident}" for t in threading.enumerate()
        ]

        logging.debug(f"Active threads: {len(active_threads)}")
        for t in active_threads:
            logging.debug(f"Thread {t} is alive")
        time.sleep(3600)


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
        conn = sqlite3.connect('tracking_train.sqlite3')
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM tracking
            WHERE chat_id = ?
        """,
            (chat_id,),
        )
        cursor.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        conn.commit()
        logging.info(
            f"Бот остановлен chat_id: {chat_id}." f"Список отслеживания очищен"
        )
    except sqlite3.Error as e:
        logging.error(f"Database error in cleanup_expired_routes: {str(e)}")
        raise
    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except (sqlite3.Error, AttributeError) as e:
            logging.error(f"Ошибка при закрытии БД: {e}")
            raise


# Выход из программы
@bot.message_handler(commands=[stop_code])  # type: ignore
def exit_admin(message):
    chat_id = message.chat.id

    with db_lock:
        try:
            conn = sqlite3.connect('tracking_train.sqlite3')
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tracking SET status = ?
                WHERE chat_id = ?
            """,
                (
                    False,
                    chat_id,
                ),
            )
            cursor.execute(
                "DELETE FROM tracking WHERE chat_id = ?", (chat_id,)
            )
            cursor.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
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
    # Проверка устаревших маршрутов и отслеживание потоков
    start_background_tasks()
    attempt_counter = 1
    max_attempts = 20
    min_delay = 15
    while True:
        # Ограничение на 3 попытки запуска с динамическим интервалом
        try:
            try:
                bot.delete_webhook()  # Попытка удалить существующий webhook
                time.sleep(1)  # Пауза для обработки запроса сервером Telegram
            except apihelper.ApiTelegramException as e:
                # Игнорирование ошибки "webhook не установлен"
                if "webhook is not set" not in str(e):
                    logging.error(f"Webhook deletion failed: {e}")
                    raise  # Проброс других ошибок API
            logging.info("Starting bot polling...")
            bot.polling(non_stop=True, timeout=90, long_polling_timeout=60)
            break

        # Ошибка запроса
        except requests.exceptions.ReadTimeout as e:
            logging.error(f"Timeout error: {e}. Restarting bot...")
            attempt_counter += 1
            time.sleep(min_delay * attempt_counter)

        # Остальные ошибки
        except Exception as e:
            logging.error(f"Attempt {attempt_counter} failed: {str(e)}")
            attempt_counter += 1
            time.sleep(min_delay * attempt_counter)
        if attempt_counter > max_attempts:
            logging.critical("Max retries exceeded")
            raise
