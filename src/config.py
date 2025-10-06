import logging
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """
    Class to hold all the application settings.
    Reads configuration from environment variables.
    """

    # Telegram
    TOKEN: str = os.getenv("TOKEN", "")
    STOP_CODE: str = os.getenv("STOP_CODE", "")
    BOT_NAME: str = os.getenv("BOT_NAME", "@TicketCatchingBot")
    WEBHOOK_URL: str = os.getenv(
        "WEBHOOK_URL", "https://famous-clubs-battle.loca.lt"
    )
    WEB_PORT: int = int(os.getenv("WEB_PORT", 8000))

    # Database
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "1765362")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "rw_by_local")

    # Cloud SQL Connector
    # e.g. 'project:region:instance'
    DB_INSTANCE_NAME: str = os.getenv("DB_INSTANCE_NAME", "")

    # Admin
    ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", 0))

    def __init__(self):
        """
        Validates that required environment variables are set.
        """
        if not self.TOKEN:
            logging.error("TOKEN environment variable not set.")
            raise ValueError("TOKEN environment variable not set.")

        if not self.WEBHOOK_URL and not os.getenv("DEV_MODE"):
            logging.error(
                "WEBHOOK_URL environment variable not set for production."
            )
            raise ValueError(
                "WEBHOOK_URL environment variable not set for production."
            )

        if not self.DB_INSTANCE_NAME and not os.getenv("DEV_MODE"):
            logging.warning(
                "DB_INSTANCE_NAME is not set. This is required for Cloud SQL."
            )


settings = Settings()
