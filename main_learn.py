import requests
from bs4.filter import SoupStrainer
from bs4 import BeautifulSoup, StopParsing
import lxml
from urllib.parse import quote
import time
import json
from token_info import token, bot_name


# url = "https://pass.rw.by/ru/route/?from=%D0%9C%D0%B8%D0%BD%D1%81%D0%BA&from_exp=2100000&from_esr=140210&to=%D0%92%D0%B8%D1%82%D0%B5%D0%B1%D1%81%D0%BA&to_exp=2100050&to_esr=160002&front_date=11+%D0%B0%D0%BF%D1%80.+2025&date=2025-04-11"
# r = requests.get(url)
# with open('test_rw_by.html', 'w+') as f:
#     f.write(r.text)


with open('test_rw_by.html', 'r') as f:
    string = f.read()

soup = BeautifulSoup(string, 'lxml')
soup.select("span")
soup.span.contents # type: ignore
soup.find_all('span')
soup.select("head > title") #найти под определённым тегом
soup.select(".sister")#найти по классу
soup.select("a#link2")#найти по id для тега а
soup.select('a[href="http://example.com/elsie"]')#найти по значению атрибута
# print(soup.prettify())

# не тратить время на весь документ и парсить только нужные теги:
only_span_tag = SoupStrainer(['span', 'div'])
soup = BeautifulSoup(string, 'lxml', parse_only=only_span_tag)
# print(soup.prettify())


# Input rules for searching
city_from = 'Минск' #input('From: ').strip().lower().capitalize()
city_to = 'Витебск' #input('To: ').strip().lower().capitalize()
date = '2025-04-12' #input('Дата в формате гггг-мм-дд: ')

# файл для проверки наличия станции в общем списке
with open('all_stations_list.json') as json_file:
    stantions = json.load(json_file)


# словарь соответствия номер-название класса
seats_type_dict = {
    '1': 'Общий',
    '2': 'Сидячий',
    '3': 'Плацкартный',
    '4': 'Купейный',
    '5': 'Мягкий',
    '6': 'СВ',
}

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

encoded_from = quote(city_from)
encoded_to = quote(city_to)

url = f'https://pass.rw.by/ru/route/?from={encoded_from}&to={encoded_to}&date={date}'

# Список поездов с временем отправления-прибытия
def get_trains_list():
    train_id_list = [i.text for i in soup.find_all('span', class_="train-number")] 
    trains_list = []
    # получение времени отправления и прибытия
    for train in train_id_list:
        time_depart = soup.select(f'[data-train-number="{train}"] [data-sort="departure"]')[0].text.strip()
        time_arriv = soup.select(f'[data-train-number="{train}"] [data-sort="arrival"]')[0].text.strip()
        trains_list.append([train,time_depart, time_arriv])
    return trains_list

# Вывод списка возможных поездов и времени отправления/прибытия
# выбор необходимого поезда
trains_list = get_trains_list()
for id, train in enumerate(trains_list):
    print(id + 1, ' ', train)

selected_num = int(input('Select num train: ')) - 1

train_selected = trains_list[selected_num][0]

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

while True:
    counter += 1
    if counter == 2:
        break
    
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'lxml')
    selling_allowed = check_selling_allowed(train_selected, soup)

    print(selling_allowed)

    seats_list = []
    if selling_allowed:
        get_tickets_by_class(train_selected, soup)
    time.sleep(5)
