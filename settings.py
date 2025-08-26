from pydantic import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or a local .env file."""

    BOT_TOKEN: str  # Telegram Bot token from BotFather
    PAN_LIST: str = ""  # Comma-separated list of PANs (ABCDE1234F,XYZAB1234G)

    class Config:
        env_file = ".env"
        case_sensitive = False
