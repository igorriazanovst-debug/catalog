import asyncio
import httpx
import json
import re
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# ВАЖНО: качество маппинга измерено на полной модели yandexgpt.
# Облегчённая yandexgpt-lite НЕ держит правила судьи (precision@pool падает
# с ~84% до ~58%). Требуется YANDEX_GPT_MODEL_URI вида
# gpt://<folder>/yandexgpt/latest  (без "-lite").
if settings.YANDEX_GPT_MODEL_URI and "lite" in settings.YANDEX_GPT_MODEL_URI.lower():
    logger.warning(
        "YANDEX_GPT_MODEL_URI указывает на облегчённую модель (lite). "
        "Качество судьи заметно ниже — используйте полную yandexgpt."
    )

# Видимая отметка в логе, что LLM-запросы пойдут через прокси (host без кред).
if settings.LLM_PROXY:
    _safe = settings.LLM_PROXY.split("@")[-1]
    print(f"[startup] LLM-запросы через прокси: {_safe}", flush=True)

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

ЧАСТЫЕ ОШИБКИ — НЕ ПУТАЙ (для товара-ТАБЛИЦ «Комплект таблиц…» /
«Таблицы демонстрационные…» правильный ответ — «Комплект демонстрационных
учебных таблиц», и НИКОГДА не следующее):
   - «Тумба для таблиц / шкаф для хранения таблиц и плакатов» — это МЕБЕЛЬ для
     хранения, а не сами таблицы. Не выбирай её для товара-таблиц.
   - «Словари, справочники, энциклопедия» — только если товар сам словарь/
     справочник/энциклопедия. Для таблиц — НЕ выбирай.
   - «Электронные средства обучения / ЭОР / интерактивные средства» — только если
     товар ЭЛЕКТРОННЫЙ (есть «ЭОР», «электронная версия», «онлайн»,
     «интерактивный»). Для бумажных таблиц/карточек — НЕ выбирай.
   - «[Кабинет физики] Комплект демонстрационных учебных таблиц» — бери ТОЛЬКО
     если товар про физику. Для химии, географии, истории, математики и др. —
     выбирай «[По предметной области] Комплект демонстрационных учебных таблиц».
   - «Раздаточные …» (карточки/материалы) — отдельный тип; не подменяй им
     демонстрационные таблицы и наоборот.

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


# Провайдеры LLM-судьи. id -> человекочитаемая метка.
PROVIDERS = {"yandex": "YandexGPT", "groq": "Groq", "aitunnel": "AITunnel (Gemini)"}

RETRIABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BASE_DELAY = 2.0  # 2s, 4s, 8s


def provider_configured(provider: str) -> bool:
    if provider == "yandex":
        return bool(settings.YANDEX_GPT_API_KEY and settings.YANDEX_GPT_FOLDER_ID)
    if provider == "groq":
        return bool(settings.GROQ_API_KEY)
    if provider == "aitunnel":
        return bool(settings.AITUNNEL_API_KEY)
    return False


def providers_status() -> list[dict]:
    """Список провайдеров с признаком, настроен ли (есть ключи)."""
    return [
        {"id": pid, "label": label, "configured": provider_configured(pid),
         "default": pid == (settings.LLM_PROVIDER or "yandex").lower()}
        for pid, label in PROVIDERS.items()
    ]


def _build_user_prompt(product_data: dict, candidates: list[dict]) -> str:
    candidates_text = "\n".join(
        f"- ID: {c['id']}, Название: {c['standard_name']}" for c in candidates
    )
    return f"""
ТОВАР ПОСТАВЩИКА:
Название: {product_data.get('name', 'Не указано')}
Описание: {product_data.get('description', 'Нет описания')}
Характеристики: {json.dumps(product_data.get('properties', {}), ensure_ascii=False)}

СПИСОК КАНДИДАТОВ ИЗ СТАНДАРТА (Приказ 838):
{candidates_text}

Найди лучшее соответствие или укажи, что подходящего нет.
"""


def _parse_decision(raw_text: str) -> dict:
    """Достаёт JSON-решение из ответа модели (снимает ```json-обёртку)."""
    clean = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.MULTILINE)
    return json.loads(clean)


def _make_client() -> httpx.AsyncClient:
    """httpx-клиент с опциональным прокси (settings.LLM_PROXY) — только для
    LLM-запросов. Совместимо с новым (proxy=) и старым (proxies=) httpx."""
    proxy = settings.LLM_PROXY
    if not proxy:
        return httpx.AsyncClient(timeout=30.0)
    try:
        return httpx.AsyncClient(timeout=30.0, proxy=proxy)
    except TypeError:
        return httpx.AsyncClient(timeout=30.0, proxies=proxy)


def _body_snippet(response, limit: int = 200) -> str:
    """Короткий однострочный фрагмент тела ответа (для диагностики 4xx/5xx)."""
    try:
        text = response.text
    except Exception:
        return ""
    return " ".join(text.split())[:limit]


async def get_llm_mapping(product_data: dict, candidates: list[dict],
                          provider: str | None = None) -> dict:
    """
    Отдаёт товар и кандидатов LLM-судье и возвращает решение
    {standard_id, confidence, reason}. При сбое — то же + error=True
    (сбой провайдера ≠ «нет подходящего стандарта»).

    provider: "yandex" | "groq". По умолчанию settings.LLM_PROVIDER.
    """
    user_prompt = _build_user_prompt(product_data, candidates)
    return await _dispatch(SYSTEM_PROMPT, user_prompt, provider)


async def _dispatch(system_prompt: str, user_prompt: str,
                    provider: str | None = None) -> dict:
    """Выбор провайдера и вызов с заданным системным промптом. Возвращает
    распарсенный JSON-ответ модели (или {error:True} при сбое)."""
    provider = (provider or settings.LLM_PROVIDER or "yandex").lower()
    if provider == "groq":
        return await _call_groq(system_prompt, user_prompt)
    if provider == "aitunnel":
        return await _call_aitunnel(system_prompt, user_prompt)
    return await _call_yandex(system_prompt, user_prompt)


async def _call_yandex(system_prompt: str, user_prompt: str) -> dict:
    if not settings.YANDEX_GPT_API_KEY or not settings.YANDEX_GPT_FOLDER_ID:
        logger.warning("YandexGPT не настроен (нет ключа/folder).")
        return {"standard_id": None, "confidence": 0.0,
                "reason": "YandexGPT не настроен", "error": True}

    payload = {
        "modelUri": settings.YANDEX_GPT_MODEL_URI,
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": 1000},
        "messages": [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_prompt},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {settings.YANDEX_GPT_API_KEY}",
        "x-folder-id": settings.YANDEX_GPT_FOLDER_ID,
        "Content-Type": "application/json",
    }
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def extract(result):
        alternatives = result.get('result', {}).get('alternatives', [])
        if not alternatives:
            raise ValueError("пустой alternatives")
        return alternatives[0]['message']['text']

    return await _post_with_retry(url, headers, payload, extract, "YandexGPT")


async def _call_groq(system_prompt: str, user_prompt: str) -> dict:
    if not settings.GROQ_API_KEY:
        logger.warning("Groq не настроен (нет GROQ_API_KEY).")
        return {"standard_id": None, "confidence": 0.0,
                "reason": "Groq не настроен", "error": True}
    return await _call_openai_compatible(
        base_url="https://api.groq.com/openai/v1",
        api_key=settings.GROQ_API_KEY, model=settings.GROQ_MODEL,
        system_prompt=system_prompt, user_prompt=user_prompt,
        label="Groq", json_mode=True,
    )


async def _call_aitunnel(system_prompt: str, user_prompt: str) -> dict:
    if not settings.AITUNNEL_API_KEY:
        logger.warning("AITunnel не настроен (нет AITUNNEL_API_KEY).")
        return {"standard_id": None, "confidence": 0.0,
                "reason": "AITunnel не настроен", "error": True}
    # response_format не форсируем: за AITunnel могут стоять разные модели
    # (gemini и др.), часть не принимает json_object → полагаемся на промпт
    # и парсер (снимает ```json-обёртку).
    return await _call_openai_compatible(
        base_url=settings.AITUNNEL_BASE_URL,
        api_key=settings.AITUNNEL_API_KEY, model=settings.AITUNNEL_MODEL,
        system_prompt=system_prompt, user_prompt=user_prompt,
        label="AITunnel", json_mode=False,
    )


async def _call_openai_compatible(*, base_url: str, api_key: str, model: str,
                                  system_prompt: str, user_prompt: str, label: str,
                                  json_mode: bool) -> dict:
    """Общий вызов OpenAI-совместимого chat/completions (Groq, AITunnel и т.п.)."""
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 1000,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = base_url.rstrip("/") + "/chat/completions"

    def extract(result):
        choices = result.get('choices', [])
        if not choices:
            raise ValueError("пустой choices")
        return choices[0]['message']['content']

    return await _post_with_retry(url, headers, payload, extract, label)


async def _post_with_retry(url, headers, payload, extract, label) -> dict:
    """Общий цикл с ретраями для всех провайдеров. extract(result)->raw_text."""
    last_reason = f"{label} не ответил"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        retry = False
        try:
            async with _make_client() as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code in RETRIABLE_STATUS:
                last_reason = (f"{label} API Error: {response.status_code} "
                               f"{_body_snippet(response)}")
                logger.warning("%s %s (попытка %d/%d): %s",
                               label, response.status_code, attempt, MAX_ATTEMPTS,
                               _body_snippet(response))
                retry = True
            else:
                response.raise_for_status()
                raw_text = extract(response.json())
                return _parse_decision(raw_text)

        except httpx.HTTPStatusError as e:
            snippet = _body_snippet(e.response)
            logger.error("%s HTTP error: %s - %s", label,
                         e.response.status_code, snippet)
            return {"standard_id": None, "confidence": 0.0,
                    "reason": f"{label} API Error: {e.response.status_code}: {snippet}",
                    "error": True}
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_reason = f"{label} network error: {e}"
            logger.warning("%s сеть/таймаут (попытка %d/%d): %s",
                           label, attempt, MAX_ATTEMPTS, e)
            retry = True
        except json.JSONDecodeError:
            last_reason = f"{label} вернул невалидный JSON"
            logger.warning("%s невалидный JSON (попытка %d/%d)", label, attempt, MAX_ATTEMPTS)
            retry = True
        except Exception as e:  # noqa: BLE001
            last_reason = f"{label} unexpected error: {e}"
            logger.exception("%s неожиданная ошибка (попытка %d/%d)", label, attempt, MAX_ATTEMPTS)
            retry = True

        if retry and attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BASE_DELAY * (2 ** (attempt - 1)))
        elif retry:
            break

    # Все попытки исчерпаны — это сбой провайдера (а не «нет подходящего стандарта»).
    return {"standard_id": None, "confidence": 0.0, "reason": last_reason, "error": True}


# --------------------------------------------------------------------------- #
# Декомпозиция строки сметы на отдельно закупаемые позиции (вложения)
# --------------------------------------------------------------------------- #
DECOMPOSITION_SYSTEM_PROMPT = """
Ты — эксперт по закупкам учебного оборудования (44-ФЗ). Тебе дают ОДНУ строку
сметы: наименование и её характеристики. Реши, это ОДИН товар или НАБОР из
нескольких самостоятельно закупаемых позиций, и верни список позиций для подбора.

КАК РАЗЛИЧАТЬ:
- ОДИН ТОВАР (is_bundle=false): характеристики — это ЧАСТИ/КОМПЛЕКТУЮЩИЕ/параметры
  одного изделия (например у прибора: соленоид, катушки, провода, датчик, корпус,
  напряжение, материал, габариты). По отдельности школа их не закупает — это
  детали одного устройства. Тогда верни ровно одну позицию = само наименование
  строки.
- НАБОР (is_bundle=true): характеристики — это САМОСТОЯТЕЛЬНЫЕ изделия, каждое из
  которых школа могла бы купить отдельно (например: «Комплект портретов…»,
  «Комплект репродукций…», «Комплект демонстрационных таблиц…», «Прибор для
  опытов…», «Коллекция…», «Модель…»). Тогда верни СПИСОК таких вложений — по
  одному пункту на каждое самостоятельное изделие. Параметры-уточнения
  (предметная область, «наличие», «соответствие», размеры) в список НЕ выноси.

ПРАВИЛА:
1. Имя вложения бери из характеристики как есть, но коротко и пригодно для поиска
   (без «не менее…», без длинных условий).
2. Не выдумывай вложения, которых нет в характеристиках.
3. Если сомневаешься — выбирай ОДИН товар (is_bundle=false).
4. quantity_per_set — сколько таких изделий в одном наборе (по характеристике;
   по умолчанию 1).

ФОРМАТ ОТВЕТА — строгий JSON без markdown:
{
  "is_bundle": <true|false>,
  "items": [
    {"name": "<наименование позиции для подбора>", "quantity_per_set": <число>}
  ]
}
"""


def _build_decomp_prompt(line_data: dict) -> str:
    chars = line_data.get("characteristics", [])
    chars_text = "\n".join(
        f"- {c.get('name', '')}: {c.get('value', '')} {c.get('unit', '')}".rstrip()
        for c in chars
    ) or "(нет характеристик)"
    return f"""
СТРОКА СМЕТЫ:
Наименование: {line_data.get('name', 'Не указано')}

ХАРАКТЕРИСТИКИ:
{chars_text}

Определи, один это товар или набор, и верни позиции для подбора.
"""


async def get_llm_decomposition(line_data: dict,
                                provider: str | None = None) -> dict:
    """Разложить строку сметы на отдельно закупаемые позиции.
    Возвращает {is_bundle, items:[{name, quantity_per_set}]} либо {error:True}."""
    user_prompt = _build_decomp_prompt(line_data)
    res = await _dispatch(DECOMPOSITION_SYSTEM_PROMPT, user_prompt, provider)
    if res.get("error"):
        return res
    # Нормализуем форму ответа.
    items = res.get("items") or []
    norm = []
    for it in items:
        name = (it.get("name") or "").strip() if isinstance(it, dict) else str(it).strip()
        if not name:
            continue
        qps = it.get("quantity_per_set", 1) if isinstance(it, dict) else 1
        try:
            qps = float(qps)
        except (TypeError, ValueError):
            qps = 1.0
        norm.append({"name": name, "quantity_per_set": qps})
    return {"is_bundle": bool(res.get("is_bundle")) and len(norm) > 1, "items": norm}