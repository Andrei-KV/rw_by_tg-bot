import json
import sys
import threading
import time
import webbrowser
from datetime import datetime
from urllib.parse import quote

import requests
import telebot
from bs4 import BeautifulSoup, SoupStrainer
from telebot import types
from token_info import bot_name, token

from all_stations_list import all_station_list

# =============================================
# Словарь соответствия номер -> название класса
# =============================================
seats_type_dict = {
    '0': 'Без нумерации мест',
    '1': 'Общий',
    '2': 'Сидячий',
    '3': 'Плацкартный',
    '4': 'Купейный',
    '5': 'Мягкий',
    '6': 'СВ',
}

# =============================================
# Глобальное хранилище пользовательских сессий
# =============================================
user_data = {}  # Ключ — chat_id, значение — словарь с параметрами

# =============================================
# Создание Telegram-бота
# =============================================
bot = telebot.TeleBot(token)


# ========================
# Декоратор: Проверка старта
# ========================
def ensure_start(func):
    def wrapper(message):
        try:
            chat_id = message.chat.id
        except AttributeError:
            chat_id = message.message.chat.id  # при callback
        if chat_id not in user_data:
            bot.send_message(chat_id, "Сначала введите /start")
            return
        return func(message)
    return wrapper


# ========================
# Стартовая команда /start
# ========================
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, 'Город отправления: ')
    bot.register_next_step_handler(message, get_city_from)
    user_data[message.chat.id] = {'step': 'start'}


# ============================================
# Получение и проверка станции отправления
# ============================================
def get_city_from(message):
    city_from = normalize_city_name(message.text)
    if city_from not in all_station_list:
        bot.send_message(message.chat.id, 'Неправильное название станции отправления')
        bot.register_next_step_handler(message, get_city_from)
        return
    user_data[message.chat.id].update({'city_from': city_from})
    bot.send_message(message.chat.id, 'Город прибытия: ')
    bot.register_next_step_handler(message, get_city_to)


# ============================================
# Получение и проверка станции назначения
# ============================================
def get_city_to(message):
    city_to = normalize_city_name(message.text)
    if city_to not in all_station_list:
        bot.send_message(message.chat.id, 'Неправильное название станции назначения')
        bot.register_next_step_handler(message, get_city_to)
        return
    user_data[message.chat.id].update({'city_to': city_to})
    bot.send_message(message.chat.id, 'Дата в формате гггг-мм-дд: ')
    bot.register_next_step_handler(message, get_date)


# ============================================
# Получение и нормализация даты
# ============================================
def get_date(message):
    try:
        date = normalize_date(message.text)
        user_data[message.chat.id].update({'date': date})
        get_trains_list(message)
    except ValueError as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}. Повторите ввод даты:")
        bot.register_next_step_handler(message, get_date)
        return


# ========================================================
# Основная функция — получение списка поездов с сайта
# ========================================================
def get_trains_list(message):
    encoded_from = quote(user_data[message.chat.id]['city_from'])
    encoded_to = quote(user_data[message.chat.id]['city_to'])
    date = user_data[message.chat.id]['date']

    url = f'https://pass.rw.by/ru/route/?from={encoded_from}&to={encoded_to}&date={date}'
    user_data[message.chat.id]['url'] = url

    try:
        r = requests.get(url)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {str(e)}\nДавайте начнем заново.")
        start(message)
        return

    # Парсинг только нужных тегов для экономии ресурсов
    only_span_div_tag = SoupStrainer(['span', 'div'])
    soup = BeautifulSoup(r.text, 'lxml', parse_only=only_span_div_tag)
    user_data[message.chat.id]['soup'] = soup

    train_id_list = [i.text for i in soup.find_all('span', class_="train-number")]
    trains_list = []

    # Сбор времени отправления и прибытия для каждого поезда
    for train in train_id_list:
        try:
            time_depart = soup.select(f'[data-train-number^="{train}"] [data-sort="departure"]')[0].text.strip()
            time_arriv = soup.select(f'[data-train-number^="{train}"] [data-sort="arrival"]')[0].text.strip()
        except Exception:
            time_depart, time_arriv = 'Нет данных', 'Нет данных'
        trains_list.append([train, time_depart, time_arriv])

    # Формирование интерфейса с кнопками
    markup = types.InlineKeyboardMarkup()
    for train in trains_list:
        markup.row(types.InlineKeyboardButton(
            f'Поезд: {train[0]} Отпр: {train[1]} Приб: {train[2]}',
            callback_data=f'{train[0]}_selected')
        )
    bot.send_message(message.chat.id, "Список доступных поездов: ", reply_markup=markup)


# ========================
# Обработка выбора поезда
# ========================
@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_selected'))
@ensure_start
def select_train(callback):
    train_selected = callback.data.split('_')[0]
    chat_id = callback.message.chat.id
    soup = user_data[chat_id]['soup']

    ticket_dict = check_tickets_by_class(train_selected, soup, chat_id)

    if 'tracking_active' not in user_data[chat_id]:
        user_data[chat_id]['tracking_active'] = {}

    user_data[chat_id]['tracking_active'][train_selected] = {
        'status': False,
        'ticket_dict': ticket_dict,
    }

    bot.answer_callback_query(callback.id)

    # Кнопка для отслеживания
    markup = types.InlineKeyboardMarkup()
    if seats_type_dict['0'] in ticket_dict:
        btn_track = types.InlineKeyboardButton(
            'Отслеживание недоступно.\nВернуться к списку поездов',
            callback_data='re_get_trains_list'
        )
    else:
        btn_track = types.InlineKeyboardButton(
            'Начать отслеживание',
            callback_data=f'{train_selected}_start_tracking'
        )
    markup.add(btn_track)

    bot.send_message(
        chat_id=callback.message.chat.id,
        text=f'Поезд №{train_selected}\n{ticket_dict}',
        reply_markup=markup
    )


# ================================
# Возврат к списку поездов
# ================================
@bot.callback_query_handler(func=lambda callback: callback.data == 're_get_trains_list')
@ensure_start
def re_get_trains_list(callback):
    get_trains_list(callback.message)


# ================================
# Запуск отслеживания
# ================================
@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_start_tracking'))
@ensure_start
def start_tracking_train(callback):
    train_tracking = callback.data.split('_')[0]
    chat_id = callback.message.chat.id

    user_data[chat_id]['tracking_active'][train_tracking]['status'] = True

    def tracking_loop(chat_id, train_tracking):
        try:
            while True:
                tracking_data = user_data.get(chat_id, {}).get('tracking_active', {}).get(train_tracking)
                if not tracking_data or not tracking_data.get('status'):
                    print(f"[thread exit] Поток завершён: {train_tracking} для {chat_id}")
                    return

                r = requests.get(user_data[chat_id]['url'])
                soup = BeautifulSoup(r.text, 'lxml', parse_only=SoupStrainer(['span', 'div']))
                user_data[chat_id]['soup'] = soup

                ticket_dict = check_tickets_by_class(train_tracking, soup, chat_id)
                if ticket_dict != tracking_data.get('ticket_dict'):
                    bot.send_message(chat_id, f'Обновление по {train_tracking}: {ticket_dict}')
                    tracking_data['ticket_dict'] = ticket_dict

                print("⚙️ Активные потоки:")
                for thread in threading.enumerate():
                    print(f"  🔸 {thread.name} (ID: {thread.ident})")

                time.sleep(15)

        except Exception as e:
            print(f"[thread error] {chat_id}, {train_tracking}: {str(e)}")

    thread = threading.Thread(
        target=tracking_loop,
        args=(chat_id, train_tracking),
        name=f"tracking_{train_tracking}_{chat_id}"
    )
    thread.start()
    bot.send_message(chat_id, f'Отслеживание поезда {train_tracking} запущено.')


# ========================
# Показать список трекинга
# ========================
@bot.message_handler(commands=['show_track_list'])
@ensure_start
def show_track_list(message):
    track_list = get_track_list(message)
    reply = '\n'.join(track_list) if track_list else 'Список отслеживания пуст'
    bot.reply_to(message, reply)


def get_track_list(message):
    track_list = []
    if user_data[message.chat.id].get('tracking_active'):
        for train, info in user_data[message.chat.id]['tracking_active'].items():
            if info['status']:
                track_list.append(train)
    return track_list


# ========================
# Остановка отслеживания
# ========================
@bot.message_handler(commands=['stop_track_train'])
@ensure_start
def stop_track_train(message):
    track_list = get_track_list(message)
    if track_list:
        markup = types.InlineKeyboardMarkup()
        for train in track_list:
            markup.row(types.InlineKeyboardButton(
                f'Остановить отслеживание поезда: {train}',
                callback_data=f'{train}_stop_tracking')
            )
        bot.reply_to(message, "Список отслеживания: ", reply_markup=markup)
    else:
        bot.reply_to(message, 'Список отслеживания пуст')


@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_stop_tracking'))
@ensure_start
def stop_tracking_train_by_number(callback):
    train = callback.data.split('_')[0]
    chat_id = callback.message.chat.id
    user_data[chat_id]['tracking_active'][train]['status'] = False
    bot.send_message(chat_id, f'Отслеживание поезда {train} остановлено.')


# ========================
# Стоп всей сессии
# ========================
@bot.message_handler(commands=['stop'])
@ensure_start
def stop(message):
    chat_id = message.chat.id
    if chat_id in user_data and 'tracking_active' in user_data[chat_id]:
        for train in user_data[chat_id]['tracking_active']:
            user_data[chat_id]['tracking_active'][train]['status'] = False
    user_data.pop(chat_id, None)
    bot.send_message(chat_id, 'Бот остановлен. Список отслеживания очищен')


# ========================
# Выход (для админа)
# ========================
@bot.message_handler(commands=['1765362'])
def exit_admin(message):
    del user_data[message.chat.id]
    bot.send_message(message.chat.id, 'Выход из ПО')
    bot.stop_polling()
    sys.exit()


# ========================
# Утилиты
# ========================
def normalize_city_name(name):
    return name.strip().lower().capitalize()


def normalize_date(date_str):
    formats = ['%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d %m %Y', '%Y %m %d']
    today = datetime.today().date()
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.date() < today:
                raise ValueError("Введена прошедшая дата")
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    raise ValueError(f"Неверный формат даты. Примеры: {today.strftime('%Y-%m-%d')}, {today.strftime('%d %m %Y')}")


def check_tickets_by_class(train_number, soup, chat_id):
    train_info = soup.select(f'div.sch-table__row[data-train-number^="{train_number}"]')
    selling_allowed = train_info[0]['data-ticket_selling_allowed']
    if selling_allowed == 'true':
        return get_tickets_by_class(train_number, soup)
    elif selling_allowed == 'false':
        return 'Мест нет'
    else:
        return 'Ошибка получения информации о поезде'


def get_tickets_by_class(train_number, soup):
    train_info = soup.select(f'div.sch-table__row[data-train-number^="{train_number}"]')
    class_names = train_info[0].find_all(class_="sch-table__t-quant js-train-modal dash")
    tickets_by_class = {}
    for class_n in class_names:
        name = seats_type_dict[class_n['data-car-type']]
        try:
            seats_num = int(class_n.select_one('span').text)
        except ValueError:
            tickets_by_class[name] = '\u221e'
            continue
        if name in tickets_by_class:
            tickets_by_class[name] += seats_num
        else:
            tickets_by_class[name] = seats_num
    return tickets_by_class


# ========================
# Запуск бота
# ========================
bot.polling(non_stop=True)
