# import calendar
import json
import logging
import os

# import queue
import sys
import pytz

# Библиотека для параллельных потоков
import threading
import time

# from collections import defaultdict
# from copy import deepcopy
from datetime import datetime, timedelta

# from logging.handlers import RotatingFileHandler
from random import randint
from urllib.parse import quote

# import sqlite3
import flask
import requests

# Импорт для бота
import telebot
from bs4 import BeautifulSoup

# Для парсинга страниц
from bs4.filter import SoupStrainer
from telebot import apihelper, types

# Список станций
from all_stations_list import all_station_list  # , all_station_list_lower
from src.config import settings
from src.database import (
    add_route_db,
    add_tracking_db,
    add_trains_db_batch,
    add_user_db,
    check_db_connection,
    check_user_exists,
    cleanup_expired_routes_db,
    create_tables,
    del_tracking_db,
    delete_user_session,
    get_due_trackings,
    get_fresh_loop,
    get_loop_data_list,
    get_track_list,
    get_trains_list_db,
    get_user_session,
    stop_all_tracking_for_user_db,
    stop_tracking_by_id_db,
    update_next_check_time,
    update_tracking_loop,
    update_user_session,
)
from src.utils import (  # get_proxies,; SiteResponseError,
    FutureDateError,
    PastDateError,
    TrainNotFoundError,
    check_tickets_by_class,
    generate_calendar,
    get_departure_datetime_from_soup,
    has_departed,
    make_request,
    normalize_city_name,
    normalize_date,
    seats_type_dict,
)

# Remove the in-memory user_data dictionary and locks
# user_data = defaultdict(
#     lambda: {}
# )
# user_data_lock = threading.Lock()


# Настройка логирования
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Явно указываем stdout (обязательно для Cloud Run)
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


setup_logging()


def get_user_data(chat_id):
    """Gets user data from the database session."""
    logging.debug(f"FLAG start 111 get_user_data {'flag'}")
    return get_user_session(chat_id)


def update_user_data(chat_id, key, value):
    """Updates a key-value pair in the user's session data."""
    data = get_user_session(chat_id)
    data[key] = value
    update_user_session(chat_id, data)


def set_user_data(chat_id, data_dict):
    """Sets the entire session data for a user."""
    update_user_session(chat_id, data_dict)


def del_user_data(chat_id):
    """Deletes the session data for a user."""
    delete_user_session(chat_id)


def send_message_safely(chat_id, text, **kwargs):
    """
    Sends a message and handles potential ConnectionError exceptions.
    """
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Failed to send message to chat_id {chat_id} due to ConnectionError: {e}")
        return None


# Check database connection on startup
check_db_connection()


# ----------------------------------------------------------------------------
# Декоратор: Проверка "start" для избежания ошибок
def ensure_start(func):
    def wrapper(*args, **kwargs):
        message = args[0]
        try:
            chat_id = message.chat.id
        except AttributeError:
            chat_id = message.message.chat.id  # если проверяется callback
        # Проверка наличия пользователя в БД
        if not check_user_exists(chat_id):
            bot.send_message(chat_id, "Сначала введите /start")
            return
        return func(*args, **kwargs)

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
        if text.startswith(f"/{settings.STOP_CODE}"):
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
bot = telebot.TeleBot(settings.TOKEN, threaded=False)  # type: ignore
app_initialized = False  # Флаг, чтобы не выполнять инициализацию повторно

# Обработка запроса от Telegram (webhook endpoint)
"""
Этот маршрут слушает POST-запросы по пути /<токен>
Telegram будет присылать сюда новые сообщения пользователей,
если правильно установить webhook (bot.set_webhook(...)).
URL /TOKEN — это защита от чужих запросов.

"""


@app.route(f'/{settings.TOKEN}', methods=['POST'])
def webhook():
    try:
        json_str = flask.request.data.decode("utf-8")
        # превращает JSON-строку в объект telebot.types.Update:
        update = telebot.types.Update.de_json(json_str)
        logging.debug(f"FLAG Webhook получен! {update}")
        if update is not None:
            logging.debug(f"update is not None {update}")
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
        add_user_db(chat_id)
    except Exception as e:
        logging.error(f"Error in start command: {str(e)}", exc_info=True)


# Чтение города отправления. Проверка наличия в списке станций
@with_command_intercept
def get_city_from(message):
    # if message.text.startswith('/stop'):
    #     # Останов бота
    #     bot.register_next_step_handler(message, stop)
    #     return
    logging.debug(f"Flag start get_city_from {message.text}")
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
            + int(bool(examples)) * f"Варианты:\n\n{examples}"
        )
        logging.debug("Flag ctrl city in list")
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
    logging.debug('FLAG start get_city_to')
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

    logging.debug('FLAG start calendar generation')
    calendar_markup = generate_calendar()
    logging.debug('FLAG finish calendar generation')

    # Отправляем календарь сразу
    logging.debug('FLAG sending calendar message')
    msg = bot.send_message(
        chat_id,
        "📅 Выберите дату:",
        reply_markup=calendar_markup,
    )
    logging.debug('FLAG finished sending calendar message')

    # Регистрируем обработчик для ручного ввода
    bot.register_next_step_handler(msg, get_date)


# Обработка ответов по выбору даты


# Чтение даты отправления
@with_command_intercept
def get_date(message):
    logging.debug('FLAG start get_date')
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
def process_get_trains_list(message):
    # For a more responsive feel, send the "typing" action from the main thread.
    # The user already sees "Идёт поиск 🔍", so this might be redundant.
    # bot.send_chat_action(message.chat.id, 'typing')
    # time.sleep(1)

    chat_id = message.chat.id
    logging.debug(f"FLAG start 1 get_trains_list {''}")
    user_info = get_user_data(chat_id)
    if not user_info:
        logging.error(f"No user data for chat_id {chat_id}")
        # Cannot raise an exception in a thread, so just return
        return

    try:
        q_from = quote(user_info["city_from"])
        q_to = quote(user_info["city_to"])
        date = user_info["date"]
    except KeyError as e:
        logging.error(f"Missing key in user data: {e}")
        # Cannot raise, so just return
        return

    # Получение новой страницы "soup"
    url = f"https://pass.rw.by/ru/route/?from={q_from}&to={q_to}&date={date}"

    update_user_data(chat_id, "url", url)
    try:
        r = make_request(url)
        only_span_div_tag = SoupStrainer(["span", "div"])
        soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)

        response_time = r.elapsed.total_seconds()  # время в секундах
        logging.info(
            f"Запрос на сайт \n{get_user_data(chat_id)}"
            f"выполнен за {response_time:.3f} секунд"
        )

    except Exception as e:
        logging.error(f"Server request error in get_trains_list: {e}")
        bot.send_message(
            chat_id, "⚠️ Ошибка запроса на сервер.\nПовторите ввод маршрута"
        )
        # Возвращаемся к началу
        start(message)
        return
    # Добавляет маршрут в БД
    add_route_db(
        user_info["city_from"],
        user_info["city_to"],
        date,
        url,
    )

    # Efficiently parse train data by iterating through train rows once
    train_rows = soup.select("div.sch-table__row")

    if not train_rows:
        bot.send_message(
            chat_id,
            "❓🚆Поезда не найдены.\
                \nПовторите ввод маршрута",
        )
        start(message)
        return

    trains_data = []
    for train_row in train_rows:
        try:
            train_number_tag = train_row.select_one("span.train-number")
            train = train_number_tag.text if train_number_tag else None

            time_depart_tag = train_row.select_one('[data-sort="departure"]')
            time_depart = (
                time_depart_tag.text.strip()
                if time_depart_tag
                else "Нет данных"
            )

            time_arriv_tag = train_row.select_one('[data-sort="arrival"]')
            time_arriv = (
                time_arriv_tag.text.strip() if time_arriv_tag else "Нет данных"
            )

            if train:  # Ensure we have a train number before adding
                trains_data.append(
                    {
                        "train": train,
                        "time_depart": time_depart,
                        "time_arriv": time_arriv,
                    }
                )
        except Exception as e:
            logging.warning(f"Could not parse a train row: {e}")
            continue

    # Add trains to DB in a batch
    if trains_data:
        add_trains_db_batch(trains_data, url)
        # Display the list of trains to the user
        show_train_list(message, url)
    else:
        # This case handles if rows were found but parsing failed for all
        bot.send_message(
            chat_id,
            "❓🚆Не удалось обработать информацию о поездах.\
                \nПовторите ввод маршрута",
        )
        start(message)
        return


def get_trains_list(message):
    # This function is now fast. It just sends a message and starts a thread.
    bot.send_chat_action(message.chat.id, 'typing')
    bot.send_message(message.chat.id, "Идёт поиск 🔍")

    # Start a new thread to handle the slow processing
    thread = threading.Thread(target=process_get_trains_list, args=(message,))
    thread.start()


@ensure_start
def show_train_list(message, url=None):
    chat_id = message.chat.id
    if not url:
        user_info = get_user_data(chat_id)
        try:
            url = user_info["url"]
        except KeyError:
            bot.send_message(
                chat_id,
                "❓Утерян последний маршрут.\
                    \nПовторите ввод маршрута",
            )
            start(message)
            return

    trains_list = get_trains_list_db(url)
    markup = types.InlineKeyboardMarkup()
    # Отображение кнопок выбора поезда из доступного списка

    if trains_list:
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


def process_train_selection(callback):
    """
    Handles the slow logic of train selection in a separate thread.
    """
    train_selected = callback.data.split("_")[0]
    chat_id = callback.message.chat.id

    user_info = get_user_data(chat_id)
    url = user_info['url']
    try:
        r = make_request(url)
        only_span_div_tag = SoupStrainer(["span", "div"])
        soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching train list: {e}")
        bot.send_message(
            chat_id, "⚠️ Ошибка запроса на сервер.\nПовторите ввод маршрута"
        )
        start(callback.message)
        return

    # Get departure datetime for the 15-minute rule
    route_date = user_info.get("date")
    departure_datetime = get_departure_datetime_from_soup(train_selected, soup, route_date)

    # Вывод количества мест по классам или "Мест нет"
    ticket_dict = check_tickets_by_class(train_selected, soup, departure_datetime)

    # Кнопка включения слежения за поездом
    markup = types.InlineKeyboardMarkup()

    # Если 'Без нумерованных мест' возврат на выбор поезда
    if "Без нумерации 🚶‍♂️" in ticket_dict:
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
    elif has_departed(departure_datetime):
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

        ticket_message = ""
        if "status" in ticket_dict:
            ticket_message = ticket_dict["status"]
        else:
            res = ''
            for i in ticket_dict.items():
                res += f'{i[0]}: {i[1]}\n'
            ticket_message = res.strip() if res else "Мест нет"

        bot.send_message(
            chat_id=callback.message.chat.id,
            text=f"🚆 Поезд №{train_selected}\n{ticket_message}",
            reply_markup=markup,
        )


# Выбор конкретного поезда из списка, отображение наличия мест
@bot.callback_query_handler(
    func=lambda callback: callback.data.endswith("_selected")
)
@ensure_start
def select_train(callback):
    # Answer the callback immediately to prevent the "query is too old" error
    bot.answer_callback_query(callback.id)

    # Start a new thread to handle the slow processing
    thread = threading.Thread(target=process_train_selection, args=(callback,))
    thread.start()


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


def background_tracker():
    logging.info("Starting background tracker...")
    while True:
        try:
            due_trackings = get_due_trackings()
            if not due_trackings:
                time.sleep(60)  # Sleep for a minute if no tasks are due
                continue

            logging.info(f"Found {len(due_trackings)} due trackings to check.")

            for tracking in due_trackings:
                (
                    tracking_id,
                    chat_id,
                    train_number,
                    train_id,
                    route_id,
                    url,
                    departure_date,
                    departure_time,
                ) = tracking

                soup = None
                for attempt in range(10):
                    try:
                        r = make_request(url)
                        # Add diagnostic logging
                        logging.debug(f"Response text for URL {url}: {r.text[:500]}")

                        only_span_div_tag = SoupStrainer(["span", "div"])
                        soup = BeautifulSoup(
                            r.text, "lxml", parse_only=only_span_div_tag
                        )
                        # If request is successful, break the loop
                        break
                    except requests.exceptions.RequestException as e:
                        logging.warning(
                            f"Attempt {attempt + 1}/10 failed for URL {url}: {e}. "
                            f"Retrying in {(attempt + 1) * 10} minutes."
                        )
                        if attempt < 9:  # Don't sleep on the last attempt
                            time.sleep((attempt + 1) * 10 * 60)
                    except Exception as e:
                        logging.error(
                            f"An unexpected error occurred while processing tracking_id {tracking_id}: {e}",
                            exc_info=True,
                        )
                        # For unexpected errors, break the loop and reschedule for later
                        soup = None  # Ensure soup is None
                        break

                # If all retries failed, soup will be None
                if soup is None:
                    logging.error(
                        f"Failed to fetch data for {url} after 10 attempts. "
                        "Skipping this check and rescheduling."
                    )

                    # Notify the user about the persistent failure
                    send_message_safely(
                        chat_id,
                        f"⚠️ Не удалось проверить поезд {train_number} после 10 попыток. "
                        "Проверю еще раз через 2 часа. Отслеживание продолжается."
                    )

                    # Reschedule for a much longer time later
                    next_check = datetime.now() + timedelta(hours=2)
                    update_next_check_time(tracking_id, next_check)
                    continue

                # Create a timezone-aware departure datetime
                minsk_tz = pytz.timezone('Europe/Minsk')
                naive_departure = datetime.combine(
                    departure_date,
                    datetime.strptime(departure_time, "%H:%M").time()
                )
                departure_datetime = minsk_tz.localize(naive_departure)

                # Check for changes
                try:
                    fresh_ticket_dict = check_tickets_by_class(train_number, soup, departure_datetime)
                except TrainNotFoundError as e:
                    # This can happen if the site is blocking or the train is gone.
                    # Log it and let the retry loop handle it.
                    logging.warning(
                        f"TrainNotFoundError for train {train_number} on attempt {attempt + 1}/10: {e}. "
                        "Will be retried."
                    )
                    continue

                stored_ticket_dict = get_fresh_loop(chat_id, train_id)

                if fresh_ticket_dict != stored_ticket_dict:
                    markup_url = types.InlineKeyboardMarkup()
                    url_to_ticket = types.InlineKeyboardButton(
                        "На сайт", url=url
                    )
                    markup_url.row(url_to_ticket)

                    ticket_message = ""
                    if "status" in fresh_ticket_dict:
                        ticket_message = fresh_ticket_dict["status"]
                    else:
                        res = ''
                        for i in fresh_ticket_dict.items():
                            res += f'{i[0]}: {i[1]}\n'
                        ticket_message = res.strip() if res else "Места появились, но детали не удалось разобрать."

                    send_message_safely(
                        chat_id,
                        f"Обновление по {train_number}:\n{ticket_message}",
                        reply_markup=markup_url,
                    )

                    # Save the new state to the database
                    json_ticket_dict = json.dumps(fresh_ticket_dict)
                    update_tracking_loop(json_ticket_dict, chat_id, train_id)

                # Check if the train has already departed
                if departure_datetime < datetime.now(pytz.utc):
                    # Train has departed, stop tracking
                    del_tracking_db(chat_id, train_id)
                    send_message_safely(
                        chat_id,
                        f"Отслеживание завершёно по расписанию "
                        f"отправления поезда {train_number}",
                    )
                    logging.info(
                        f"Stopping tracking for train {train_number} "
                        f"for user {chat_id} as it has departed."
                    )
                    continue

                # Calculate next check time based on how far in the future the departure is
                time_until_departure = departure_datetime - datetime.now(pytz.utc)
                hours_until_departure = time_until_departure.total_seconds() / 3600

                delay_minutes = 0
                if hours_until_departure > 36:
                    delay_minutes = randint(40, 60)
                elif 24 <= hours_until_departure <= 36:
                    delay_minutes = randint(20, 40)
                elif 4 <= hours_until_departure < 24:
                    delay_minutes = randint(10, 20)
                else:  # 0 <= hours_until_departure < 4
                    delay_minutes = randint(5, 10)

                next_check_at = datetime.now() + timedelta(
                    minutes=delay_minutes
                )
                update_next_check_time(tracking_id, next_check_at)
                logging.info(
                    f"Rescheduled check for train {train_number} "
                    f"(tracking_id: {tracking_id}) at {next_check_at}"
                )

        except Exception as e:
            logging.error(f"Error in background_tracker main loop: {e}", exc_info=True)
            time.sleep(60)  # Sleep on error to avoid fast error loops


@bot.callback_query_handler(
    func=lambda callback: callback.data.endswith("_start_tracking")
)
@ensure_start
def start_tracking_train(callback):
    bot.answer_callback_query(callback.id)
    train_tracking = callback.data.split("_")[0]
    chat_id = callback.message.chat.id
    url = get_user_data(chat_id).get('url')

    if not url:
        bot.send_message(
            chat_id,
            "Ошибка: URL маршрута не найден. "
            "Пожалуйста, начните заново с /start.",
        )
        return

    try:
        loop_data_list = get_loop_data_list(chat_id, train_tracking, url)

        if not loop_data_list:
            bot.send_message(
                chat_id,
                "⚠️ Ошибка получения данных для отслеживания.\n"
                "Повторите ввод маршрута",
            )
            start(callback.message)
            return

        status_exist = loop_data_list["status_exist"]
        count = loop_data_list["count"]

        if status_exist:
            bot.send_message(
                chat_id,
                f"Отслеживание поезда {train_tracking} уже запущено.",
            )
            return

        if count >= 5:
            bot.send_message(chat_id, "Превышено число отслеживаний (max 5).")
            return

        # Now that checks have passed, show typing indicator
        bot.send_chat_action(chat_id, 'typing')
        time.sleep(1)

        # Fetch ticket info
        r = make_request(url)
        only_span_div_tag = SoupStrainer(["span", "div"])
        soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)

        # Get departure datetime for the 15-minute rule
        route_date = get_user_data(chat_id).get("date")
        departure_datetime = get_departure_datetime_from_soup(train_tracking, soup, route_date)

        ticket_dict = check_tickets_by_class(train_tracking, soup, departure_datetime)

        # Add to tracking list
        add_tracking_db(
            chat_id,
            train_tracking,
            ticket_dict,
            url,
        )

        bot.send_message(
            chat_id, f"Отслеживание поезда {train_tracking} запущено."
        )

    except Exception as e:
        logging.error(f"Error in start_tracking_train: {e}", exc_info=True)
        bot.send_message(
            chat_id, "⚠️ Произошла ошибка при добавлении отслеживания."
        )
        start(callback.message)


# =============================================================================


# Отображение списка отслеживаемых поездов
@bot.message_handler(commands=["show_track_list"])
@ensure_start
def show_track_list(message):

    # Для отображения активности
    bot.send_chat_action(message.chat.id, 'typing')  # Show typing indicator
    time.sleep(1)  # Optional delay

    reply = "Список отслеживания пуст"  # по умолчанию
    track_list = get_track_list(message.chat.id)
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
            # x[4] is already a datetime.date object, no need for strptime
            f_date = x[4].strftime("%d.%m.%y")
            reply_edit.append(
                f"🚆 {x[1]} {x[2]}➡️{x[3]}\n🕒 {x[5]} {f_date} \n{'-'*5}"
            )
        reply = "\n".join(reply_edit)
    bot.reply_to(message, f"{reply}")


# Останов отслеживания конкретного поезда
@bot.message_handler(commands=["stop_track_train"])
@ensure_start
def stop_track_train(message):
    track_list = get_track_list(message.chat.id)
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
            # x[4] is already a datetime.date object, no need for strptime
            f_date = x[4].strftime("%d.%m.%y")
            # Для отображения в сообщении
            reply = f"🚫 {x[1]} {x[2]}➡️{x[3]} 🕒 {x[5]} {f_date}"
            markup.row(
                types.InlineKeyboardButton(
                    f"{reply}",
                    callback_data=(
                        f"{x[0]}:{x[1]}:"
                        f"{x[4].strftime('%Y-%m-%d')}_stop_tracking"
                    ),
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
        stop_tracking_by_id_db(tracking_id)
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


# ============================================================================
# Вспомогательные функции


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


# ============================================================================
# Фоновые задачи


# Удаление прошедших маршрутов из таблицы routes
def cleanup_expired_routes():
    while True:
        try:
            logging.info("Starting expired routes cleanup...")
            cleanup_expired_routes_db()
            logging.info("Finished expired routes cleanup.")
        except Exception as e:
            logging.error(f"Error in cleanup_expired_routes: {e}", exc_info=True)
        # Проверяем каждые 2 часа
        time.sleep(2 * 60 * 60)


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
        daemon=True,
    )
    cleanup_thread.start()

    # Отслеживание работающих потоков каждый час
    monitor_threads = threading.Thread(
        target=monitor_threads_track,
        name="monitor_threads",
        daemon=True,
    )
    monitor_threads.start()

    # Запуск фонового отслеживания
    tracker_thread = threading.Thread(
        target=background_tracker,
        name="background_tracker",
        daemon=True,
    )
    tracker_thread.start()


# =============================================================================
# Админ-команды


@bot.message_handler(commands=["cleanup"])
def manual_cleanup(message):
    """
    Manually triggers the cleanup of expired routes.
    Only accessible by the admin.
    """
    if message.chat.id == settings.ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "🧹 Запускаю очистку...")
        try:
            cleanup_expired_routes_db()
            bot.send_message(message.chat.id, "✅ Очистка завершена.")
        except Exception as e:
            logging.error(f"Manual cleanup failed: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ Ошибка во время очистки.")
    else:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для этой команды.")


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

    stop_all_tracking_for_user_db(chat_id)
    del_user_data(chat_id)
    bot.send_message(chat_id, "🛑 Бот остановлен")


# Выход из программы
@bot.message_handler(commands=[settings.STOP_CODE])  # type: ignore
def exit_admin(message):
    chat_id = message.chat.id

    stop_all_tracking_for_user_db(chat_id)

    bot.send_message(chat_id, "Выход из ПО")

    def stop_bot():
        bot.stop_polling()
        os._exit(0)  # Принудительный выход

    threading.Thread(target=stop_bot).start()


# =============================================================================
# Запуск бота в режиме непрерывной работы
# При работе через gunicorn:
# main.py не будет выполнять __main__-блок,
#  потому что Gunicorn просто импортирует app.
# Чтобы всё сработало:
# webhook должен быть установлен заранее
# фоновые задачи нужно запускать внутри @app.on_event("startup")
# if __name__ == "__main__":
#     # Запуск существующих отслеживаний
#     restore_all_trackings()
#     # Проверка устаревших маршрутов и отслеживание потоков
#     start_background_tasks()

#     try:
#         try:
#             bot.remove_webhook()  # Попытка удалить существующий webhook
#             time.sleep(2)  # Пауза для обработки запроса сервером Telegram
#             success = bot.set_webhook(url=f"{webhook_url}/{token}")
#             if success:
#                 logging.info(f"Webhook установлен: {webhook_url}")
#             else:
#                 logging.error("Ошибка установки webhook")
#             # app.run(host='0.0.0.0', port=web_port) # Для разработки
#             # Для деплоя запускается через Gunicorn

#         except apihelper.ApiTelegramException as e:
#             # Игнорирование ошибки "webhook не установлен"
#             if "webhook is not set" not in str(e):
#                 logging.error(f"Webhook deletion failed: {e}")
#             else:
#                 raise  # Проброс других ошибок API

#         # Ошибка запроса
#     except requests.exceptions.ReadTimeout as e:
#         logging.error(f"Timeout error: {e}.")

#     # Остальные ошибки
#     except Exception as e:
#         logging.error(f"Attempt failed: {str(e)}")


def initialize_app():
    global app_initialized
    if app_initialized:
        return
    app_initialized = True

    logging.info("🔧 Инициализация приложения")
    # Create database tables if they don't exist
    create_tables()

    try:
        # Проверка вебхука для разных воркеров
        webhook_info = bot.get_webhook_info()
        if webhook_info.url != f"{settings.WEBHOOK_URL}/{settings.TOKEN}":

            bot.remove_webhook()
            time.sleep(5)
            success = bot.set_webhook(
                url=f"{settings.WEBHOOK_URL}/{settings.TOKEN}"
            )
            if success:
                logging.info(f"✅ Webhook установлен: {settings.WEBHOOK_URL}")
            else:
                logging.error("❌ Ошибка установки webhook")

    except apihelper.ApiTelegramException as e:
        if "webhook is not set" not in str(e):
            logging.error(f"Webhook deletion failed: {e}")
        else:
            raise
    except requests.exceptions.ReadTimeout as e:
        logging.error(f"Timeout error: {e}.")
    except Exception as e:
        logging.error(f"Unexpected error during init: {e}")

    start_background_tasks()


initialize_app()
