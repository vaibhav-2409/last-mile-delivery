"""Runtime configuration. Everything is environment driven so the same image
runs locally (SQLite) and on Render/Railway (Postgres)."""
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- core ---
    APP_NAME: str = os.getenv("APP_NAME", "Last-Mile Delivery Tracker")
    ENV: str = os.getenv("ENV", "development")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./lastmile.db")

    # --- auth ---
    JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_TTL_MINUTES: int = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "720"))

    # --- bootstrap admin (created by seed / first boot) ---
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@lastmile.dev")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "Admin@123")

    # --- email (SMTP: Brevo / Mailtrap / Gmail app-password all work) ---
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_USE_TLS: bool = _bool("SMTP_USE_TLS", True)
    MAIL_FROM: str = os.getenv("MAIL_FROM", "no-reply@lastmile.dev")
    MAIL_FROM_NAME: str = os.getenv("MAIL_FROM_NAME", "Last-Mile Tracker")

    # --- sms (Twilio trial or Fast2SMS free tier) ---
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "").lower()  # twilio | fast2sms | ""
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
    FAST2SMS_API_KEY: str = os.getenv("FAST2SMS_API_KEY", "")

    # --- behaviour ---
    NOTIFICATIONS_ENABLED: bool = _bool("NOTIFICATIONS_ENABLED", True)
    AUTO_SEED_ON_BOOT: bool = _bool("AUTO_SEED_ON_BOOT", True)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    @property
    def sqlalchemy_url(self) -> str:
        url = self.DATABASE_URL
        # Render/Heroku hand out postgres:// which SQLAlchemy 2.x no longer accepts
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        return url

    @property
    def email_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASSWORD)

    @property
    def sms_configured(self) -> bool:
        if self.SMS_PROVIDER == "twilio":
            return bool(self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN and self.TWILIO_FROM_NUMBER)
        if self.SMS_PROVIDER == "fast2sms":
            return bool(self.FAST2SMS_API_KEY)
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
