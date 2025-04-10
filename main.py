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


# словарь соответствия номер-название класса
seats_type_dict = {
    '1': 'Общий',
    '2': 'Сидячий',
    '3': 'Плацкартный',
    '4': 'Купейный',
    '5': 'Мягкий',
    '6': 'СВ',
}


with open('test_rw_by.html', 'r') as f:
    string = f.read()

# не тратить время на весь документ и парсить только нужные теги:
only_span_tag = SoupStrainer(['span', 'div'])
soup = BeautifulSoup(string, 'lxml', parse_only=only_span_tag)
# print(soup.prettify())


# Input rules for searching
# city_from = 'Минск' #input('From: ').strip().lower().capitalize()
# city_to = 'Витебск' #input('To: ').strip().lower().capitalize()
# date = '2025-04-12' #input('Дата в формате гггг-мм-дд: ')

#-------------------------------
#Подключение бота для ввода данных
city_from = ''
city_to = ''
date = ''
#Создаётся объект бота, который умеет принимать сообщения от Telegram.
bot = telebot.TeleBot(token)
@bot.message_handler(commands=['start'])
def start(message):
    # приветствие и переход на получение данных
    bot.send_message(message.chat.id, 'Привет. Город отправления: ')
    #вызов следующей функции для города отправления
    bot.register_next_step_handler(message, get_city_from)
    
def get_city_from(message):
    global city_from
    city_from = message.text.strip().lower().capitalize()
    bot.send_message(message.chat.id, 'Город прибытия: ')
    #вызов следующей функции для города прибытия
    bot.register_next_step_handler(message, get_city_to)

def get_city_to(message):
    global city_to
    city_to = message.text.strip().lower().capitalize()
    bot.send_message(message.chat.id, 'Дата в формате гггг-мм-дд: ')
    #вызов следующей функции для города даты
    bot.register_next_step_handler(message, get_date)

def get_date(message):
    global city_from, city_to, date
    print(type(city_to))
    date = message.text.strip()
    # проверка введённых данных
    bot.send_message(message.chat.id, f'{city_from} - {city_to}: {date}')
    #вызов следующей функции для доступных поездов
    bot.register_next_step_handler(message, get_trains_list)

def get_trains_list(message):
    global city_from, city_to, date
    encoded_from = quote(city_from)
    encoded_to = quote(city_to)

    url = f'https://pass.rw.by/ru/route/?from={encoded_from}&to={encoded_to}&date={date}'
    try:
        r = requests.get(url)
        soup = BeautifulSoup(r.text, 'lxml')

    except Exception:
        bot.send_message(message.chat.id, 'Что-то не так')


    
    
    train_id_list = [i.text for i in soup.find_all('span', class_="train-number")] 
    for i in train_id_list:
        bot.send_message(message.chat.id, i)
    trains_list = []
    # получение времени отправления и прибытия
    for train in train_id_list:
        time_depart = soup.select(f'[data-train-number="{train}"] [data-sort="departure"]')[0].text.strip()
        time_arriv = soup.select(f'[data-train-number="{train}"] [data-sort="arrival"]')[0].text.strip()
        trains_list.append([train,time_depart, time_arriv])
    for i in trains_list:
        bot.send_message(message.chat.id, i)  


#-------------------------------------




# Список поездов с временем отправления-прибытия
# def get_trains_list():
#     train_id_list = [i.text for i in soup.find_all('span', class_="train-number")] 
#     trains_list = []
#     # получение времени отправления и прибытия
#     for train in train_id_list:
#         time_depart = soup.select(f'[data-train-number="{train}"] [data-sort="departure"]')[0].text.strip()
#         time_arriv = soup.select(f'[data-train-number="{train}"] [data-sort="arrival"]')[0].text.strip()
#         trains_list.append([train,time_depart, time_arriv])
#     return trains_list

# Вывод списка возможных поездов и времени отправления/прибытия
# выбор необходимого поезда
# trains_list = get_trains_list()
# for id, train in enumerate(trains_list):
#     print(id + 1, ' ', train)

# selected_num = int(input('Select num train: ')) - 1

# train_selected = trains_list[selected_num][0]

#проверка наличия любого места
def check_selling_allowed(train_number, soup):
    train_info = soup.select(f'div.sch-table__row[data-train-number="{train_number}"]')
    selling_allowed = train_info[0]['data-ticket_selling_allowed']
    if selling_allowed == 'true':
        return True
    return False #data-value

#вывод количества мест
def get_tickets_by_class(train_number, soup):
    # информация о наличии мест и классов вагонов
    train_info = soup.select(f'div.sch-table__row[data-train-number="{train_number}"]')
    # доступные классы вагонов и места
    class_names = train_info[0].find_all(class_="sch-table__t-quant js-train-modal dash")
    print(class_names)
    # вывод словаря с заменой номера на имя класса обслуживания
    # и общего количества мест для каждого класса
    tickets_by_class = {}
    for class_n in class_names:
        name = seats_type_dict[class_n['data-car-type']]
        seats_num = int(class_n.select_one('span').text)
        if name in tickets_by_class:
            tickets_by_class[name] += seats_num
        else:
            tickets_by_class[name] = seats_num
    print(tickets_by_class)
    pass

# Цикл на ограниченное число итераций
counter = 0

# while True:
#     counter += 1
#     if counter == 2:
#         break
    
#     r = requests.get(url)
#     soup = BeautifulSoup(r.text, 'lxml')
#     selling_allowed = check_selling_allowed(train_selected, soup)


#     seats_list = []
#     if selling_allowed:
#         get_tickets_by_class(train_selected, soup)
#     time.sleep(5)



    


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
with open('all_stations_list.json') as json_file:
    stantions = json.load(json_file)




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