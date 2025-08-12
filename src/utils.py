import calendar
import logging
from datetime import datetime, timedelta

import requests
from telebot import types

from all_stations_list import all_station_list, all_station_list_lower

# from src.config import settings
from src.database import get_departure_date_db


class PastDateError(ValueError):
    pass


class FutureDateError(ValueError):
    pass


class SiteResponseError(Exception):
    pass


seats_type_dict = {
    "0": "Без нумерации 🚶‍♂️",
    "1": "Общий 🚃",
    "2": "Сидячий 💺",
    "3": "Плацкартный 🛏️",
    "4": "Купейный 🚪🛏️",
    "5": "Мягкий 🛋️",
    "6": "СВ 👑",
}


def normalize_city_name(name):
    logging.debug(f"Flag normalize_city_name {name}")
    name = name.strip().lower()
    try:
        index = all_station_list_lower.index(name)
        name = all_station_list[index]
    except Exception:
        name = name.capitalize()
    return name


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


def make_request(url):
    """Creates a requests session and makes a GET request."""
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
    r = session.get(url, timeout=30)
    r.raise_for_status() # Raise an exception for bad status codes
    return r


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

    depart_time = get_departure_date_db(train_id) if train_id else None
    today = datetime.today().date()

    if not train_info and depart_time and (depart_time >= today):
        raise SiteResponseError('Ошибка получения данных поезда с сайта')
    elif not train_info and depart_time and depart_time < today:
        result = 0
    elif not train_info:
        result = 0
    else:
        result = int(train_info[0]["data-value"])
    return result
