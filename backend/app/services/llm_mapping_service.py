import httpx
import json
import re
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# Системный промпт для YandexGPT
SYSTEM_PROMPT = """
Ты — эксперт по сопоставлению товаров с позициями Приказа Минпросвещения РФ №838.
Тебе дают товар поставщика и список кандидатов-позиций стандарта. У каждого
кандидата в квадратных скобках указана область/кабинет, например
"[По предметной области]" или "[Кабинет физики]".
Выбери ОДНУ позицию, наиболее точно соответствующую товару, либо верни null.

ГЛАВНЫЕ ПРАВИЛА:
1. Сопоставляй по ТИПУ ИЗДЕЛИЯ, а не по теме/предмету. Тип задаётся ведущим
   словом названия товара:
   - "таблицы" → позиция про таблицы;
   - "карты"/"атлас" → позиция про карты;
   - "модель"/"модель-аппликация" → позиция про модели;
   - "карточки"/"раздаточный" → позиция про раздаточные карточки/материалы;
   - "портреты" → позиция про портреты; и т.д.
   НЕ путай типы между собой (таблицы ≠ карты ≠ модели ≠ пособия ≠ приборы).
2. Для демонстрационных / учебных ТАБЛИЦ выбирай позицию-таблицы по правилу:
   - если среди кандидатов есть «[Кабинет <предмет>] Комплект демонстрационных
     учебных таблиц», и предмет товара совпадает с этим кабинетом
     (например товар «Таблицы по физике» и кандидат «[Кабинет физики]
     Комплект … таблиц») → выбирай ЭТУ кабинетную позицию;
   - во всех остальных случаях (предмета нет среди кабинетных таблиц) → выбирай
     ОБЩУЮ "[По предметной области] Комплект демонстрационных учебных таблиц".
   В любом случае это должна быть позиция-ТАБЛИЦЫ, а не предметные
   пособия/карты/модели.
3. Так же предпочитай общие "[По предметной области]" позиции:
   - словари/справочники/энциклопедии → "Словари, справочники, энциклопедия";
   - электронные/интерактивные средства обучения, ЭОР, онлайн-курсы, электронные
     версии → "Электронные средства обучения/интерактивные средства обучения...".
4. Русский/родной язык ≠ иностранный язык. Если товар про русский язык, НЕ
   выбирай позицию про иностранный язык (и наоборот).
5. Если ни один кандидат не совпадает по ТИПУ изделия — верни standard_id = null.
6. Слова "набор", "комплект", "учебный" — не признак типа, не опирайся на них.

ПРИМЕРЫ:
- "Таблицы демонстрационные «Химия 8 класс»" →
  "[По предметной области] Комплект демонстрационных учебных таблиц".
- "Комплект настенных учебных карт. История России" →
  позиция про карты по истории/настенные карты (НЕ таблицы).
- "Раздаточные карточки с буквами русского алфавита" →
  позиция про раздаточные карточки с буквами русского алфавита (НЕ иностранный).
- "ЭОР ... Электронная версия" →
  "[По предметной области] Электронные средства обучения...".
- "Лобзик" при кандидатах про пособия → null.

ФОРМАТ ОТВЕТА:
Строгий JSON без markdown, без комментариев.
{
  "standard_id": <int или null>,
  "confidence": <float 0.0..1.0>,
  "reason": "<краткое объяснение по типу изделия>"
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