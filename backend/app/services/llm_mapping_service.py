import httpx
import json
import re
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# Системный промпт для YandexGPT
SYSTEM_PROMPT = """
Ты — эксперт по оснащению школ и тендерным закупкам согласно Приказу Минпросвещения РФ №838.
Твоя задача: сопоставить товар поставщика с одной из позиций стандарта из предоставленного списка кандидатов.

ПРАВИЛА:
1. Внимательно анализируй назначение товара, его характеристики и контекст (школьное оборудование, пособия, инвентарь).
2. Игнорируй общие слова-паразиты ("набор", "комплект", "учебный"), если они не несут смысловой нагрузки.
3. Если ни один кандидат не подходит (товар поставщика вообще из другой области, например, "Лобзик", а кандидат "Словарные слова"), верни `standard_id: null`.
4. Оцени уверенность (confidence) от 0.0 до 1.0.
5. Кратко объясни причину выбора или отказа в поле `reason`.

ФОРМАТ ОТВЕТА:
Строгий JSON без markdown-разметки, без комментариев.
{
  "standard_id": <int или null>,
  "confidence": <float>,
  "reason": "<string>"
}
"""


async def get_llm_mapping(product_data: dict, candidates: list[dict]) -> dict:
    """
    Отправляет товар и топ-N кандидатов в YandexGPT для принятия решения.
    
    :param product_data: Словарь с данными товара (name, description, properties)
    :param candidates: Список кандидатов из БД (id, standard_name)
    :return: Словарь с решением LLM
    """
    if not settings.YANDEX_GPT_API_KEY or not settings.YANDEX_GPT_FOLDER_ID:
        logger.warning("YandexGPT API key or Folder ID is not set. Skipping LLM fallback.")
        return {"standard_id": None, "confidence": 0.0, "reason": "LLM not configured"}

    # Формируем список кандидатов для промпта
    candidates_text = "\n".join([f"- ID: {c['id']}, Название: {c['standard_name']}" for c in candidates])
    
    # Формируем запрос пользователя
    user_prompt = f"""
ТОВАР ПОСТАВЩИКА:
Название: {product_data.get('name', 'Не указано')}
Описание: {product_data.get('description', 'Нет описания')}
Характеристики: {json.dumps(product_data.get('properties', {}), ensure_ascii=False)}

СПИСОК КАНДИДАТОВ ИЗ СТАНДАРТА (Приказ 838):
{candidates_text}

Найди лучшее соответствие или укажи, что подходящего нет.
"""

    payload = {
        "modelUri": settings.YANDEX_GPT_MODEL_URI,
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,  # Низкая температура для более предсказуемого ответа
            "maxTokens": 1000
        },
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": user_prompt}
        ]
    }

    headers = {
        "Authorization": f"Api-Key {settings.YANDEX_GPT_API_KEY}",
        "x-folder-id": settings.YANDEX_GPT_FOLDER_ID,
        "Content-Type": "application/json"
    }

    llm_text = ""  # Инициализируем, чтобы не было ошибки в except json.JSONDecodeError

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            
            # Правильный ключ — 'alternatives'
            alternatives = result.get('result', {}).get('alternatives', [])
            if not alternatives:
                logger.error(f"No alternatives in YandexGPT response: {result}")
                return {"standard_id": None, "confidence": 0.0, "reason": "LLM returned empty alternatives"}
            
            llm_text = alternatives[0]['message']['text']
            
            # Очищаем от возможных markdown-оберток (```json ... ```)
            clean_text = re.sub(r'^```json\s*|\s*```$', '', llm_text.strip(), flags=re.MULTILINE)
            
            # Парсим JSON
            return json.loads(clean_text)

    except httpx.HTTPStatusError as e:
        logger.error(f"YandexGPT API HTTP error: {e.response.status_code} - {e.response.text}")
        return {"standard_id": None, "confidence": 0.0, "reason": f"LLM API Error: {e.response.status_code}"}
    except json.JSONDecodeError:
        logger.error(f"Failed to parse LLM JSON response: {llm_text}")
        return {"standard_id": None, "confidence": 0.0, "reason": "LLM returned invalid JSON"}
    except Exception as e:
        logger.exception(f"Unexpected error during LLM mapping: {str(e)}")
        return {"standard_id": None, "confidence": 0.0, "reason": f"Unexpected error: {str(e)}"}