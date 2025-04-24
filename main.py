import json
import os
import sqlite3

# Библиотека для параллельных потоков
import threading
import time
from collections import defaultdict
from datetime import datetime
from random import randint
from urllib.parse import quote

import requests

# Импорт для бота
import telebot
from bs4 import BeautifulSoup

# Для парсинга страниц
from bs4.filter import SoupStrainer
from telebot import types

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
    "0": "Без нумерации мест",
    "1": "Общий",
    "2": "Сидячий",
    "3": "Плацкартный",
    "4": "Купейный",
    "5": "Мягкий",
    "6": "СВ",
}


# В начале кода создаем словарь для временного хранения вводимых данных
user_data = defaultdict(
    lambda: {}
)  # Ключ - chat_id, значение - словарь с данными

# -----------------------------------------------------------------------------
# Создание БД и подключение

conn = sqlite3.connect('tracking_train.sqlite3')

# Курсор для выполнения команд
cursor = conn.cursor()

# Создание таблицы пользователей
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
    status TEXT DEFAULT False,
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
# Функции для работы с БД
def add_user_db(chat_id):
    conn = sqlite3.connect('tracking_train.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT OR IGNORE INTO users (chat_id)
            VALUES (?)
        """,
            (chat_id,),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def add_route_db(city_from, city_to, date, url):
    conn = sqlite3.connect('tracking_train.sqlite3')
    cursor = conn.cursor()
    try:
        # Благодаря URL UNIQUE не будет повторной записи
        cursor.execute(
            """
            INSERT OR IGNORE INTO routes (city_from, city_to, date, url)
            VALUES (?, ?, ? , ?)
        """,
            (city_from, city_to, date, url),
        )
        # cursor.execute("SELECT route_id FROM routes WHERE url = ?", (url,))
        # route_id = cursor.fetchone()[0]
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def add_train_db(train, time_depart, time_arriv, url):
    conn = sqlite3.connect('tracking_train.sqlite3')
    cursor = conn.cursor()
    try:
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
    finally:
        cursor.close()
        conn.close()


def add_tracking_db(chat_id, train_selected, ticket_dict, url, status=False):
    conn = sqlite3.connect('tracking_train.sqlite3')
    cursor = conn.cursor()
    try:
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
            INSERT INTO tracking (chat_id, train_id, json_ticket_dict, status)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, train_id, json_ticket_dict, status),
        )

        conn.commit()
    finally:
        cursor.close()
        conn.close()


# Получить список поездов по заданному маршруту из БД
def get_trains_list_db(url):
    conn = sqlite3.connect('tracking_train.sqlite3')
    cursor = conn.cursor()
    try:
        # Получить route_id по известному URL
        cursor.execute("SELECT route_id FROM routes WHERE url = ?", (url,))
        route_id = cursor.fetchone()[0]
        # Получить trains_list по route_id
        cursor.execute(
            """
        SELECT train_number, time_depart, time_arriv FROM trains
        WHERE route_id = ?""",
            (route_id,),
        )
        trains_list = cursor.fetchall()

        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return trains_list


# ----------------------------------------------------------------------------


# Декоратор: Проверка "start" для избежания ошибок
def ensure_start(func):
    def wrapper(message):
        try:
            chat_id = message.chat.id
        except AttributeError:
            chat_id = message.message.chat.id  # если проверяется callback
        if chat_id not in user_data:
            bot.send_message(chat_id, "Сначала введите /start")
            return
        return func(message)

    return wrapper


# =============================================================================
# Подключение бота для ввода данных


# Создаётся объект бота, который умеет принимать сообщения от Telegram.
bot = telebot.TeleBot(token, threaded=True, num_threads=5)


# Запуск чата. Запрос города отправления
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Город отправления: ")
    # Регистрация следующей функции для города отправления
    bot.register_next_step_handler(message, get_city_from)
    user_data[chat_id] = {"step": "start"}

    # Добавить пользователя в БД
    add_user_db(chat_id)


# Получение города отправления. Проверка наличия в списке станций
def get_city_from(message):
    chat_id = message.chat.id
    city_from = normalize_city_name(message.text)
    if city_from not in all_station_list:
        bot.send_message(chat_id, "Неправильное название станции отправления")
        # Возврат при ошибке ввода
        bot.register_next_step_handler(message, get_city_from)
        return
    user_data[chat_id].update({"city_from": city_from})
    bot.send_message(chat_id, "Город прибытия: ")
    # Регистрация следующей функции для города прибытия
    bot.register_next_step_handler(message, get_city_to)


# Получение города прибытия. Проверка наличия в списке станций
def get_city_to(message):
    chat_id = message.chat.id
    city_to = normalize_city_name(message.text)
    if city_to not in all_station_list:
        bot.send_message(chat_id, "Неправильное название станции назначения")
        # Возврат при ошибке ввода
        bot.register_next_step_handler(message, get_city_to)
        return
    user_data[chat_id].update({"city_to": city_to})
    bot.send_message(chat_id, "Дата в формате гггг-мм-дд: ")
    # Регистрация следующей функции для даты
    bot.register_next_step_handler(message, get_date)


# Получение даты отправления
def get_date(message):
    chat_id = message.chat.id
    try:
        date = normalize_date(message.text)
        user_data[chat_id].update({"date": date})
        get_trains_list(message)
    except (PastDateError, FutureDateError, ValueError) as e:
        bot.send_message(chat_id, f"{e}.\nПовторите ввод даты")
        # Возврат при ошибке ввода
        bot.register_next_step_handler(message, get_date)
        return


# Функция получения поездов по маршруту
def get_trains_list(message):
    chat_id = message.chat.id
    q_from = quote(user_data[chat_id]["city_from"])
    q_to = quote(user_data[chat_id]["city_to"])
    date = user_data[chat_id]["date"]
    chat_id = chat_id

    # Получение новой страницы "soup"
    url = f"https://pass.rw.by/ru/route/?from={q_from}&to={q_to}&date={date}"
    user_data[chat_id]["url"] = url
    try:
        r = requests.get(url)
    except Exception as e:
        error_msg = f"Ошибка: {str(e)}\nДавайте начнем заново."
        bot.send_message(chat_id, error_msg)
        start(message)  # Возвращаемся к началу

    # Добавляет маршрут в БД и возвращает route_id
    add_route_db(
        user_data[chat_id]["city_from"],
        user_data[chat_id]["city_to"],
        date,
        url,
    )

    only_span_div_tag = SoupStrainer(["span", "div"])
    soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)

    # Обновление страницы
    user_data[chat_id]["soup"] = soup

    train_id_list = [
        i.text for i in soup.find_all("span", class_="train-number")
    ]

    trains_list = []
    # получение времени отправления и прибытия
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
        add_train_db(train, time_depart, time_arriv, url)
        show_train_list(message)


def show_train_list(message):
    chat_id = message.chat.id
    url = user_data[chat_id]["url"]
    trains_list = get_trains_list_db(url)
    markup = types.InlineKeyboardMarkup()
    # Отображение кнопок выбора поезда из доступного списка
    for train in trains_list:
        markup.row(
            types.InlineKeyboardButton(
                f"Поезд: {train[0]} Отпр: {train[1]} Приб: {train[2]}",
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
    if "tracking_active" not in user_data[chat_id]:
        user_data[chat_id]["tracking_active"] = {}

    user_data[chat_id]["tracking_active"][train_selected] = {
        "status": False,
        "ticket_dict": ticket_dict,
    }

    # Добавить поезд в список отслеживания

    url = user_data[chat_id]['url']
    add_tracking_db(chat_id, train_selected, ticket_dict, url, status=False)

    # Кнопка включения слежения за поездом
    markup = types.InlineKeyboardMarkup()

    # Если 'Без нумерованных мест' возврат на выбор поезда
    if seats_type_dict["0"] in ticket_dict:
        btn_track = types.InlineKeyboardButton(
            "Отслеживание недоступно.\nВернуться к списку поездов",
            callback_data="re_get_trains_list",
        )
    # Проверка времени отправления
    elif check_depart_time(train_selected, soup) < 0:
        btn_track = types.InlineKeyboardButton(
            "Поезд уже отправился.\nВернуться к списку поездов",
            callback_data="re_get_trains_list",
        )
    else:
        btn_track = types.InlineKeyboardButton(
            "Начать отслеживание",
            callback_data=f"{train_selected}_start_tracking",
        )
    markup.add(btn_track)

    bot.send_message(
        chat_id=callback.message.chat.id,
        text=f"Поезд №{train_selected}\n{ticket_dict}",
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
        # В разработке. Пока возврат к предыдущему маршруту
        bot.send_message(
            message.chat.id,
            "Функция в разработке. Возврат к сущетвующему маршруту",
        )
        get_trains_list(message)
        pass

        # bot.send_message(message.chat.id, "Город отправления: ")
        # # Регистрация следующей функции для города отправления
        # bot.register_next_step_handler(message, get_city_from)
        # pass
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

    # Проверка отслеживания поезда, чтобы не запустить излишний поток
    if user_data[chat_id]["tracking_active"][train_tracking]["status"]:
        bot.send_message(
            chat_id, f"Отслеживание поезда {train_tracking} уже запущено."
        )
        return

    # Регистрация поезда в списке отслеживания
    user_data[chat_id]["tracking_active"][train_tracking]["status"] = True

    # Запуск отслеживания в параллельном потоке
    # Лучше передавать аргументы, а не использовать внешние
    def tracking_loop(chat_id, train_tracking):
        try:
            while True:
                tracking_data = (
                    user_data.get(chat_id, {})
                    .get("tracking_active", {})
                    .get(train_tracking)
                )

                # Проверка, что данные существуют и отслеживаются активно,
                # иначе останов сессии
                if not tracking_data or not tracking_data.get("status"):
                    print(
                        f"[thread exit] Поток завершён:/"
                        f"{train_tracking} для {chat_id}"
                    )
                    return

                # Получение новой страницы "soup"
                try:
                    r = requests.get(user_data[chat_id]["url"])
                except Exception as e:
                    error_msg = f"Ошибка: {str(e)}\nДавайте начнем заново."
                    bot.send_message(chat_id, error_msg)
                    start(chat_id)  # Возвращаемся к началу

                only_span_div_tag = SoupStrainer(["span", "div"])
                soup = BeautifulSoup(
                    r.text, "lxml", parse_only=only_span_div_tag
                )

                # Добавление в сессию
                user_data[chat_id]["soup"] = soup

                # Проверка времени
                # (прекратить отслеживание за 10 мин до отправления)
                if check_depart_time(train_tracking, soup) < 600:
                    bot.send_message(
                        chat_id,
                        f"Отслеживание завершёно за 10 мин"
                        f"до отправления поезда {train_tracking}",
                    )
                    print(
                        f"[thread exit] Поток завершён за 10 мин/"
                        f"до отпр.: {train_tracking} для {chat_id}"
                    )
                    return

                # Получение более свежей информации по билетам
                ticket_dict = check_tickets_by_class(
                    train_tracking, soup, chat_id
                )

                # Выводить сообщение при появлении изменений в билетах
                #  + быстрая ссылка
                if ticket_dict != tracking_data.get("ticket_dict"):
                    markup_url = types.InlineKeyboardMarkup()  # объект кнопки
                    url_to_ticket = types.InlineKeyboardButton(
                        "Перейти на сайт", url=user_data[chat_id]["url"]
                    )
                    markup_url.row(url_to_ticket)
                    bot.send_message(
                        chat_id,
                        f"Обновление по {train_tracking}: {ticket_dict}",
                        reply_markup=markup_url,
                    )
                    tracking_data["ticket_dict"] = ticket_dict

                # Отслеживание активных потоков для отладки
                print("⚙️ Активные потоки:")
                for thread in threading.enumerate():
                    print(
                        f"  🔸 {thread.name}/"
                        f"{user_data[chat_id]['city_from']}/"
                        f"{user_data[chat_id]['city_to']}/"
                        f"{user_data[chat_id]['date']} "
                        f"(ID: {thread.ident})"
                    )

                time.sleep(randint(240, 300))

        except Exception as e:
            print(f"[thread error] {chat_id}, {train_tracking}: {str(e)}")

    # Регистрация и запуск параллельного потока с заданным именем
    # и аргументами, чтобы не быть в ситуации, когда
    # функция запустится через секунду-другую,
    # а к этому времени переменные уже будут другими.
    # Например, другой пользователь вызовет бота, и chat_id перезапишется,
    # а старый поток будет отслеживать не того юзера.
    thread = threading.Thread(
        target=tracking_loop,
        args=(chat_id, train_tracking),
        name=f"tracking_{train_tracking}_{chat_id}",
    )

    thread.start()
    bot.send_message(
        chat_id, f"Отслеживание поезда {train_tracking} запущено."
    )


# =============================================================================


# Отдельная функция для списка отслеживаемых поездов, т.к. используется
# для команд Отображения и Останова
def get_track_list(message):
    track_list = []
    chat_id = message.chat.id
    if user_data[chat_id].get("tracking_active", False):
        for train, info in user_data[chat_id]["tracking_active"].items():
            if info["status"]:
                track_list.append(train)
    return track_list  # для функции удаления из списка отслеживания


# Отображение списка отслеживаемых поездов
@bot.message_handler(commands=["show_track_list"])
@ensure_start
def show_track_list(message):
    reply = "Список отслеживания пуст"  # по умолчанию
    track_list = get_track_list(message)
    if track_list:
        reply = "\n".join(track_list)
    bot.reply_to(message, f"{reply}")


# Останов отслеживания конкретного поезда
@bot.message_handler(commands=["stop_track_train"])
@ensure_start
def stop_track_train(message):
    track_list = get_track_list(message)
    if track_list:
        markup = types.InlineKeyboardMarkup()
        for train in track_list:
            markup.row(
                types.InlineKeyboardButton(
                    f"Остановить отслеживание поезда: {train}",
                    callback_data=f"{train}_stop_tracking",
                )
            )
        bot.reply_to(message, "Список отслеживания: ", reply_markup=markup)
    else:
        bot.reply_to(message, "Список отслеживания пуст")


# Функция удаления поезда из списка отслеживания
@bot.callback_query_handler(
    func=lambda callback: callback.data.endswith("_stop_tracking")
)
@ensure_start
def stop_tracking_train_by_number(callback):
    bot.answer_callback_query(callback.id)
    train_stop_tracking = callback.data.split("_")[0]
    chat_id = callback.message.chat.id

    user_data[chat_id]["tracking_active"][train_stop_tracking][
        "status"
    ] = False

    bot.send_message(
        chat_id, f"Отслеживание поезда {train_stop_tracking} остановлено."
    )


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

    # Временно для тестов, попытка получить данные при следующем запросе
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


# Проверка времени (прекратить отслеживание за 10 минут до отправления)
def check_depart_time(train_number, soup):
    # информация о наличии мест и классов вагонов
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"] \
            div.sch-table__time.train-from-time'
    )
    # время до отправления в секундах
    return int(train_info[0]["data-value"])


# =============================================================================
# Останов бота и завершение программы


# Останов сессии для пользователя
@bot.message_handler(commands=["stop"])
@ensure_start
def stop(message):
    chat_id = message.chat.id
    # для остановки параллельного потока необходимо перевести статус для
    # всех поездов в False
    if chat_id in user_data and "tracking_active" in user_data[chat_id]:
        for train in user_data[chat_id]["tracking_active"]:
            user_data[chat_id]["tracking_active"][train]["status"] = False
    # после остановки поездов, удалить всю сессию
    user_data.pop(chat_id, None)
    bot.send_message(chat_id, "Бот остановлен. Список отслеживания очищен")


# Выход из программы
@bot.message_handler(commands=[stop_code])
def exit_admin(message):
    chat_id = message.chat.id
    if chat_id in user_data:
        for train in user_data[chat_id]["tracking_active"]:
            user_data[chat_id]["tracking_active"][train]["status"] = False
    user_data.pop(chat_id, None)
    bot.send_message(chat_id, "Выход из ПО")

    def stop_bot():
        bot.stop_polling()
        os._exit(0)  # Принудительный выход

    threading.Thread(target=stop_bot).start()


# =============================================================================
# Запуск бота в режиме непрерывной работы
if __name__ == "__main__":
    print("Бот запущен...")
    while True:
        try:
            bot.polling(non_stop=True, timeout=90, long_polling_timeout=60)
        except requests.exceptions.ReadTimeout as e:
            print(f"Timeout error: {e}. Restarting bot...")
            # Здесь можно добавить логику перезапуска
            time.sleep(10)
