import requests
from bs4.filter import SoupStrainer
from bs4 import BeautifulSoup, StopParsing
import lxml
from urllib.parse import quote
import time
import json
import sys
# импорт для бота
from token_info import token, bot_name
import telebot
import webbrowser
from telebot import types
#библеотека для параллельных потоков
import threading
#список станций
from all_stations_list import all_station_list
from datetime import datetime
# словарь соответствия номер-название класса
seats_type_dict = {
    '0': 'Без нумерации мест',
    '1': 'Общий',
    '2': 'Сидячий',
    '3': 'Плацкартный',
    '4': 'Купейный',
    '5': 'Мягкий',
    '6': 'СВ',
}


#-------------------------------
# В начале кода создаем словарь для хранения данных
user_data = {}  # Ключ - chat_id, значение - словарь с данными

# ========================
# Декоратор: Проверка старта для избежания ошибок
# ========================
def ensure_start(func):
    def wrapper(message):
        try:
            chat_id = message.chat.id
        except AttributeError:
            chat_id = message.message.chat.id #если проверяется callback
        if chat_id not in user_data:
            bot.send_message(chat_id, "Сначала введите /start")
            return
        return func(message)
    return wrapper

#Подключение бота для ввода данных


#Создаётся объект бота, который умеет принимать сообщения от Telegram.
bot = telebot.TeleBot(token)
@bot.message_handler(commands=['start'])
def start(message):
    # приветствие и переход на получение данных
    bot.send_message(message.chat.id, 'Город отправления: ')
    #вызов следующей функции для города отправления
    bot.register_next_step_handler(message, get_city_from)
    user_data[message.chat.id] = {'step': 'start'}
    
def get_city_from(message):
    
    city_from = normalize_city_name(message.text)
    if city_from not in all_station_list:
        bot.send_message(message.chat.id, 'Неправильное название станции отправления')
        bot.register_next_step_handler(message, get_city_from)  # Возвращаемся к началу
        return
    user_data[message.chat.id].update({'city_from': city_from})
    bot.send_message(message.chat.id, 'Город прибытия: ')
    #вызов следующей функции для города прибытия
    bot.register_next_step_handler(message, get_city_to)

def get_city_to(message):
    
    city_to = normalize_city_name(message.text)
    if city_to not in all_station_list:
        bot.send_message(message.chat.id, 'Неправильное название станции назначения')
        bot.register_next_step_handler(message, get_city_to)  # Возвращаемся к началу
        return
    user_data[message.chat.id].update({'city_to': city_to})
    bot.send_message(message.chat.id, 'Дата в формате гггг-мм-дд: ')
    #вызов следующей функции для города даты
    bot.register_next_step_handler(message, get_date)

def get_date(message):
    try:
        date = normalize_date(message.text)
        user_data[message.chat.id].update({'date': date})
        get_trains_list(message)
    except ValueError as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}. Повторите ввод даты:")
        bot.register_next_step_handler(message, get_date)
        return

#функция получения поездов по направлению
def get_trains_list(message):

    encoded_from = quote(user_data[message.chat.id]['city_from'])
    encoded_to = quote(user_data[message.chat.id]['city_to'])
    date = user_data[message.chat.id]['date']
    #получение новой страницы soup

    url = f'https://pass.rw.by/ru/route/?from={encoded_from}&to={encoded_to}&date={date}'
    user_data[message.chat.id]['url'] = url
    try:
        r = requests.get(url)
    except Exception as e:
        error_msg = f"Ошибка: {str(e)}\nДавайте начнем заново."
        bot.send_message(message.chat.id, error_msg)
        start(message)  # Возвращаемся к началу

    #на время тестов обращение к файлу Минск-Брест 2025-04-25
    # with open('test_rw_by.html', 'r+') as f:
    #     r = f.read()

    only_span_div_tag = SoupStrainer(['span', 'div'])
    soup = BeautifulSoup(r.text, 'lxml', parse_only=only_span_div_tag) #вернуть r.text

    #добавление в сессию
    user_data[message.chat.id]['soup'] = soup

    train_id_list = [i.text for i in soup.find_all('span', class_="train-number")]
    
    trains_list = []
    # получение времени отправления и прибытия
    for train in train_id_list:
        try:
            time_depart = soup.select(f'[data-train-number^="{train}"] [data-sort="departure"]')[0].text.strip()
            time_arriv = soup.select(f'[data-train-number^="{train}"] [data-sort="arrival"]')[0].text.strip()
        except Exception as e:
            time_depart, time_arriv = ('Нет данных', 'Нет данных',)
        trains_list.append([train, time_depart, time_arriv])
    
    #получение списка доступных поездов
    markup = types.InlineKeyboardMarkup()
    #отображение кнопок выбора поезда
    marker = 'selected_train'
    for train in trains_list:
        markup.row(types.InlineKeyboardButton(
            f'Поезд: {train[0]} Отпр: {train[1]} Приб: {train[2]}', 
            callback_data= f'{train[0]}_selected')
            )
    #вместо глобальной soup используем пользовательские данные бота

    bot.send_message(message.chat.id, "Список доступных поездов: ", reply_markup=markup)

#выбор поезда из списка (добавка через _, чтобы разделить реакцию на ответы)
@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_selected'))
@ensure_start 
def select_train(callback): # callback == все данные ответа
    train_selected = callback.data.split('_')[0]
    chat_id = callback.message.chat.id
    # получаем из сессии здесь, т.к. дальше не передаётся объект message
    soup = user_data[chat_id]['soup']

    #вывод количества мест по классам или "Мест нет"
    ticket_dict = check_tickets_by_class(train_selected, soup, chat_id)
    
    #добавляем в список поездов, но здесь статус отслеживания пока что False
    #здесь, т.к. необходимо получить список мест для контроля изменений
    if 'tracking_active' not in user_data[chat_id]:
        user_data[chat_id]['tracking_active'] = {}

    user_data[chat_id]['tracking_active'][train_selected] = {
                'status': False,
                'ticket_dict': ticket_dict,
        }


    #необходимо, чтобы убрать "часики" ожидания
    # показывает всплывающее окно, если описать сообщение
    bot.answer_callback_query(callback.id)
   
    #кнопка включения слежения за поездом
    markup = types.InlineKeyboardMarkup()
    
    #если 'Без нумерованных мест' возврат на выбор поезда
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
#обработка возврата к списку поездов, если поезд без нумерации мест
@bot.callback_query_handler(func=lambda callback: callback.data == 're_get_trains_list')
@ensure_start 
def re_get_trains_list(callback):
    get_trains_list(callback.message)
    pass

#включение отслеживания, добавление поезда в лист слежения
@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_start_tracking'))
@ensure_start 
def start_tracking_train(callback): 

    train_tracking = callback.data.split('_')[0]
    chat_id = callback.message.chat.id
    
    # регистрация поезда в списке отслеживания
    user_data[chat_id]['tracking_active'][train_tracking]['status'] = True

    #запуск отслеживания в параллельном потоке
    #лучше передавать аргументы, а не использовать внешние
    def tracking_loop(chat_id, train_tracking):
        #проверка, что отслеживание поезда активно
        try:
            while True:
                
                tracking_data = user_data.get(chat_id, {}).get('tracking_active', {}).get(train_tracking)

                #проверка, что данные существуют и отслеживаются активно
                if not tracking_data or not tracking_data.get('status'):
                    print(f"[thread exit] Поток завершён: {train_tracking} для {chat_id}")
                    return
                
                #получение новой страницы soup
                try:
                    user_data[chat_id ]['url']
                    r = requests.get(user_data[chat_id ]['url'])
                except Exception as e:
                    error_msg = f"Ошибка: {str(e)}\nДавайте начнем заново."
                    bot.send_message(chat_id, error_msg)
                    start(chat_id)  # Возвращаемся к началу

                #на время тестов обращение к файлу Минск-Брест 2025-04-25
                # with open('test_rw_by.html', 'r+') as f:
                #     r = f.read()

                only_span_div_tag = SoupStrainer(['span', 'div'])
                soup = BeautifulSoup(r.text, 'lxml', parse_only=only_span_div_tag) #вернуть r.text

                #добавление в сессию
                user_data[chat_id]['soup'] = soup

                #получение более свежей информации по билетам
                ticket_dict = check_tickets_by_class(train_tracking, soup, chat_id)
                
                #выводить сообщение при появлении изменений в билетах
                if ticket_dict != tracking_data.get('ticket_dict'):
                    bot.send_message(chat_id, f'Обновление по {train_tracking}: {ticket_dict}')
                    tracking_data['ticket_dict'] = ticket_dict
                
                #отслеживание активных потоков для отладки
                print("⚙️ Активные потоки:")
                for thread in threading.enumerate():
                    print(f"  🔸 {thread.name} (ID: {thread.ident})")

                time.sleep(15)
        
        except Exception as e:
            print(f"[thread error] {chat_id}, {train_tracking}: {str(e)}")
    
    #регистрация и запуск параллельного потока с заданным именем и аргументами, 
    #чтобы не быть в ситуации, когда функция запустится через секунду-другую, 
    # а к этому времени переменные уже будут другими. 
    # Например, другой пользователь вызовет бота, и chat_id перезапишется, 
    # а старый поток будет отслеживать не того юзера.
    thread = threading.Thread(
        target=tracking_loop, 
        args=(chat_id, train_tracking), 
        name=f"tracking_{train_tracking}_{chat_id}"
        )
    
    thread.start()
    bot.send_message(chat_id, f'Отслеживание поезда {train_tracking} запущено.')

#отдельная функция для списка отслеживаемых поездов, т.к. используется
#для команд Отображения и Останова
def get_track_list(message):
    track_list = []
    if user_data[message.chat.id].get('tracking_active', False): 
        for train, info in user_data[message.chat.id]['tracking_active'].items():
            if info['status']:
                track_list.append(train)
        if track_list:
            reply = '\n'.join(track_list) 
    return track_list #для функции удаления из списка отслеживания

#отображение списка отслеживаемых поездов
@bot.message_handler(commands=['show_track_list'])
@ensure_start
def show_track_list(message):

    reply = 'Список отслеживания пуст' #по умолчанию
    track_list = get_track_list(message)
    if track_list:
        reply = '\n'.join(track_list) 
    bot.reply_to(message, f'{reply}')

#останов отслеживания конкретного поезда

@bot.message_handler(commands=['stop_track_train'])
@ensure_start
def stop_track_train(message):
    track_list =  get_track_list(message)
    if track_list:
        markup = types.InlineKeyboardMarkup()
        for train in track_list:
            markup.row(types.InlineKeyboardButton(
                f'Остановить отслеживание поезда: {train}', 
                callback_data= f'{train}_stop_tracking')
                )
        bot.reply_to(message, "Список отслеживания: ", reply_markup=markup)
    else:
        bot.reply_to(message, 'Список отслеживания пуст')

#функция удаления поезда из списка отслеживания
@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_stop_tracking')) 
@ensure_start
def stop_tracking_train_by_number(callback):
    train_stop_tracking = callback.data.split('_')[0]
    chat_id = callback.message.chat.id

    user_data[chat_id]['tracking_active'][train_stop_tracking]['status'] = False

    bot.send_message(chat_id, f'Отслеживание поезда {train_stop_tracking} остановлено.')

#-------------------------------------

#нормализация ввода города
def normalize_city_name(name):
    return name.strip().lower().capitalize()

#нормализация ввода даты с контролем "сегодня и далее"
def normalize_date(date_str):
    formats = ['%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d %m %Y',
               '%Y %m %d']
    today = datetime.today().date()
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            #"сегодня и далее":
            if dt.date() < today:
                raise ValueError("Введена прошедшая дата")
            return dt.strftime('%Y-%m-%d')  # возвращаем нормализованный формат
        except ValueError:
            continue  # пробуем следующий формат

    # Если ни один формат не подошёл и дата в прошлом:
    raise ValueError(f"Неверный формат даты. Примеры: {today.strftime('%Y-%m-%d')}, {today.strftime('%d %m %Y')}")

    
#проверка наличия места
def check_tickets_by_class(train_number, soup, chat_id):
    train_info = soup.select(f'div.sch-table__row[data-train-number^="{train_number}"]')
    selling_allowed = train_info[0]['data-ticket_selling_allowed']
    if selling_allowed == 'true':
        return get_tickets_by_class(train_number, soup)
    elif selling_allowed == 'false':
        return 'Мест нет'
    else:
        return 'Ошибка получения информации о поезде'

#получение количества мест
def get_tickets_by_class(train_number, soup):

    # информация о наличии мест и классов вагонов
    train_info = soup.select(f'div.sch-table__row[data-train-number^="{train_number}"]')
    # доступные классы вагонов и места
    class_names = train_info[0].find_all(class_="sch-table__t-quant js-train-modal dash")
    # вывод словаря с заменой номера на имя класса обслуживания
    # и общего количества мест для каждого класса
    tickets_by_class = {}
    for class_n in class_names:
        name = seats_type_dict[class_n['data-car-type']] # type: ignore
        try:
            seats_num = int(class_n.select_one('span').text) # type: ignore
        except ValueError:
            seats_num = 'Без нумерации мест'
            tickets_by_class[name] = '\u221e'
            continue
        if name in tickets_by_class:
            tickets_by_class[name] += seats_num
        else:
            tickets_by_class[name] = seats_num
    return tickets_by_class


#останов сессии для юзера
@bot.message_handler(commands=['stop'])
@ensure_start
def stop(message):
    chat_id = message.chat.id
    #для остановки параллельного потока необходимо перевести статус для 
    #всех поездов в False
    if chat_id in user_data and 'tracking_active' in user_data[chat_id]:
        for train in user_data[chat_id]['tracking_active']:
            user_data[chat_id]['tracking_active'][train]['status'] = False
    #после остановки поездов, удалить всю сессию
    user_data.pop(chat_id, None)
    bot.send_message(chat_id, 'Бот остановлен. Список отслеживания очищен ')


#выход из программы
@bot.message_handler(commands=['1765362'])
def exit_admin(message):
    del user_data[message.chat.id] 
    bot.send_message(message.chat.id, 'Выход из ПО')
    bot.stop_polling()
    sys.exit()

#для постоянной работы:
bot.polling(non_stop=True)
