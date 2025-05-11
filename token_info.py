from dotenv import load_dotenv
import os

# Загружаем переменные из файла .env
load_dotenv()

token = os.getenv("TOKEN")
stop_code = os.getenv("STOP_CODE")
bot_name = os.getenv("BOT_NAME")