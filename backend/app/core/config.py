import os
from pathlib import Path

from pydantic_settings import BaseSettings

# Понятная ошибка вместо длинного трейсбека, если .env сохранён не в UTF-8
# (типично: русский комментарий в Windows-1251). pydantic читает .env как UTF-8
# и иначе падает с невнятным UnicodeDecodeError на старте всего сервера.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    try:
        _ENV_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise RuntimeError(
            f"Файл {_ENV_PATH} не в кодировке UTF-8 (невалидный байт в позиции "
            f"{e.start}). Пересохраните .env как UTF-8 или уберите не-ASCII символы "
            f"из комментариев. Быстрая чистка: "
            f"python3 -c \"p='{_ENV_PATH}'; b=open(p,'rb').read(); "
            f"open(p+'.bak','wb').write(b); "
            f"open(p,'w',encoding='utf-8').write(b.decode('utf-8','ignore'))\""
        ) from None

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