
# Token
# 7982797130:AAFTvuBdm2bWhmu4UWr2g1Ptc8g7WBR_XJ8 
# @TicketCatchingBot

import telebot
import webbrowser
from telebot import types
import sqlite3

name = None
#Создаётся объект бота, который умеет принимать сообщения от Telegram.
bot = telebot.TeleBot('7982797130:AAFTvuBdm2bWhmu4UWr2g1Ptc8g7WBR_XJ8')

name = None
# старт с созданием таблицы в БД и работа с ней
@bot.message_handler(commands=['start'])
def start(message):

    # подключение к БД (создание, если нету)
    conn = sqlite3.connect('test_bot.sql')
    # курсор для выполнения команд
    cur = conn.cursor()

    # подготовка запроса 
    cur.execute('CREATE TABLE IF NOT EXISTS users(id int auto_increment primary key, name varchar(50), password varchar(50))')

    # синхронизация именений
    conn.commit()

    # закрыть соединение с таблицей
    cur.close()

    # закрыть соединение с БД
    conn.close()

    bot.send_message(message.chat.id, 'Ввести имя')
    
    #вызов следующей функции после ввода имени
    bot.register_next_step_handler(message, user_name)


def user_name(message):
    global name
    name = message.text.strip()
    bot.send_message(message.chat.id, 'Ввести пароль')
    bot.register_next_step_handler(message, user_pass)

def user_pass(message):
    password = message.text.strip()
    #добавление в БД
    conn = sqlite3.connect('test_bot.sql')
    cur = conn.cursor()
    cur.execute("INSERT INTO users (name, password) VALUES ('%s', '%s')" % (name, password))
    conn.commit()
    cur.close()
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Список пользователей', callback_data='users'))
    bot.send_message(message.chat.id, 'Пользователь зарегистрирован', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback(call): #для обращения к данным таблицы
    conn = sqlite3.connect('test_bot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    #conn.commit() -- не нужна, так как нет изменений в БД
    users = cur.fetchall() # возвращает все найденные записи
    
    # перебор найденных данных и вывод н экран
    info = ''
    for el in users:
        info += f'Имя: {el[1]}, password: {el[2]}\n'
    cur.close()
    conn.close()

    # вывод информации из БД
    bot.send_message(call.message.chat.id, info)

# декоратор для обработки команд (регистрирует функцию как обработчик команд)
'''message_handler — главный декоратор для обработки сообщений.
commands=['start'] — фильтр: "вызывай эту функцию только на команду /start"'''
@bot.message_handler(commands=['1'])
def main(message):
    bot.send_message(message.chat.id, '<b>Hallo</b>', parse_mode='html')

@bot.message_handler(commands=['2'])
def main(message):
    bot.send_message(message.chat.id, message) #выводит полную информацию про чат
     #и про пользователя, с которым идёт диалог

@bot.message_handler(commands=['3'])
def main(message):
    bot.send_message(message.chat.id, message.from_user.first_name) #выводит имя пользователя

# для быстрого открытия сайта 
@bot.message_handler(commands=['site'])
def main(message):
    webbrowser.open('https://pass.rw.by/')


# для обработки файлов
@bot.message_handler(content_types=['photo'])
def get_photo(message):
    
    #для создания кнопки под сообщением(можно прописывать вне функции)
    markup = types.InlineKeyboardMarkup() # объект кнопки
    markup.add(types.InlineKeyboardButton('Go to site', url='https://google.com/'))
    markup.add(types.InlineKeyboardButton('Delete photo', callback_data='delete')) #вызывается функция с переданным значением 'delete'
    markup.add(types.InlineKeyboardButton('Edit', callback_data='edit'))

    bot.reply_to(message, 'Beautiful Photo', reply_markup=markup) # передать объект кнопки

#варианты расположения кнопок:
@bot.message_handler(commands=['4'])
def get_photo(message):
    markup = types.InlineKeyboardMarkup() # объект кнопки
    # создание кнопки
    btn1 = types.InlineKeyboardButton('Go to site', url='https://google.com/')
    markup.row(btn1) # добавить кнопку 1 в 1-й ряд
    btn2 = types.InlineKeyboardButton('Delete', callback_data='delete')
    btn3 = types.InlineKeyboardButton('Edit', callback_data='edit')
    #передача кнопки в объект кнопки
    markup.row(btn2, btn3)

    bot.reply_to(message, 'Test buttons templates', reply_markup=markup)

# обработка callback_data при нажатии на кнопку
# здесь func -- фильтр, показывает: на какие запросы реагировать (здесь - на все)
@bot.callback_query_handler(func=lambda callback: True) 
def callback_message(callback): # callback == все данные ответа

    if callback.data == 'delete':
        #удалить предпоследнее сообщение
        bot.delete_message(callback.message.chat.id, callback.message.message_id - 1)
    elif callback.data == 'edit':
        # изменить сообщение ответа бота (нельзя редактировать сообщение пользователя?)
        bot.edit_message_text('Edit text', callback.message.chat.id, callback.message.message_id)


# добавить кнопки под строкой ввода сообщений
@bot.message_handler(commands=['5'])
def main(message):
    
    markup = types.ReplyKeyboardMarkup()
    btn1 = types.KeyboardButton('Go to site') # принимает просто название кнопки
    markup.row(btn1)
    bot.send_message(message.chat.id, 'Hello 5', reply_markup=markup)
    # регистрация действий при нажатии кнопки
    bot.register_next_step_handler(message, on_click) # передаёт функцию, которая будет срабатывать при нажатии
    # on_click -- следующая функция, что будет работать

def on_click(message):
    if message.text == 'Go to site': # текст, который на кнопке
        bot.send_message(message.chat.id, 'Web is open')

# если не указаны параметры, то можно обрабатывать любые данные
# если будет прописан вверху, то будет перехватывать все сообщения
@bot.message_handler()
def info(message):
    #выбор текста из сообщения
    if message.text.lower() == 'привет':
        bot.send_message(message.chat.id, f'Hello {message.from_user.first_name}')
    elif message.text.lower() == 'id':
        # метод ответа на определённое сообщение
        bot.reply_to(message, f'ID = {message.from_user.id}')







#для постоянной работы:
bot.polling(non_stop=True)