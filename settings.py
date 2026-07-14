import os
from dotenv import load_dotenv


class Settings:
    """Application configuration loaded from environment variables or a local .env file."""

    def __init__(self):
        load_dotenv()
        # Retrieve variables from environment
        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise ValueError("BOT_TOKEN environment variable is missing")
        self.BOT_TOKEN = bot_token
        self.PAN_LIST = os.getenv("PAN_LIST", "")

