import calendar
import logging
from datetime import datetime, timedelta
import pytz

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


def check_tickets_by_class(train_number, soup, departure_datetime=None):
    train_info = soup.select(
        f'div.sch-table__row[data-train-number^="{train_number}"]'
    )
    if not train_info:
        return {"status": "Ошибка получения информации о поезде"}

    selling_allowed = train_info[0].get("data-ticket_selling_allowed")

    no_seats_status = {"status": "Мест нет"}
    if departure_datetime:
        time_diff = departure_datetime - datetime.now(pytz.utc)
        if time_diff < timedelta(minutes=15):
            no_seats_status = {"status": "Продажа онлайн закрыта"}

    if selling_allowed == "true":
        tickets = get_tickets_by_class(train_info)
        if not tickets:  # If get_tickets_by_class returns empty, it's also a "no seats" case
            return no_seats_status
        return tickets
    elif selling_allowed == "false":
        return no_seats_status
    else:
        # This case might occur if the attribute is missing
        return {"status": "Ошибка получения информации о поезде"}


def get_tickets_by_class(train_info):
    # Dозвращает словарь с классами вагонов и количеством мест
    if not train_info:
        return {}

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
    r.raise_for_status()  # Raise an exception for bad status codes
    return r


def get_departure_datetime_from_soup(train_number, soup, route_date):
    """Parses departure time from soup and combines with date to return a datetime object."""
    try:
        train_info = soup.select_one(
            f'div.sch-table__row[data-train-number^="{train_number}"]'
        )
        if not train_info:
            return None

        time_str = train_info.select_one('div.sch-table__time.train-from-time').text.strip()
        departure_time = datetime.strptime(time_str, "%H:%M").time()

        # The date from the user session is a string 'YYYY-MM-DD'
        if isinstance(route_date, str):
            route_date = datetime.strptime(route_date, "%Y-%m-%d").date()

        return datetime.combine(route_date, departure_time)
    except (AttributeError, ValueError) as e:
        logging.warning(f"Could not parse departure datetime for train {train_number} from soup: {e}")
        return None


def has_departed(departure_datetime):
    """
    Checks if a timezone-aware departure datetime is in the past.
    Returns True if the train has departed, False otherwise.
    """
    if not departure_datetime:
        # If we don't have a departure time, assume it hasn't departed
        # to prevent accidentally removing it.
        return False
    return departure_datetime < datetime.now(pytz.utc)
