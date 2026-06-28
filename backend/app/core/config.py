import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # База данных
    database_url: str = os.getenv("database_url", "")
    
    # Redis (если используешь)
    redis_url: str = os.getenv("redis_url", "")
    
    # Секретный ключ для JWT и т.д.
    secret_key: str = os.getenv("secret_key", "change-me-in-production")
    
    # YandexGPT Configuration
    YANDEX_GPT_API_KEY: str = os.getenv("YANDEX_GPT_API_KEY", "")
    YANDEX_GPT_FOLDER_ID: str = os.getenv("YANDEX_GPT_FOLDER_ID", "")
    YANDEX_GPT_MODEL_URI: str = os.getenv("YANDEX_GPT_MODEL_URI", "")

    # Groq Configuration (OpenAI-совместимый API). Модель по умолчанию —
    # сильная llama-3.3-70b (судье нужна точность). Сменить — GROQ_MODEL в .env.
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Провайдер LLM-судьи по умолчанию: "yandex" | "groq".
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "yandex")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Игнорировать переменные из .env, которые не объявлены в классе

# Создаем экземпляр настроек
settings = Settings()