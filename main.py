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

# словарь соответствия номер-название класса
seats_type_dict = {
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
    
    city_from = message.text.strip().lower().capitalize()
    user_data[message.chat.id].update({'city_from': city_from})
    bot.send_message(message.chat.id, 'Город прибытия: ')
    #вызов следующей функции для города прибытия
    bot.register_next_step_handler(message, get_city_to)

def get_city_to(message):
    
    city_to = message.text.strip().lower().capitalize()
    user_data[message.chat.id].update({'city_to': city_to})
    bot.send_message(message.chat.id, 'Дата в формате гггг-мм-дд: ')
    #вызов следующей функции для города даты
    bot.register_next_step_handler(message, get_date)

def get_date(message):
    
    date = message.text.strip()
    user_data[message.chat.id].update({'date': date})
    # переход на получение списка доступных поездов
    try:
        get_trains_list(message)
    except Exception as e:
        error_msg = f"Ошибка: {str(e)}\nДавайте начнем заново."
        bot.send_message(message.chat.id, error_msg)
        start(message)  # Возвращаемся к началу

#функция получения поездов по направлению
def get_trains_list(message):

    encoded_from = quote(user_data[message.chat.id]['city_from'])
    encoded_to = quote(user_data[message.chat.id]['city_to'])

    #получение новой страницы soup

    # url = f'https://pass.rw.by/ru/route/?from={encoded_from}&to={encoded_to}&date={date}'
    # user_data[message.chat.id]['url'] = url
    # r = requests.get(url)

    #на время тестов обращение к файлу Минск-Брест 2025-04-25
    with open('test_rw_by.html', 'r+') as f:
        r = f.read()

    only_span_div_tag = SoupStrainer(['span', 'div'])
    soup = BeautifulSoup(r, 'lxml', parse_only=only_span_div_tag) #вернуть r.text

    #добавление в сессию
    user_data[message.chat.id]['soup'] = soup

    train_id_list = [i.text for i in soup.find_all('span', class_="train-number")] 
    trains_list = []
    # получение времени отправления и прибытия
    for train in train_id_list:
        time_depart = soup.select(f'[data-train-number="{train}"] [data-sort="departure"]')[0].text.strip()
        time_arriv = soup.select(f'[data-train-number="{train}"] [data-sort="arrival"]')[0].text.strip()
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
def select_train(callback): # callback == все данные ответа
    train_selected = callback.data.split('_')[0]

    # получаем из сессии здесь, т.к. дальше не передаётся объект message
    soup = user_data[callback.message.chat.id]['soup']

    #вывод количества мест по классам или "Мест нет"
    ticket_dict = check_tickets_by_class(train_selected, soup)
    
    #необходимо, чтобы убрать "часики" ожидания
    # показывает всплывающее окно, если описать сообщение
    bot.answer_callback_query(callback.id)
   
    #кнопка включения слежения за поездом
    markup = types.InlineKeyboardMarkup()
    btn_track = types.InlineKeyboardButton('Начать отслеживание', callback_data=f'{train_selected}_tracking')
    markup.add(btn_track)
    
    bot.send_message(
        chat_id=callback.message.chat.id,
        text=f'Поезд №{train_selected}\n{ticket_dict}',
        reply_markup=markup
    )

#включение отслеживания, добавление поезда в лист слежения
@bot.callback_query_handler(func=lambda callback: callback.data.endswith('_tracking')) 
def start_tracking_train(callback): 

    train_tracking = callback.data.split('_')[0]
    chat_id = callback.message.chat.id
    bot.send_message(chat_id, f'Обновление по {train_tracking}')

    
    #запуск отслеживания в параллельном потоке
    def tracking_loop():
        #проверка, что отслеживание поезда активно
        while user_data[chat_id].get('tracking_active', {}).get(train_tracking, False):

            #получение новой страницы soup
            # user_data[chat_id ]['url']
            # r = requests.get(user_data[chat_id ]['url'])

            #на время тестов обращение к файлу Минск-Брест 2025-04-25
            with open('test_rw_by.html', 'r+') as f:
                r = f.read()

            only_span_div_tag = SoupStrainer(['span', 'div'])
            soup = BeautifulSoup(r, 'lxml', parse_only=only_span_div_tag) #вернуть r.text

            #добавление в сессию
            user_data[chat_id]['soup'] = soup

            ticket_dict = check_tickets_by_class(train_tracking, soup)
            bot.send_message(chat_id, f'Обновление по {train_tracking}: {ticket_dict}')
            time.sleep(30)
    
    # регистрация поезда в списке отслеживания
    if 'tracking_active' not in user_data[chat_id]:
        user_data[chat_id]['tracking_active'] = {}
    user_data[chat_id]['tracking_active'][train_tracking] = True

    thread = threading.Thread(target=tracking_loop)
    thread.start()
    bot.send_message(chat_id, f'Отслеживание поезда {train_tracking} запущено.')
  
        

#-------------------------------------



#проверка наличия места
def check_tickets_by_class(train_number, soup):
    train_info = soup.select(f'div.sch-table__row[data-train-number="{train_number}"]') # type: ignore
    selling_allowed = train_info[0]['data-ticket_selling_allowed']
    if selling_allowed == 'true':
        return get_tickets_by_class(train_number, soup)
    return 'Мест нет'

#получение количества мест
def get_tickets_by_class(train_number, soup):

    # информация о наличии мест и классов вагонов
    train_info = soup.select(f'div.sch-table__row[data-train-number="{train_number}"]') # type: ignore
    # доступные классы вагонов и места
    class_names = train_info[0].find_all(class_="sch-table__t-quant js-train-modal dash")
    # вывод словаря с заменой номера на имя класса обслуживания
    # и общего количества мест для каждого класса
    tickets_by_class = {}
    for class_n in class_names:
        name = seats_type_dict[class_n['data-car-type']] # type: ignore
        seats_num = int(class_n.select_one('span').text) # type: ignore
        if name in tickets_by_class:
            tickets_by_class[name] += seats_num
        else:
            tickets_by_class[name] = seats_num
    return tickets_by_class


# получение нового объекта soup (страницы на  определённую дату по маршруту)


#останов
@bot.message_handler(commands=['stop'])
def stop(message):
    bot.send_message(message.chat.id, 'Пока. Сервер остановлен ')
    bot.stop_polling()
    sys.exit(0)
#для постоянной работы:
bot.polling(non_stop=True)


'''
# файл для проверки наличия станции в общем списке
with open('all_stations_dict.py') as py_file:
    stantions = py_file.read()




def ctrl_rus(city_name):
    ctrl_rus = 'ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ'
    if not city_name:
        return False
    for i in city_name:
        if i not in ctrl_rus or not i:
            return False
    return True

while True:
    if not ctrl_rus(city_from):
        city_from = input('Изменить город отправления: ').strip().lower().capitalize()
        continue
    elif not ctrl_rus(city_to):
        city_to = input('Изменить город прибытия: ').strip().lower().capitalize()
        continue
    else:
        break
        '''