from dotenv import load_dotenv
import os

# Загружаем переменные из файла .env
load_dotenv()

token = os.getenv("TOKEN")
stop_code = os.getenv("STOP_CODE")
bot_name = os.getenv("BOT_NAME")
db_password = os.getenv("DB_PASSWORD")
db_user = os.getenv("DB_USER")
db_host = os.getenv("DB_HOST") 
db_port = os.getenv("DB_PORT") 
db_name = os.getenv("DB_NAME") 
webhook_url = os.getenv("WEBHOOK_URL")
web_port = int(os.getenv("WEB_PORT")) # type: ignore