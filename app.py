import os
import json
import re
import mimetypes
import base64
from io import BytesIO
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_cors import CORS
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from supabase import create_client, Client
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from docx import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from xml.sax.saxutils import escape


load_dotenv()


def get_env(name: str, legacy_name: str | None = None, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    if legacy_name:
        value = os.getenv(legacy_name)
        if value:
            return value
    return default


app = Flask(__name__, template_folder='templates')
app.secret_key = get_env('SECRET_KEY', 'REACT_APP_SECRET_KEY', 'ai_traveler_secret_key_2026')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
CORS(app, resources={r"/api/*": {"origins": "*"}})

GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
GEMINI_MODEL = get_env("GEMINI_MODEL", "REACT_APP_GEMINI_MODEL", GEMINI_FALLBACK_MODEL)
GEMINI_TIMEOUT_MS = int(get_env("GEMINI_TIMEOUT_MS", "REACT_APP_GEMINI_TIMEOUT_MS", "60000"))
GEMINI_EMBEDDING_MODEL = get_env("GEMINI_EMBEDDING_MODEL", "REACT_APP_GEMINI_EMBEDDING_MODEL", default="gemini-embedding-2")
GEMINI_EMBEDDING_DIMS = int(get_env("GEMINI_EMBEDDING_DIMS", "REACT_APP_GEMINI_EMBEDDING_DIMS", default="768"))
GROQ_VISION_MODEL = get_env("GROQ_VISION_MODEL", "REACT_APP_GROQ_VISION_MODEL", default="qwen/qwen3.8-27b")
OPENROUTER_MODEL = get_env("OPENROUTER_MODEL", "REACT_APP_OPENROUTER_MODEL", default="inclusionai/ling-3.2-flash:free")
ROUTE_OPTIMIZATION_GUIDANCE = """
路線順暢是重要要求：先把地理位置相近的地點排在同一時段或同一天，沿著同一方向逐區移動；
避免景點間來回折返、跨區後又返回原區、重複造訪同一地點。依實際合理的交通時間排序，
預留轉乘與步行時間；若距離或交通資訊不確定，優先選擇鄰近景點並避免宣稱精確時間。
"""
MEAL_AND_ATTRACTION_GUIDANCE = """
除非旅客的明確條件另有要求，每天安排早餐、午餐、晚餐各一個餐飲活動，category 設為「食物」，
並用 meal 欄位分別標示「早餐」、「午餐」、「晚餐」；餐飲活動需安排在合理用餐時段，優先提供真實店名。
除此之外，每天安排 3 至 4 個 category 為「景點」的明確景點。每天總活動數為 6 至 7 個，
不要把交通、休息或泛稱的「自由活動」當成餐點或景點。
"""
DAILY_ACCOMMODATION_GUIDANCE = """
每一天都要依據當日景點的地理分布，推薦一個具體、真實的行政區或街區作為住宿區域，
優先選擇靠近當日主要景點群且方便隔日交通的地點；用 accommodationReason 簡短說明選擇原因。
不同天的行程區域不同時，住宿建議也應相應調整，不要每天都推薦同一個車站周邊。
"""
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
UPLOAD_BUCKET = 'user-files'

spots = [
    {'name': '雷門', 'emoji': '⛩️', 'tag': '景點', 'category': 'attraction', 'desc': '充滿歷史感的紅色大燈籠。', 'rating': 4.8},
    {'name': '明治神宮', 'emoji': '⛩️', 'tag': '景點', 'category': 'attraction', 'desc': '東京市中心的森林神社。', 'rating': 4.7},
    {'name': '一蘭拉麵', 'emoji': '🍜', 'tag': '美食', 'category': 'food', 'desc': '豚骨拉麵的經典代表作。', 'rating': 4.5},
    {'name': '築地市場', 'emoji': '🍣', 'tag': '美食', 'category': 'food', 'desc': '新鮮海鮮與在地小吃集中地。', 'rating': 4.7},
    {'name': '淺草豪景飯店', 'emoji': '🏨', 'tag': '住宿', 'category': 'hotel', 'desc': '適合探索淺草與市區的住宿選擇。', 'rating': 4.4},
    {'name': '新宿格蘭貝爾飯店', 'emoji': '🏨', 'tag': '住宿', 'category': 'hotel', 'desc': '交通便利、適合城市旅行的住宿選擇。', 'rating': 4.3},
]

# ==========================================
# Supabase 設定
# ==========================================

SUPABASE_URL = get_env("SUPABASE_URL", "REACT_APP_SUPABASE_URL")
SUPABASE_KEY = get_env("SUPABASE_KEY", "REACT_APP_SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be configured.")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_gemini_client() -> genai.Client | None:
    api_key = (
        get_env("GEMINI_API_KEY", "REACT_APP_GEMINI_API_KEY")
        or os.getenv("REACT_APP_EXPLORE_GEMINI_API_KEY")
    )
    if not api_key:
        return None
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


def get_gemini_embedding_client() -> genai.Client | None:
    """優先使用 Embedding 專用 key，沒有的話再 fallback 到一般 Gemini key。"""
    api_key = (
        get_env("GEMINI_EMBEDDING_API_KEY", "REACT_APP_GEMINI_EMBEDDING_API_KEY")
        or get_env("GEMINI_API_KEY", "REACT_APP_GEMINI_API_KEY")
        or os.getenv("REACT_APP_EXPLORE_GEMINI_API_KEY")
    )
    if not api_key:
        return None
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


def parse_model_json(text: str) -> Any:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    return json.loads(cleaned)


def generate_gemini_content(client: genai.Client, contents: Any, config: Any = None) -> Any:
    try:
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
    except genai_errors.ClientError as error:
        if error.code != 404 or GEMINI_MODEL == GEMINI_FALLBACK_MODEL:
            raise
        app.logger.warning(
            "Gemini model %s is unavailable; retrying with %s.",
            GEMINI_MODEL,
            GEMINI_FALLBACK_MODEL,
        )
        return client.models.generate_content(
            model=GEMINI_FALLBACK_MODEL,
            contents=contents,
            config=config,
        )


def generate_gemini_json(prompt: str, contents: Any = None) -> Any:
    client = get_gemini_client()
    if client is None:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    response = generate_gemini_content(
        client,
        contents=contents if contents is not None else prompt,
        config={
            "system_instruction": (
                "你是旅遊規劃助手。只回傳有效 JSON，不要 Markdown 或額外文字。"
            ),
            "response_mime_type": "application/json",
            "temperature": 0.2,
            "max_output_tokens": 8192,
        },
    )
    if not response.text:
        raise ValueError("Gemini returned an empty response.")
    return parse_model_json(response.text)


def cosine_similarity(vec_a: list[float] | np.ndarray, vec_b: list[float] | np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    a = np.asarray(vec_a, dtype=np.float64)
    b = np.asarray(vec_b, dtype=np.float64)
    if a.shape != b.shape or a.size == 0:
        return 0.0
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def embed_texts_with_gemini(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using gemini-embedding-2 and return vectors."""
    client = get_gemini_embedding_client()
    if client is None:
        raise RuntimeError(
            "GEMINI_EMBEDDING_API_KEY 或 GEMINI_API_KEY 尚未設定。"
        )
    if not texts:
        return []

    # Prefer task-aware formatting for retrieval-style similarity.
    prepared = [
        f"title: none | text: {text.strip()}" if text.strip() else "title: none | text: empty"
        for text in texts
    ]
    result = client.models.embed_content(
        model=GEMINI_EMBEDDING_MODEL,
        contents=prepared,
        config=types.EmbedContentConfig(output_dimensionality=GEMINI_EMBEDDING_DIMS),
    )
    embeddings = getattr(result, "embeddings", None) or []
    vectors: list[list[float]] = []
    for emb in embeddings:
        values = getattr(emb, "values", None)
        if values is None and isinstance(emb, dict):
            values = emb.get("values")
        if not values:
            raise ValueError("Gemini embedding response is missing vector values.")
        vectors.append([float(v) for v in values])
    if len(vectors) != len(texts):
        raise ValueError(
            f"Expected {len(texts)} embeddings but received {len(vectors)}."
        )
    return vectors


def rank_candidates_by_embedding(
    query_text: str,
    candidates: list[dict[str, Any]],
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """
    Rank candidate spots by cosine similarity of gemini-embedding-2 vectors.
    Each candidate should have at least name / reason (or desc) fields.
    Returns top_k candidates enriched with similarityScore.
    """
    if not candidates:
        return []
    query = (query_text or "").strip()
    if not query:
        return candidates[:top_k]

    candidate_texts = []
    for c in candidates:
        name = str(c.get("name") or "").strip()
        reason = str(c.get("reason") or c.get("desc") or c.get("description") or "").strip()
        address = str(c.get("address") or "").strip()
        text = " | ".join(part for part in (name, reason, address) if part)
        candidate_texts.append(text or name or "unknown")

    all_texts = [query] + candidate_texts
    vectors = embed_texts_with_gemini(all_texts)
    query_vec = vectors[0]
    scored: list[tuple[float, dict[str, Any]]] = []
    for idx, candidate in enumerate(candidates):
        score = cosine_similarity(query_vec, vectors[idx + 1])
        enriched = dict(candidate)
        enriched["similarityScore"] = round(score, 4)
        scored.append((score, enriched))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[: max(1, top_k)]]


def search_spot_embeddings(
    query_vector: list[float],
    *,
    city: str | None = None,
    top_k: int = 2,
    min_similarity: float = 0.72,
) -> list[dict[str, Any]]:
    """Query pgvector via Supabase RPC. Returns [] if table/RPC unavailable."""
    try:
        payload: dict[str, Any] = {
            "query_embedding": query_vector,
            "match_count": top_k,
            "min_similarity": min_similarity,
        }
        if city:
            payload["filter_city"] = city
        response = supabase.rpc("match_spot_embeddings", payload).execute()
        rows = response.data or []
        spots: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            spots.append({
                "name": name,
                "emoji": str(row.get("emoji") or "📍").strip() or "📍",
                "reason": str(row.get("description") or "").strip(),
                "address": str(row.get("address") or "").strip(),
                "city": str(row.get("city") or "").strip(),
                "similarityScore": round(float(row.get("similarity") or 0), 4),
                "mapsUrl": google_maps_search_url(
                    str(row.get("address") or name).strip(),
                    str(row.get("city") or "").strip(),
                ),
            })
        return spots
    except Exception as error:
        app.logger.warning("pgvector search unavailable: %s", error)
        return []


def upsert_spot_embeddings(
    spots: list[dict[str, Any]],
    vectors: list[list[float]],
    *,
    city: str | None = None,
    source: str = "ai",
) -> None:
    """Best-effort write of ranked spots into the shared vector knowledge base."""
    if not spots or not vectors or len(spots) != len(vectors):
        return
    rows = []
    for spot, vector in zip(spots, vectors):
        name = str(spot.get("name") or "").strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "emoji": str(spot.get("emoji") or "📍").strip() or "📍",
            "category": str(spot.get("category") or "attraction").strip() or "attraction",
            "description": str(
                spot.get("reason") or spot.get("description") or spot.get("desc") or ""
            ).strip(),
            "address": str(spot.get("address") or "").strip(),
            "city": (city or str(spot.get("city") or "").strip() or None),
            "source": source,
            "embedding": vector,
            "metadata": {
                "mapsUrl": spot.get("mapsUrl"),
                "similarityScore": spot.get("similarityScore"),
            },
        })
    if not rows:
        return
    try:
        # Requires service-role key (typical for server-side SUPABASE_KEY)
        # or an insert policy for authenticated users.
        supabase.table("spot_embeddings").upsert(
            rows,
            on_conflict="name,city",
        ).execute()
    except Exception as error:
        # Unique index is on (name, coalesce(city,'')); some clients need manual merge.
        app.logger.warning("spot_embeddings upsert failed, trying per-row insert: %s", error)
        for row in rows:
            try:
                existing = (
                    supabase.table("spot_embeddings")
                    .select("id")
                    .eq("name", row["name"])
                    .eq("city", row["city"])
                    .limit(1)
                    .execute()
                    .data
                    or []
                )
                if existing:
                    supabase.table("spot_embeddings").update({
                        "emoji": row["emoji"],
                        "description": row["description"],
                        "address": row["address"],
                        "embedding": row["embedding"],
                        "metadata": row["metadata"],
                        "source": row["source"],
                    }).eq("id", existing[0]["id"]).execute()
                else:
                    supabase.table("spot_embeddings").insert(row).execute()
            except Exception as row_error:
                app.logger.warning(
                    "Failed to persist spot embedding for %s: %s",
                    row.get("name"),
                    row_error,
                )


def infer_city_from_text(*parts: str) -> str | None:
    """Lightweight city hint extraction for filtering vector search."""
    text = " ".join(p for p in parts if p)
    known = [
        "東京", "大阪", "京都", "名古屋", "福岡", "札幌", "橫濱", "神戶", "奈良",
        "台北", "臺北", "台中", "臺中", "台南", "臺南", "高雄", "新北", "桃園",
        "首爾", "釜山", "濟州",
        "曼谷", "清邁", "普吉",
        "新加坡",
        "巴黎", "倫敦", "紐約", "洛杉磯", "舊金山", "雪梨", "墨爾本",
        "上海", "北京", "香港", "澳門", "深圳", "廣州", "成都", "杭州",
    ]
    for city in known:
        if city in text:
            return city
    return None


class OpenRouterAPIError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"OpenRouter API returned HTTP {status_code}.")


def generate_openrouter_json(prompt: str, max_tokens: int = 2048) -> Any:
    api_key = get_env("OPENROUTER_API_KEY", "REACT_APP_OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是旅遊規劃助手。只回傳有效 JSON，不要 Markdown 或額外文字。",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    api_request = Request(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "AI Travel Assistant",
            "User-Agent": "AITravelAssistant/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(api_request, timeout=60) as response:
            response_data = json.loads(response.read())
    except HTTPError as error:
        raise OpenRouterAPIError(error.code) from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("Could not connect to the OpenRouter API.") from error

    choices = response_data.get("choices") or []
    message = choices[0].get("message") if choices else None
    output_text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("OpenRouter returned no recommendation result.")
    return parse_model_json(output_text)


def generate_planner_json(prompt: str) -> tuple[Any, str]:
    try:
        return generate_gemini_json(prompt), "gemini"
    except RuntimeError as error:
        if "GEMINI_API_KEY is not configured" not in str(error):
            raise
        app.logger.warning("Gemini API key is not configured; using OpenRouter for itinerary generation.")
    except genai_errors.ServerError as error:
        if error.code != 503:
            raise
        app.logger.warning("Gemini returned HTTP 503; retrying itinerary generation with OpenRouter.")
    return generate_openrouter_json(prompt, max_tokens=8192), "openrouter"


class GroqAPIError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Groq API returned HTTP {status_code}.")


def generate_groq_image_json(prompt: str, image_bytes: bytes, mime_type: str) -> Any:
    api_key = get_env("GROQ_API_KEY", "REACT_APP_GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    image_data_url = (
        f"data:{mime_type};base64,"
        f"{base64.b64encode(image_bytes).decode('ascii')}"
    )
    payload = json.dumps({
        "model": GROQ_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 1200,
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    api_request = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AITravelAssistant/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(api_request, timeout=60) as response:
            response_data = json.loads(response.read())
    except HTTPError as error:
        raise GroqAPIError(error.code) from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("Could not connect to the Groq API.") from error

    choices = response_data.get("choices") or []
    message = choices[0].get("message") if choices else None
    output_text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("Groq returned no image-recognition result.")
    return parse_model_json(output_text)


class OpenMeteoError(RuntimeError):
    pass


def open_meteo_get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    api_request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "AITravelAssistant/1.0"},
    )
    try:
        with urlopen(api_request, timeout=15) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise OpenMeteoError(f"Open-Meteo returned HTTP {error.code}.") from error
    except (URLError, TimeoutError) as error:
        raise OpenMeteoError("Could not connect to Open-Meteo.") from error
    except json.JSONDecodeError as error:
        raise OpenMeteoError("Open-Meteo returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise OpenMeteoError("Open-Meteo returned an invalid response.")
    return result


def open_meteo_weather_code(code: int) -> str:
    return {
        0: "晴朗",
        1: "大致晴朗",
        2: "局部多雲",
        3: "陰天",
        45: "有霧",
        48: "霧凇",
        51: "毛毛雨",
        53: "毛毛雨",
        55: "大毛毛雨",
        56: "凍毛毛雨",
        57: "凍毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        66: "凍雨",
        67: "凍雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        77: "雪粒",
        80: "短暫陣雨",
        81: "陣雨",
        82: "強陣雨",
        85: "短暫陣雪",
        86: "陣雪",
        95: "雷雨",
        96: "雷雨伴隨冰雹",
        99: "雷雨伴隨冰雹",
    }.get(code, "天氣多變")


def get_weather_forecast(
    city: str,
    days: int,
    start_date: datetime,
) -> tuple[list[dict[str, Any]], str]:
    weather = [
        {
            "day": day,
            "date": (start_date + timedelta(days=day - 1)).strftime("%Y-%m-%d"),
            "condition": "暫無預報",
            "temperature": "暫無預報",
            "rain": None,
            "available": False,
        }
        for day in range(1, days + 1)
    ]
    last_date = (start_date + timedelta(days=days - 1)).date()
    today = datetime.now().date()
    forecast_limit = today + timedelta(days=15)
    if start_date.date() < today:
        return weather, ""
    if start_date.date() > forecast_limit:
        for day in weather:
            day["condition"] = "超出預測範圍"
        return weather, ""

    forecast_end = min(last_date, forecast_limit)
    for day in weather:
        if datetime.strptime(day["date"], "%Y-%m-%d").date() > forecast_limit:
            day["condition"] = "超出預測範圍"

    try:
        geocoding = open_meteo_get_json(OPEN_METEO_GEOCODING_URL, {
            "name": city,
            "count": "1",
            "language": "zh",
            "format": "json",
        })
        locations = geocoding.get("results") or []
        if not locations:
            return weather, f"Open-Meteo 找不到「{city}」的位置，暫時無法提供天氣預報。"
        location = locations[0]
        forecast = open_meteo_get_json(OPEN_METEO_FORECAST_URL, {
            "latitude": str(location["latitude"]),
            "longitude": str(location["longitude"]),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": "auto",
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": forecast_end.strftime("%Y-%m-%d"),
        })
        daily = forecast.get("daily") or {}
        dates = daily.get("time") or []
        weather_by_date = {day["date"]: day for day in weather}
        for index, date_text in enumerate(dates):
            day = weather_by_date.get(date_text)
            if day is None:
                continue
            maximum = daily["temperature_2m_max"][index]
            minimum = daily["temperature_2m_min"][index]
            rain = daily["precipitation_probability_max"][index]
            day.update({
                "condition": open_meteo_weather_code(int(daily["weather_code"][index])),
                "temperature": f"{minimum:.0f}-{maximum:.0f}°C",
                "rain": rain,
                "available": True,
            })
        if not any(day["available"] for day in weather):
            raise OpenMeteoError("Open-Meteo returned no daily forecast.")
        status = f"天氣資料來源：Open-Meteo（{location.get('name', city)}）"
        return weather, status
    except (IndexError, KeyError, TypeError, ValueError) as error:
        app.logger.warning("Open-Meteo returned an invalid forecast for %s: %s", city, error)
        return weather, "Open-Meteo 回傳的天氣資料格式無效，暫時無法提供預報。"
    except OpenMeteoError as error:
        app.logger.warning("Open-Meteo forecast unavailable for %s: %s", city, error)
        return weather, "Open-Meteo 天氣服務目前無法使用，請稍後再試。"


def google_maps_search_url(place: str, city: str = '') -> str:
    query = ', '.join(part for part in (place, city) if part)
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


def google_maps_directions_url(places: list[str], city: str = '') -> str | None:
    destinations = [', '.join(part for part in (place, city) if part) for place in places if place]
    if not destinations:
        return None
    params = [
        'api=1',
        f'origin={quote(destinations[0])}',
        f'destination={quote(destinations[-1])}',
        'travelmode=transit',
    ]
    if len(destinations) > 2:
        params.append(f"waypoints={quote('|'.join(destinations[1:-1]))}")
    return f"https://www.google.com/maps/dir/?{'&'.join(params)}"


def normalize_activity(
    activity: Any,
    city: str,
    index: int,
    include_maps: bool = True,
) -> dict[str, str]:
    if isinstance(activity, dict):
        name = str(activity.get('name') or activity.get('place') or activity.get('location') or '').strip()
        place = str(activity.get('place') or activity.get('name') or name).strip()
        category = str(activity.get('category') or '活動').strip()
        normalized = {
            'time': str(activity.get('time') or f'時段 {index}').strip(),
            'name': name or place or '未命名行程',
            'place': place or name or '未指定地點',
            'address': str(activity.get('address') or '').strip(),
            'category': category,
            'description': str(activity.get('description') or '').strip(),
        }
        meal = str(activity.get('meal') or '').strip()
        if meal in {'早餐', '午餐', '晚餐'}:
            normalized['meal'] = meal
    else:
        place = str(activity or '').strip()
        normalized = {
            'time': f'時段 {index}',
            'name': place or '未命名行程',
            'place': place or '未指定地點',
            'address': '',
            'category': '活動',
            'description': '',
        }
    if include_maps:
        normalized['mapsUrl'] = google_maps_search_url(
            normalized['address'] or normalized['place'], city
        )
    return normalized


def normalize_day(
    day_plan: Any,
    city: str,
    day_number: int,
    date_text: str,
    include_maps: bool = True,
) -> dict[str, Any]:
    source = day_plan if isinstance(day_plan, dict) else {}
    activities = source.get('activities') or []
    if not isinstance(activities, list):
        activities = [activities]
    normalized_activities = [
        normalize_activity(activity, city, index, include_maps=include_maps)
        for index, activity in enumerate(activities, start=1)
    ]
    for index in range(1, len(normalized_activities)):
        previous = normalized_activities[index - 1]
        current = normalized_activities[index]
        normalized_activities[index]["transportUrl"] = google_maps_directions_url(
            [
                previous["address"] or previous["place"],
                current["address"] or current["place"],
            ],
            city,
        ) or ""
    places = [
        activity['address'] or activity['place']
        for activity in normalized_activities
        if activity['place'] != '未指定地點'
    ]
    normalized_day = {
        'day': day_number,
        'date': str(source.get('date') or date_text),
        'title': str(source.get('title') or f'{city} 第 {day_number} 天'),
        'description': str(source.get('description') or '').strip(),
        'accommodationArea': str(source.get('accommodationArea') or '').strip(),
        'accommodationReason': str(source.get('accommodationReason') or '').strip(),
        'activities': normalized_activities,
    }
    if include_maps:
        normalized_day['mapsUrl'] = google_maps_directions_url(places, city)
    return normalized_day


def has_required_daily_meals_and_attractions(day_plan: Any) -> bool:
    if not isinstance(day_plan, dict):
        return False
    activities = day_plan.get('activities')
    if not isinstance(activities, list) or not 6 <= len(activities) <= 7:
        return False

    meal_activities = [
        activity for activity in activities
        if isinstance(activity, dict)
        and str(activity.get('category') or '').strip() in {'食物', '美食', 'food'}
    ]
    meals = {
        str(activity.get('meal') or '').strip()
        for activity in meal_activities
    }
    attraction_count = sum(
        1 for activity in activities
        if isinstance(activity, dict)
        and str(activity.get('category') or '').strip() in {'景點', 'attraction'}
    )
    return (
        len(meal_activities) == 3
        and meals == {'早餐', '午餐', '晚餐'}
        and 3 <= attraction_count <= 4
    )


def get_authenticated_client() -> tuple[Any, Any]:
    user_id = session.get('user_id')
    access_token = session.get('access_token')
    refresh_token = session.get('refresh_token')
    if not user_id or not access_token or not refresh_token:
        return None, None

    try:
        user_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        user_client.auth.set_session(access_token, refresh_token)
        return user_client, user_id
    except Exception:
        return None, None


def authentication_required() -> tuple[Any, Any, Any]:
    user_client, user_id = get_authenticated_client()
    if not user_client:
        return None, None, (jsonify({'error': '請先登入才能使用此功能。'}), 401)
    return user_client, user_id, None


def store_auth_session(auth_session: Any) -> None:
    """Store Supabase's authenticated user and tokens in the Flask session."""
    if not auth_session or not auth_session.user or not auth_session.session:
        raise ValueError("Supabase returned an incomplete authentication session.")

    user = auth_session.user
    tokens = auth_session.session
    user_meta = user.user_metadata or {}
    display_name = str(user_meta.get('full_name') or user_meta.get('name') or '').strip()

    session['user'] = user.email
    session['user_id'] = user.id
    session['access_token'] = tokens.access_token
    session['refresh_token'] = tokens.refresh_token
    session['name'] = display_name or user.email
    session['gender'] = user_meta.get('gender')


# ==========================================
# 路由：首頁
# ==========================================
@app.route("/")
def home():
    # Supabase may use the configured Site URL and return the code to "/".
    if request.args.get('code'):
        return redirect(url_for('auth_callback', **request.args.to_dict(flat=True)))

    # 從 session 中獲取當前用戶的資訊
    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    # 渲染首頁模板，並傳遞用戶資訊
    return render_template("index.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

# ==========================================
# 路由：其他頁面
# ==========================================
@app.route("/planner")
def planner():

    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("planner.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

@app.route("/explore")
def explore():

    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("explore.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

@app.route("/favorites")
def favorites():
    current_user = session.get('user')
    if not current_user:
        flash('請先登入才能使用我的收藏。', 'warning')
        return redirect(url_for('login'))

    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("favorites.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender,
                           favorites=[])

@app.route("/about")
def about():
    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("about.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

@app.route("/recognize")
def recognize():
    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("recognize.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "請提供有效的 JSON 資料。"}), 400

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "prompt 必須是不可為空的文字。"}), 400

    client = get_gemini_client()
    if client is None:
        app.logger.error("GEMINI_API_KEY is not configured.")
        return jsonify({"error": "Gemini API 尚未設定，請確認 GEMINI_API_KEY 環境變數。"}), 503

    try:
        response = generate_gemini_content(
            client,
            contents=prompt.strip(),
        )
        return jsonify({"response": response.text})
    except Exception:
        app.logger.exception("Gemini API request failed.")
        return jsonify({"error": "Gemini API 呼叫失敗，請稍後再試。"}), 502


# ==========================================
# API：生成行程
# ==========================================
@app.route("/api/generate_itinerary", methods=['POST'])
def generate_itinerary():
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    start_date_text = str(data.get('startDate', '')).strip()
    style = str(data.get('style', 'balanced')).strip() or 'balanced'
    conditions = str(data.get('conditions', '')).strip()
    budget = data.get('budget')
    if len(conditions) > 1000:
        return jsonify({'error': '旅遊條件不可超過 1000 個字。'}), 400
    try:
        days = max(1, min(14, int(data.get('days', 1))))
    except (TypeError, ValueError):
        return jsonify({'error': '旅行天數格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請輸入目的地。'}), 400
    try:
        start_date = datetime.strptime(start_date_text, '%Y-%m-%d') if start_date_text else datetime.now()
    except ValueError:
        return jsonify({'error': '出發日期格式不正確。'}), 400

    prompt = f"""
規劃{city} {days}天旅行，日期從{start_date.strftime('%Y-%m-%d')}開始，風格：{style}，預算：{budget or '未指定'} TWD。
旅客的額外條件與特殊需求如下，必須優先遵守並反映在行程安排中：
「{conditions or '無額外條件'}」
如果條件與一般建議衝突，請以旅客條件為準；無法滿足時，請在當天 description 說明原因並提供替代方案。
{ROUTE_OPTIMIZATION_GUIDANCE}
{MEAL_AND_ATTRACTION_GUIDANCE}
{DAILY_ACCOMMODATION_GUIDANCE}
只回傳 JSON，不要解釋。每天的 activities 按照時間排序。
格式：{{"itinerary":[{{"day":1,"date":"YYYY-MM-DD","title":"短標題","description":"30字內摘要",
"accommodationArea":"住宿行政區或街區","accommodationReason":"依當日景點選擇的理由",
"activities":[{{"time":"08:00","name":"早餐：明確店名","place":"明確店名","category":"食物",
"meal":"早餐","description":"餐點簡介"}},{{"time":"09:00","name":"明確景點或店名",
"place":"明確地點名稱","address":"簡短地址","category":"景點","description":"20字內說明"}}]}}]}}
day 必須是 1 到 {days}，日期依序增加。category 只能是：食物、活動、購物、景點、酒店、交通、其他。
必須使用真實、明確的地點或店名，不要使用「探索市區」等泛稱。
"""
    try:
        result, source = generate_planner_json(prompt)
        itinerary = result.get('itinerary') if isinstance(result, dict) else None
        if not isinstance(itinerary, list) or len(itinerary) != days:
            raise ValueError("Gemini returned an invalid itinerary shape.")
        if any(not has_required_daily_meals_and_attractions(day_plan) for day_plan in itinerary):
            return jsonify({
                'error': '行程未符合每日三餐及 3 至 4 個景點的安排，請重新生成或調整旅遊條件。'
            }), 502
        normalized_itinerary = [
            normalize_day(
                day_plan,
                city,
                day_number,
                (start_date + timedelta(days=day_number - 1)).strftime('%Y-%m-%d'),
                include_maps=False,
            )
            for day_number, day_plan in enumerate(itinerary, start=1)
        ]
        return jsonify({
            'itinerary': normalized_itinerary,
            'insights': build_trip_insights(city, days, start_date, float(budget or 0), conditions),
            'source': source,
        })
    except OpenRouterAPIError as error:
        app.logger.error("OpenRouter itinerary fallback returned HTTP %s.", error.status_code)
        if error.status_code in {401, 403}:
            return jsonify({'error': 'OpenRouter API 金鑰無效或權限不足，請確認 OPENROUTER_API_KEY。'}), 502
        if error.status_code == 429:
            return jsonify({'error': 'OpenRouter 備援服務請求過多或額度不足，請稍後再試。'}), 502
        return jsonify({'error': 'Gemini 暫時無法使用，OpenRouter 備援服務也未能完成行程生成。'}), 502
    except RuntimeError as error:
        if "GEMINI_API_KEY is not configured" in str(error):
            app.logger.error("GEMINI_API_KEY is not configured.")
            return jsonify({'error': 'Gemini API 尚未設定，請確認 GEMINI_API_KEY 環境變數。'}), 503
        if "OPENROUTER_API_KEY is not configured" in str(error):
            app.logger.error("OPENROUTER_API_KEY is not configured for itinerary fallback.")
            return jsonify({'error': 'Gemini 暫時無法使用，請確認 OPENROUTER_API_KEY 環境變數以啟用備援。'}), 503
        app.logger.exception("Gemini itinerary generation failed.")
        return jsonify({'error': 'Gemini API 呼叫失敗，請確認 API key、模型名稱與部署設定。'}), 502
    except Exception:
        app.logger.exception("Gemini itinerary generation failed.")
        return jsonify({'error': 'AI 行程規劃失敗，請稍後再試。'}), 502

# ==========================================
# API：獲取探索景點
# ==========================================
@app.route("/api/explore_spots")
def get_explore_spots():
    category = request.args.get('category', 'all')
    location = str(request.args.get('location', '')).strip()
    condition = str(request.args.get('condition', '')).strip()
    if len(location) > 200:
        return jsonify({'error': '地點不可超過 200 個字。'}), 400
    if len(condition) > 500:
        return jsonify({'error': '推薦條件不可超過 500 個字。'}), 400
    category_name = {
        'all': '景點、美食與住宿',
        'attraction': '景點',
        'food': '美食',
        'hotel': '住宿',
    }.get(category)
    if category_name is None:
        return jsonify({'error': '無效的景點分類。'}), 400

    try:
        result = generate_gemini_json(
            f"""
請根據以下使用者輸入，推薦 6 個適合探索的{category_name}。這些輸入是硬性條件，不可忽略，也不可自行改成東京或其他預設地點。
使用者指定地點（所有推薦必須位於此地）：「{location or '未指定地點'}」
使用者推薦條件（每一筆推薦都必須符合）：「{condition or '未提供額外條件'}」
目前選擇分類（每一筆推薦都必須屬於此分類）：「{category_name}」
推薦內容彼此不要重複；若地點或條件不足以判斷，請選擇符合輸入的知名且可查證項目，不要捏造或使用與輸入無關的內容。
每個推薦需包含真實且適合旅遊的名稱、emoji、tag、category、簡短 desc 與 1 到 5 的 rating。
category 必須是 attraction、food 或 hotel；若目前選擇不是全部，category 必須符合目前選擇分類。
desc 必須明確說明它如何符合使用者的地點與推薦條件。
只回傳 JSON：{{"spots":[{{"name":"名稱","emoji":"圖示","tag":"景點、美食或住宿","category":"分類代碼","desc":"說明符合地點與條件的原因","rating":4.5}}]}}
"""
        )
        generated_spots = result.get('spots') if isinstance(result, dict) else None
        if not isinstance(generated_spots, list) or not generated_spots:
            raise ValueError("Gemini returned an invalid spots shape.")
        valid_categories = {'attraction', 'food', 'hotel'}
        category_aliases = {
            'attraction': 'attraction',
            '景點': 'attraction',
            'food': 'food',
            '美食': 'food',
            'hotel': 'hotel',
            '住宿': 'hotel',
        }
        valid_spots = []
        for spot in generated_spots:
            if not isinstance(spot, dict):
                continue
            raw_category = str(spot.get('category') or '').strip()
            spot_category = category_aliases.get(raw_category.lower(), category_aliases.get(raw_category, ''))
            name = str(spot.get('name') or '').strip()
            description = str(spot.get('desc') or '').strip()
            if (
                spot_category not in valid_categories
                or (category != 'all' and spot_category != category)
                or not name
                or not description
            ):
                continue
            spot['category'] = spot_category
            spot['mapsUrl'] = google_maps_search_url(
                name, location
            )
            valid_spots.append(spot)
        if not valid_spots:
            raise ValueError("Gemini returned no spots matching the selected category.")
        return jsonify({'spots': valid_spots[:6], 'source': 'gemini'})
    except RuntimeError as error:
        if "not configured" not in str(error):
            app.logger.exception("Explore Gemini generation failed.")
            return jsonify({
                'error': 'AI 推薦服務目前無法使用，請確認 Vercel 的 API 金鑰與模型設定。'
            }), 502
        app.logger.error("GEMINI_API_KEY is not configured.")
        if location or condition:
            return jsonify({
                'error': 'AI 推薦服務尚未設定，無法依據你的地點與條件生成推薦。'
            }), 503
        filtered = spots if category == 'all' else [spot for spot in spots if spot['category'] == category]
        return jsonify({
            'spots': [
                {**spot, 'mapsUrl': google_maps_search_url(spot['name'], location)}
                for spot in filtered
            ],
            'source': 'fallback',
        })
    except Exception:
        app.logger.exception("Gemini spot recommendation failed.")
        return jsonify({'error': 'AI 景點推薦失敗，請稍後再試。'}), 502


@app.get('/api/favorites')
def get_favorites():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        favorite_rows = user_client.table('ai_recommendation_favorites').select(
            'id, recommendation_id, created_at'
        ).eq('user_id', user_id).order('created_at', desc=True).execute().data or []
        recommendation_ids = [row['recommendation_id'] for row in favorite_rows]
        if not recommendation_ids:
            return jsonify({'favorites': []})

        recommendations = user_client.table('ai_recommendations').select('*').in_(
            'id', recommendation_ids
        ).eq('user_id', user_id).execute().data or []
        recommendations_by_id = {item['id']: item for item in recommendations}
        favorites = []
        for favorite in favorite_rows:
            recommendation = recommendations_by_id.get(favorite['recommendation_id'])
            if recommendation:
                item = recommendation.get('recommendation_data') or {}
                item = dict(item) if isinstance(item, dict) else {}
                item.setdefault('name', recommendation.get('recommendation_text', '未命名推薦'))
                item['recommendationId'] = recommendation['id']
                item['favoriteId'] = favorite['id']
                favorites.append(item)
        return jsonify({'favorites': favorites})
    except Exception as error:
        return jsonify({'error': f'讀取收藏失敗：{error}'}), 500


@app.post('/api/upload_itinerary')
def upload_itinerary():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    uploaded_file = request.files.get('file')
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify({'error': '請選擇要上傳的檔案。'}), 400

    filename = secure_filename(uploaded_file.filename) or f'uploaded-{uuid4().hex}'
    extension = os.path.splitext(filename)[1].lower()

    file_bytes = uploaded_file.read()
    if not file_bytes:
        return jsonify({'error': '檔案內容不可為空。'}), 400
    if len(file_bytes) > 10 * 1024 * 1024:
        return jsonify({'error': '檔案大小不可超過 10 MB。'}), 413
    title = str(request.form.get('title') or filename).strip()
    if not title:
        return jsonify({'error': '檔案標題不可為空。'}), 400
    if len(title) > 200:
        return jsonify({'error': '檔案標題不可超過 200 個字。'}), 400

    try:
        if extension == '.txt':
            try:
                text = file_bytes.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = file_bytes.decode('cp950')
        elif extension == '.docx':
            document = Document(BytesIO(file_bytes))
            paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            table_rows = [
                ' | '.join(cell.text.strip() for cell in row.cells)
                for table in document.tables
                for row in table.rows
            ]
            text = '\n'.join(paragraphs + [row for row in table_rows if row])
        elif extension == '.pdf':
            reader = PdfReader(BytesIO(file_bytes))
            text = '\n'.join(page.extract_text() or '' for page in reader.pages).strip()
        else:
            try:
                text = file_bytes.decode('utf-8-sig')
            except UnicodeDecodeError:
                try:
                    text = file_bytes.decode('cp950')
                except UnicodeDecodeError:
                    text = '此檔案格式已保存，但目前無法直接預覽內容。'
    except Exception:
        app.logger.exception('Itinerary file parsing failed.')
        return jsonify({'error': '檔案解析失敗，請確認檔案格式正確。'}), 422

    text = text.strip()
    if not text:
        text = '此檔案格式已保存，但檔案中沒有可讀取的文字內容。'
    storage_path = f'{user_id}/{uuid4().hex}-{filename}'
    mime_type = uploaded_file.mimetype or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    try:
        user_client.storage.from_(UPLOAD_BUCKET).upload(
            storage_path,
            file_bytes,
            file_options={'content-type': mime_type, 'upsert': False},
        )
        record = user_client.table('user_uploaded_files').insert({
            'user_id': user_id,
            'title': title,
            'original_filename': filename,
            'storage_path': storage_path,
            'mime_type': mime_type,
            'size_bytes': len(file_bytes),
            'extracted_text': text[:100000],
        }).execute().data[0]
    except Exception:
        try:
            user_client.storage.from_(UPLOAD_BUCKET).remove([storage_path])
        except Exception:
            app.logger.exception('Uploaded file cleanup failed.')
        app.logger.exception('Uploaded file persistence failed.')
        return jsonify({'error': '檔案儲存失敗，請稍後再試。'}), 500
    return jsonify({'file': serialize_uploaded_file(record)}), 201


def serialize_uploaded_file(record: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': record['id'],
        'title': record['title'],
        'fileName': record['original_filename'],
        'fileType': (os.path.splitext(record['original_filename'])[1][1:] or 'FILE').upper(),
        'mimeType': record.get('mime_type'),
        'sizeBytes': record.get('size_bytes'),
        'text': record.get('extracted_text') or '',
        'createdAt': record.get('created_at'),
    }


@app.get('/api/uploaded-files')
def get_uploaded_files():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        rows = user_client.table('user_uploaded_files').select(
            'id, title, original_filename, mime_type, size_bytes, extracted_text, created_at'
        ).eq('user_id', user_id).order('created_at', desc=True).execute().data or []
        return jsonify({'files': [serialize_uploaded_file(row) for row in rows]})
    except Exception as error:
        return jsonify({'error': f'讀取上傳檔案失敗：{error}'}), 500


@app.delete('/api/uploaded-files/<file_id>')
def delete_uploaded_file(file_id):
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        rows = user_client.table('user_uploaded_files').select('storage_path').eq(
            'id', file_id
        ).eq('user_id', user_id).limit(1).execute().data or []
        if not rows:
            return jsonify({'error': '找不到上傳檔案。'}), 404
        user_client.storage.from_(UPLOAD_BUCKET).remove([rows[0]['storage_path']])
        user_client.table('user_uploaded_files').delete().eq(
            'id', file_id
        ).eq('user_id', user_id).execute()
        return jsonify({'message': '已刪除上傳檔案。'})
    except Exception as error:
        return jsonify({'error': f'刪除上傳檔案失敗：{error}'}), 500


@app.post('/api/favorites')
def create_favorite():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'error': '缺少收藏內容。'}), 400

    try:
        recommendation = user_client.table('ai_recommendations').insert({
            'user_id': user_id,
            'recommendation_text': str(data.get('desc') or name),
            'recommendation_data': data,
            'source': str(data.get('source') or 'explore'),
        }).execute().data[0]
        favorite = user_client.table('ai_recommendation_favorites').insert({
            'user_id': user_id,
            'recommendation_id': recommendation['id'],
        }).execute().data[0]
        return jsonify({'favorite': {**data, 'recommendationId': recommendation['id'], 'favoriteId': favorite['id']}}), 201
    except Exception as error:
        return jsonify({'error': f'新增收藏失敗：{error}'}), 500


@app.delete('/api/favorites/<recommendation_id>')
def delete_favorite(recommendation_id):
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        user_client.table('ai_recommendation_favorites').delete().eq(
            'user_id', user_id
        ).eq('recommendation_id', recommendation_id).execute()
        return jsonify({'message': '已取消收藏。'})
    except Exception as error:
        return jsonify({'error': f'取消收藏失敗：{error}'}), 500


@app.post('/api/generations')
def create_generation_record():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    itinerary = data.get('itinerary')
    city = str(data.get('city', '')).strip()
    parent_generation_record_id = str(data.get('parentGenerationRecordId') or '').strip() or None
    if not city or not isinstance(itinerary, list) or not itinerary:
        return jsonify({'error': '缺少有效的行程資料。'}), 400

    try:
        if parent_generation_record_id:
            parent_rows = user_client.table('ai_generation_records').select('id').eq(
                'id', parent_generation_record_id
            ).eq('user_id', user_id).limit(1).execute().data or []
            if not parent_rows:
                return jsonify({'error': '找不到原始行程紀錄。'}), 404
        record = user_client.table('ai_generation_records').insert({
            'user_id': user_id,
            'record_type': 'itinerary',
            'title': f'{city} 行程' + ('（重新生成）' if parent_generation_record_id else ''),
            'prompt': str(data.get('prompt') or f'規劃 {city} 的旅行行程'),
            'generated_content': json.dumps(itinerary, ensure_ascii=False),
            'context_data': {
                'city': city,
                'days': data.get('days'),
                'startDate': data.get('startDate'),
                'budget': data.get('budget'),
                'style': data.get('style'),
                'conditions': data.get('conditions'),
                'parentGenerationRecordId': parent_generation_record_id,
            },
            'parent_generation_record_id': parent_generation_record_id,
            'model_name': 'demo-itinerary-generator',
            'request_payload': data,
            'response_payload': {'itinerary': itinerary},
        }).execute().data[0]
        return jsonify({'record': record}), 201
    except Exception as error:
        return jsonify({'error': f'儲存行程紀錄失敗：{error}'}), 500


@app.get('/api/generation-favorites')
def get_generation_favorites():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        rows = user_client.table('ai_generation_favorites').select(
            'id, generation_record_id, created_at, is_featured'
        ).eq('user_id', user_id).order('is_featured', desc=True).order(
            'created_at', desc=True
        ).execute().data or []
        record_ids = [row['generation_record_id'] for row in rows]
        records = []
        if record_ids:
            records = user_client.table('ai_generation_records').select('*').in_(
                'id', record_ids
            ).eq('user_id', user_id).execute().data or []
        records_by_id = {record['id']: record for record in records}
        return jsonify({
            'favorites': [
                {**row, 'record': records_by_id[row['generation_record_id']]}
                for row in rows if row['generation_record_id'] in records_by_id
            ]
        })
    except Exception as error:
        return jsonify({'error': f'讀取行程收藏失敗：{error}'}), 500


@app.post('/api/generation-pdf')
def create_generation_pdf():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    title = str(data.get('title') or 'AI-旅遊行程').strip()
    text = str(data.get('text') or '').strip()
    if not text:
        return jsonify({'error': '缺少有效的行程內容。'}), 400

    try:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=50, leftMargin=50,
            topMargin=50, bottomMargin=50
        )
        styles = getSampleStyleSheet()
        style = styles['BodyText'].clone('itinerary')
        style.fontName = 'STSong-Light'
        style.fontSize = 11
        style.leading = 18
        story = [Paragraph(escape(title), style), Spacer(1, 16)]
        for line in text.splitlines():
            story.append(Paragraph(escape(line) or '&nbsp;', style))
        document.build(story)
        buffer.seek(0)
        return send_file(
            buffer, mimetype='application/pdf', as_attachment=True,
            download_name=f'{title}.pdf'
        )
    except Exception as error:
        return jsonify({'error': f'產生 PDF 失敗：{error}'}), 500


@app.post('/api/generation-favorites')
def create_generation_favorite():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    generation_record_id = str(data.get('generationRecordId', '')).strip()
    if not generation_record_id:
        return jsonify({'error': '缺少生成紀錄 ID。'}), 400
    try:
        record_rows = user_client.table('ai_generation_records').select('id').eq(
            'id', generation_record_id
        ).eq('user_id', user_id).limit(1).execute().data or []
        if not record_rows:
            return jsonify({'error': '找不到可收藏的行程紀錄。'}), 404

        existing_rows = user_client.table('ai_generation_favorites').select('*').eq(
            'user_id', user_id
        ).eq('generation_record_id', generation_record_id).limit(1).execute().data or []
        if existing_rows:
            return jsonify({'favorite': existing_rows[0]}), 200

        favorite = user_client.table('ai_generation_favorites').insert({
            'user_id': user_id,
            'generation_record_id': generation_record_id,
            'is_featured': False,
        }).execute().data[0]
        return jsonify({'favorite': favorite}), 201
    except Exception as error:
        return jsonify({'error': f'收藏行程失敗：{error}'}), 500


@app.delete('/api/generation-favorites/<generation_record_id>')
def delete_generation_favorite(generation_record_id):
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        user_client.table('ai_generation_favorites').delete().eq(
            'user_id', user_id
        ).eq('generation_record_id', generation_record_id).execute()
        return jsonify({'message': '已取消行程收藏。'})
    except Exception as error:
        return jsonify({'error': f'取消行程收藏失敗：{error}'}), 500


@app.patch('/api/generation-favorites/<generation_record_id>/feature')
def feature_generation_favorite(generation_record_id):
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        favorite_rows = user_client.table('ai_generation_favorites').select('id').eq(
            'user_id', user_id
        ).eq('generation_record_id', generation_record_id).limit(1).execute().data or []
        if not favorite_rows:
            return jsonify({'error': '找不到可設定的收藏行程。'}), 404
        current_rows = user_client.table('ai_generation_favorites').select('is_featured').eq(
            'user_id', user_id
        ).eq('generation_record_id', generation_record_id).limit(1).execute().data or []
        is_currently_featured = bool(current_rows[0].get('is_featured')) if current_rows else False
        user_client.table('ai_generation_favorites').update({
            'is_featured': False,
        }).eq('user_id', user_id).execute()
        favorite = user_client.table('ai_generation_favorites').update({
            'is_featured': not is_currently_featured,
        }).eq('user_id', user_id).eq(
            'generation_record_id', generation_record_id
        ).execute().data[0]
        return jsonify({'favorite': favorite})
    except Exception as error:
        return jsonify({'error': f'設定首選行程失敗：{error}'}), 500


@app.post('/api/search_history')
def create_search_history():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    search_query = str(data.get('searchQuery', '')).strip()
    if not search_query:
        return jsonify({'error': '搜尋內容不可為空。'}), 400
    try:
        record = user_client.table('search_history').insert({
            'user_id': user_id,
            'search_query': search_query,
            'search_filters': data.get('filters') or {},
        }).execute().data[0]
        return jsonify({'record': record}), 201
    except Exception as error:
        return jsonify({'error': f'儲存搜尋紀錄失敗：{error}'}), 500


@app.post('/api/trip_insights')
def trip_insights():
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    start_date_text = str(data.get('startDate', '')).strip()
    conditions = str(data.get('conditions', '')).strip()
    if len(conditions) > 1000:
        return jsonify({'error': '旅遊條件不可超過 1000 個字。'}), 400
    try:
        days = max(1, min(30, int(data.get('days', 1))))
        budget = max(0, float(data.get('budget') or 0))
    except (TypeError, ValueError):
        return jsonify({'error': '天數或預算格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請先輸入目的地。'}), 400
    try:
        start_date = datetime.strptime(start_date_text, '%Y-%m-%d') if start_date_text else datetime.now()
    except ValueError:
        return jsonify({'error': '出發日期格式不正確。'}), 400
    return jsonify(build_trip_insights(city, days, start_date, budget, conditions))


def build_trip_insights(
    city: str,
    days: int,
    start_date: datetime,
    budget: float,
    conditions: str,
) -> dict[str, Any]:
    hotel = round(budget * 0.35) if budget else days * 2200
    transport = round(budget * 0.12) if budget else days * 700
    food = round(budget * 0.25) if budget else days * 1000
    tickets = round(budget * 0.13) if budget else days * 500
    reserve = round(budget * 0.15) if budget else days * 600
    total = hotel + transport + food + tickets + reserve
    transport_summary = '每段景點間的大眾運輸路線可開啟地圖查詢。'
    transport_tip = '交通時間與班次以出發前的即時查詢為準。'
    if conditions:
        transport_tip = f'已依你的條件「{conditions[:120]}」保留交通安排彈性。'
    weather, weather_status = get_weather_forecast(city, days, start_date)
    flight_url = (
        "https://www.google.com/travel/flights?"
        + urlencode({"q": f"Flights to {city}"})
    )
    return {
        'weather': weather,
        'weatherStatus': weather_status,
        'transport': {
            'summary': transport_summary,
            'estimatedCost': transport,
            'tip': transport_tip,
            'flightsUrl': flight_url,
        },
        'accommodation': {'area': f'{city} 車站周邊', 'nightlyBudget': round(hotel / days), 'reason': '靠近交通節點，適合首次造訪與每日往返。'},
        'budget': {
            'hotel': hotel, 'transport': transport, 'food': food, 'tickets': tickets, 'reserve': reserve, 'total': total,
            'withinBudget': not budget or total <= budget,
            'message': '預算配置可行，已保留 15% 預備金。' if not budget or total <= budget else '目前配置可能超出預算，建議降低住宿或購物支出。',
        },
    }


@app.post('/api/replan')
def replan():
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    reason = str(data.get('reason', '臨時變更')).strip() or '臨時變更'
    conditions = str(data.get('conditions', '')).strip()
    itinerary = data.get('itinerary')
    target_day = data.get('targetDay')
    try:
        target_day = int(target_day) if target_day is not None else None
    except (TypeError, ValueError):
        return jsonify({'error': '指定天數格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請先輸入目的地。'}), 400
    if len(conditions) > 1000:
        return jsonify({'error': '旅遊條件不可超過 1000 個字。'}), 400
    if not isinstance(itinerary, list) or target_day is None:
        return jsonify({'error': '請選擇要調整的行程日期。'}), 400

    updated_itinerary = [dict(day_plan) for day_plan in itinerary]
    target_plan = next((day_plan for day_plan in updated_itinerary if day_plan.get('day') == target_day), None)
    if target_plan is None:
        return jsonify({'error': '找不到指定的行程日期。'}), 400
    date_text = str(target_plan.get('date') or datetime.now().strftime('%Y-%m-%d'))
    try:
        result = generate_gemini_json(f"""
請重新安排 {city} 第 {target_day} 天的旅程，原因是「{reason}」。
同時必須遵守旅客原本設定的條件：「{conditions or '無額外條件'}」。
原本行程：{json.dumps(target_plan, ensure_ascii=False)}
{ROUTE_OPTIMIZATION_GUIDANCE}
{MEAL_AND_ATTRACTION_GUIDANCE}
{DAILY_ACCOMMODATION_GUIDANCE}
只替換這一天，activities 按照時間排序。
回傳 JSON：{{"day":{target_day},"date":"{date_text}","title":"標題","description":"調整原因與建議",
"accommodationArea":"住宿行政區或街區","accommodationReason":"依當日景點選擇的理由",
"activities":[{{"time":"08:00","name":"早餐：明確店名","place":"明確店名","category":"食物","meal":"早餐","description":"餐點簡介"}},
{{"time":"09:00-11:00","name":"明確地點或店名","place":"可搜尋地點","address":"完整地址","category":"景點","description":"說明"}}]}}
category 只能使用：食物、活動、購物、景點、酒店、交通、其他。
""")
        if not has_required_daily_meals_and_attractions(result):
            return jsonify({
                'error': '重新安排的行程未符合每日三餐及 3 至 4 個景點，請再試一次。'
            }), 502
        replanned = normalize_day(result, city, target_day, date_text, include_maps=False)
    except RuntimeError:
        app.logger.error("GEMINI_API_KEY is not configured for replanning.")
        fallback_activity = '室內景點' if reason == '下雨' else '彈性景點'
        replanned = normalize_day({
            'title': f'{city} 第 {target_day} 天（已依{reason}調整）',
            'description': f'因{reason}改安排室內或彈性景點。',
            'activities': [
                {'time': '08:00', 'name': f'早餐：{city} 在地早餐店', 'place': f'{city} 在地早餐店',
                 'category': '食物', 'meal': '早餐', 'description': '依所在地選擇合適店家。'},
                {'time': '09:30-11:00', 'name': f'{city} 室內文化景點', 'place': f'{city} 室內文化景點',
                 'category': '景點', 'description': '安排室內行程，降低天候影響。'},
                {'time': '12:00', 'name': f'午餐：{city} 在地餐廳', 'place': f'{city} 在地餐廳',
                 'category': '食物', 'meal': '午餐', 'description': '依所在地選擇合適店家。'},
                {'time': '13:30-15:00', 'name': fallback_activity, 'place': fallback_activity,
                 'category': '景點', 'description': '依突發狀況調整。'},
                {'time': '15:30-17:00', 'name': f'{city} 室內展館', 'place': f'{city} 室內展館',
                 'category': '景點', 'description': '安排室內行程，降低天候影響。'},
                {'time': '18:00', 'name': f'晚餐：{city} 在地餐廳', 'place': f'{city} 在地餐廳',
                 'category': '食物', 'meal': '晚餐', 'description': '依所在地選擇合適店家。'},
            ],
        }, city, target_day, date_text, include_maps=False)
    except Exception:
        app.logger.exception("Gemini itinerary replanning failed.")
        return jsonify({'error': 'AI 重新安排失敗，請稍後再試。'}), 502
    updated_itinerary = [
        replanned if day_plan.get('day') == target_day else normalize_day(
            day_plan, city, int(day_plan.get('day', index + 1)), str(day_plan.get('date') or ''),
            include_maps=False
        )
        for index, day_plan in enumerate(updated_itinerary)
    ]
    return jsonify({'itinerary': updated_itinerary, 'message': f'已只調整第 {target_day} 天，其餘行程保持不變。'})


@app.post('/api/regenerate_itinerary')
def regenerate_itinerary():
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    start_date_text = str(data.get('startDate', '')).strip()
    style = str(data.get('style', 'balanced')).strip() or 'balanced'
    conditions = str(data.get('conditions', '')).strip()
    edit_instruction = str(data.get('editInstruction', '')).strip()
    itinerary = data.get('itinerary')
    try:
        days = max(1, min(14, int(data.get('days', len(itinerary or [])))))
    except (TypeError, ValueError):
        return jsonify({'error': '旅行天數格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請先輸入目的地。'}), 400
    if not edit_instruction:
        return jsonify({'error': '請輸入想要修改的內容。'}), 400
    if not isinstance(itinerary, list) or not itinerary:
        return jsonify({'error': '請先生成至少一天的行程。'}), 400
    days = min(days, len(itinerary))
    try:
        start_date = datetime.strptime(start_date_text, '%Y-%m-%d') if start_date_text else datetime.now()
    except ValueError:
        return jsonify({'error': '出發日期格式不正確。'}), 400

    edited_itinerary = itinerary[:days]
    try:
        result, _ = generate_planner_json(f"""
請根據旅客的修改要求，重新生成完整的 {city} {days} 天旅遊行程。
出發日期：{start_date.strftime('%Y-%m-%d')}
旅行風格：{style}；預算：{data.get('budget') or '未指定'} TWD。
特殊條件：{conditions or '無額外條件'}。
旅客想要修改的內容：
{edit_instruction}
以下是目前的原始行程。請依照旅客的修改要求調整，並保留未要求修改的內容與偏好：
{json.dumps(edited_itinerary, ensure_ascii=False)}
{ROUTE_OPTIMIZATION_GUIDANCE}
{MEAL_AND_ATTRACTION_GUIDANCE}
{DAILY_ACCOMMODATION_GUIDANCE}
每個活動應盡量使用明確且真實的地點或店名。
回傳 JSON：{{"itinerary":[{{"day":1,"date":"YYYY-MM-DD","title":"短標題","description":"30字內摘要",
"accommodationArea":"住宿行政區或街區","accommodationReason":"依當日景點選擇的理由",
"activities":[{{"time":"08:00","name":"早餐：明確店名","place":"明確店名","category":"食物",
"meal":"早餐","description":"餐點簡介"}},{{"time":"09:00","name":"明確景點或店名",
"place":"明確地點名稱","address":"簡短地址","category":"景點","description":"20字內說明"}}]}}]}}
day 必須是 1 到 {days}，日期依序增加。category 只能是：食物、活動、購物、景點、酒店、交通、其他。
""")
        generated = result.get('itinerary') if isinstance(result, dict) else None
        if not isinstance(generated, list) or not generated:
            raise ValueError("AI returned an invalid regenerated itinerary shape.")
        if any(
            not has_required_daily_meals_and_attractions(day_plan)
            for day_plan in generated
        ):
            return jsonify({
                'error': '重新生成的行程未符合每日三餐及 3 至 4 個景點，請調整修改內容後重試。'
            }), 502
        days = len(generated)
        normalized_itinerary = [
            normalize_day(
                day_plan,
                city,
                day_number,
                (start_date + timedelta(days=day_number - 1)).strftime('%Y-%m-%d'),
                include_maps=False,
            )
            for day_number, day_plan in enumerate(generated, start=1)
        ]
        return jsonify({
            'itinerary': normalized_itinerary,
            'insights': build_trip_insights(
                city,
                days,
                start_date,
                float(data.get('budget') or 0),
                conditions,
            ),
            'days': days,
        })
    except OpenRouterAPIError as error:
        app.logger.error("OpenRouter itinerary fallback returned HTTP %s.", error.status_code)
        if error.status_code in {401, 403}:
            return jsonify({'error': 'OpenRouter API 金鑰無效或權限不足，請確認 OPENROUTER_API_KEY。'}), 502
        if error.status_code == 429:
            return jsonify({'error': 'OpenRouter 備援服務請求過多或額度不足，請稍後再試。'}), 502
        return jsonify({'error': 'Gemini 暫時無法使用，OpenRouter 備援服務也未能完成行程修改。'}), 502
    except RuntimeError as error:
        if "OPENROUTER_API_KEY is not configured" in str(error):
            app.logger.error("OPENROUTER_API_KEY is not configured for itinerary regeneration.")
            return jsonify({'error': 'Gemini 暫時無法使用，請確認 OPENROUTER_API_KEY 以啟用備援。'}), 503
        app.logger.exception("Itinerary regeneration failed.")
        return jsonify({'error': 'AI 重新生成失敗，請稍後再試。'}), 502
    except ValueError as error:
        app.logger.warning("Gemini returned invalid edited itinerary: %s", error)
        return jsonify({'error': 'AI 回傳的行程格式無法使用，請稍後重試。'}), 502
    except Exception:
        app.logger.exception("Gemini edited itinerary regeneration failed.")
        return jsonify({'error': 'AI 服務目前無法重新生成行程，請稍後再試。'}), 502


@app.post('/api/recognize_image')
def recognize_image():
    if 'image' not in request.files or not request.files['image'].filename:
        return jsonify({'error': '請選擇圖片。'}), 400
    image = request.files['image']
    image_bytes = image.read()
    if not image_bytes:
        return jsonify({'error': '圖片內容不可為空。'}), 400
    supported_image_types = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
    if image.mimetype not in supported_image_types:
        return jsonify({'error': '僅支援 JPEG、PNG、WEBP 或 GIF 圖片。'}), 400

    # Step 1: Groq Vision — identify the landmark / scene only.
    vision_prompt = """
請辨識這張旅遊圖片中的地標、景點或場景。
若無法確定，請誠實說明不確定，不要捏造精確地點。
不要推薦相似景點，只回傳辨識結果。
回傳 JSON：{"landmarkName":"辨識名稱或未知","description":"辨識說明（包含地點特徵、氛圍或相關資訊）"}
"""
    try:
        result = generate_groq_image_json(vision_prompt, image_bytes, image.mimetype)
        if not isinstance(result, dict):
            raise ValueError("Groq returned an invalid image result.")
        landmark_name = str(result.get('landmarkName') or '').strip() or '未知'
        description = str(result.get('description') or '').strip()
        result['landmarkName'] = landmark_name
        result['description'] = description
        if landmark_name and landmark_name != '未知':
            result['mapsUrl'] = google_maps_search_url(landmark_name)

        similar_spots: list[dict[str, Any]] = []
        recommendation_source = 'groq-fallback'
        query_text = f"{landmark_name}。{description}".strip('。')
        city_hint = infer_city_from_text(landmark_name, description)

        # Step 2: Try pgvector knowledge base first (if any rows exist).
        query_vector: list[float] | None = None
        try:
            if landmark_name != '未知' and query_text:
                query_vector = embed_texts_with_gemini([query_text])[0]
                db_hits = search_spot_embeddings(
                    query_vector,
                    city=city_hint,
                    top_k=2,
                    min_similarity=0.72,
                )
                if len(db_hits) >= 2:
                    similar_spots = db_hits
                    recommendation_source = 'pgvector'
                elif len(db_hits) == 1:
                    # Keep one DB hit; still generate more live candidates below.
                    similar_spots = db_hits
        except Exception as vector_error:
            app.logger.warning("Vector search step skipped: %s", vector_error)
            query_vector = None

        # Step 3: If knowledge base is sparse, generate candidates with Gemini
        # and rank them with gemini-embedding-2 + cosine similarity.
        if len(similar_spots) < 2:
            try:
                candidate_prompt = f"""
根據以下已辨識的旅遊地標，請列出 6 個真實、適合延伸探索的相似或相關景點。
辨識結果：{landmark_name}
說明：{description or '無額外說明'}
每個候選需包含真實店名或景點名稱、emoji、簡短推薦原因、可在 Google Maps 搜尋的完整地址。
不要重複辨識地標本身；優先選擇同一城市或同類型體驗的景點。
只回傳 JSON：{{"candidates":[{{"name":"景點名稱","emoji":"圖示","reason":"推薦原因","address":"完整地址","city":"城市"}}]}}
"""
                candidate_result = generate_gemini_json(candidate_prompt)
                candidates = (
                    candidate_result.get('candidates')
                    if isinstance(candidate_result, dict)
                    else None
                )
                if not isinstance(candidates, list) or not candidates:
                    raise ValueError("Gemini returned no candidate spots.")

                valid_candidates = []
                existing_names = {s['name'] for s in similar_spots}
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get('name') or '').strip()
                    if not name or name == landmark_name or name in existing_names:
                        continue
                    valid_candidates.append({
                        'name': name,
                        'emoji': str(item.get('emoji') or '📍').strip() or '📍',
                        'reason': str(item.get('reason') or item.get('desc') or '').strip(),
                        'address': str(item.get('address') or '').strip(),
                        'city': str(item.get('city') or city_hint or '').strip(),
                    })
                if not valid_candidates:
                    raise ValueError("No valid candidates after filtering.")

                ranked = rank_candidates_by_embedding(
                    query_text=query_text,
                    candidates=valid_candidates,
                    top_k=max(2 - len(similar_spots), 1),
                )
                for spot in ranked:
                    spot['mapsUrl'] = google_maps_search_url(
                        str(spot.get('address') or spot.get('name') or '').strip(),
                        str(spot.get('city') or city_hint or '').strip(),
                    )
                similar_spots = (similar_spots + ranked)[:2]
                recommendation_source = (
                    'pgvector+gemini-embedding-2'
                    if recommendation_source == 'pgvector'
                    else 'gemini-embedding-2'
                )

                # Grow the knowledge base for future requests (best-effort).
                try:
                    if query_vector is None:
                        query_vector = embed_texts_with_gemini([query_text])[0]
                    candidate_texts = [
                        " | ".join(
                            part for part in (
                                s.get('name'),
                                s.get('reason') or s.get('description'),
                                s.get('address'),
                            )
                            if part
                        )
                        for s in ranked
                    ]
                    candidate_vectors = embed_texts_with_gemini(candidate_texts)
                    upsert_spot_embeddings(
                        ranked,
                        candidate_vectors,
                        city=city_hint,
                        source='recognize',
                    )
                except Exception as persist_error:
                    app.logger.warning(
                        "Failed to persist new spot embeddings: %s",
                        persist_error,
                    )
            except Exception as emb_error:
                app.logger.warning(
                    "Embedding-based recommendation failed, falling back to Groq candidates: %s",
                    emb_error,
                )
                if not similar_spots:
                    fallback_prompt = f"""
已辨識地標：{landmark_name}
說明：{description or '無'}
請推薦 2 個相似或適合延伸探索的真實景點，每個都要提供可在 Google Maps 搜尋的地點名稱。
回傳 JSON：{{"similarSpots":[{{"name":"景點名稱","emoji":"圖示","reason":"推薦原因","address":"完整地址"}}]}}
"""
                    try:
                        fallback = generate_groq_image_json(
                            fallback_prompt, image_bytes, image.mimetype
                        )
                        raw_spots = (
                            fallback.get('similarSpots')
                            if isinstance(fallback, dict)
                            else []
                        )
                        if isinstance(raw_spots, list):
                            for spot in raw_spots[:2]:
                                if not isinstance(spot, dict):
                                    continue
                                name = str(spot.get('name') or '').strip()
                                if not name:
                                    continue
                                similar_spots.append({
                                    'name': name,
                                    'emoji': str(spot.get('emoji') or '📍').strip() or '📍',
                                    'reason': str(spot.get('reason') or '').strip(),
                                    'address': str(spot.get('address') or '').strip(),
                                    'mapsUrl': google_maps_search_url(
                                        str(spot.get('address') or name).strip()
                                    ),
                                })
                            recommendation_source = 'groq-fallback'
                    except Exception:
                        app.logger.exception(
                            "Groq fallback recommendation also failed."
                        )

        result['similarSpots'] = similar_spots
        result['recommendationSource'] = recommendation_source
        result['embeddingModel'] = (
            GEMINI_EMBEDDING_MODEL
            if 'embedding' in recommendation_source or recommendation_source == 'pgvector'
            else None
        )
        result['vectorStore'] = recommendation_source.startswith('pgvector')
        return jsonify({
            **result,
            'source': (
                'groq+pgvector'
                if recommendation_source.startswith('pgvector')
                else (
                    'groq+embedding'
                    if 'embedding' in recommendation_source
                    else 'groq'
                )
            ),
        })
    except GroqAPIError as error:
        app.logger.error("Groq image recognition returned HTTP %s.", error.status_code)
        if error.status_code in {401, 403}:
            return jsonify({'error': 'Groq API 金鑰無效或權限不足，請確認 GROQ_API_KEY。'}), 502
        if error.status_code == 429:
            return jsonify({'error': 'Groq API 請求過多或額度不足，請檢查 Groq 帳戶與速率限制。'}), 502
        return jsonify({'error': f'Groq API 呼叫失敗（HTTP {error.status_code}），請稍後再試。'}), 502
    except RuntimeError:
        if not get_env("GROQ_API_KEY", "REACT_APP_GROQ_API_KEY"):
            app.logger.error("GROQ_API_KEY is not configured.")
            return jsonify({'error': 'Groq API 尚未設定，請確認 GROQ_API_KEY 環境變數。'}), 503
        app.logger.exception("Groq image recognition request failed.")
        return jsonify({'error': '無法連線 Groq API，請檢查網路後再試。'}), 502
    except Exception:
        app.logger.exception("Groq image recognition failed.")
        return jsonify({'error': 'Groq 圖片辨識失敗，請稍後再試。'}), 502


# ==========================================
# 註冊功能 - 支援個人資料
# ==========================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 1. 接收所有表單資料
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = (request.form.get('name') or '').strip()
        gender = (request.form.get('gender') or '').strip()
        phone = (request.form.get('phone') or '').strip()

        if not email or not password or not full_name or not phone or gender not in {'male', 'female', 'other'}:
            flash('請完整填寫註冊資料，並選擇有效的性別。', 'warning')
            return redirect(url_for('register'))

        try:
            # 2. 呼叫 Supabase 的註冊 API
            # 將資料打包進 options["data"] 中
            res = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name,
                        "gender": gender,
                        "phone": phone,
                    }
                }
            })

            # 顯示成功訊息並重定向到登入頁面
            flash('註冊信已發送！請前往您的信箱點擊驗證連結。', 'info')
            return redirect(url_for('login'))

        except Exception as e:
            # 處理註冊失敗的情況
            flash(f'註冊失敗: {e}', 'danger')
            return redirect(url_for('register'))

    # 對於 GET 請求，渲染註冊頁面
    return render_template('register.html')

# ==========================================
# 登入功能
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # 獲取表單中的信箱和密碼
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        try:
            # 使用 Supabase 進行密碼登入
            res = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            # 將 Supabase 使用者與 session token 存入 Flask session，供 RLS API 使用
            store_auth_session(res)

            # 顯示歡迎訊息並重定向到首頁
            flash(f'歡迎回來, {session["name"]}!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            # 處理登入失敗的情況
            error_msg = str(e)
            if "Email not confirmed" in error_msg:
                flash('登入失敗：您的帳號尚未開通！請先前往信箱點擊驗證連結。', 'warning')
            else:
                flash('登入失敗：請檢查信箱或密碼是否正確。', 'danger')

            return redirect(url_for('login'))

    # 對於 GET 請求，渲染登入頁面
    return render_template('login.html')


@app.route('/auth/google')
def google_login():
    try:
        public_app_url = get_env('PUBLIC_APP_URL')
        redirect_url = (
            f"{public_app_url.rstrip('/')}{url_for('auth_callback')}"
            if public_app_url
            else url_for('auth_callback', _external=True)
        )
        res = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {"redirect_to": redirect_url},
        })
        if not res or not res.url:
            raise RuntimeError("Supabase did not return a Google OAuth URL.")
        return redirect(res.url)
    except Exception:
        app.logger.exception("Google OAuth initialization failed.")
        flash('Google 登入目前無法使用，請稍後再試。', 'danger')
        return redirect(url_for('login'))


@app.route('/auth/callback')
def auth_callback():
    error_description = request.args.get('error_description') or request.args.get('error')
    if error_description:
        app.logger.warning("Google OAuth callback returned an error: %s", error_description)
        flash('Google 登入已取消或失敗，請稍後再試。', 'warning')
        return redirect(url_for('login'))

    code = request.args.get('code')
    if not code:
        flash('Google 登入回應無效，請重新嘗試。', 'danger')
        return redirect(url_for('login'))

    try:
        res = supabase.auth.exchange_code_for_session({"auth_code": code})
        store_auth_session(res)
        flash(f'歡迎回來, {session["name"]}!', 'success')
        return redirect(url_for('home'))
    except Exception:
        app.logger.exception("Google OAuth callback failed.")
        flash('Google 登入失敗，請確認設定後再試。', 'danger')
        return redirect(url_for('login'))


@app.route('/logout')
def logout():
    try:
        # 嘗試從 Supabase 登出
        supabase.auth.sign_out()
    except Exception as e:
        # 記錄登出錯誤，但不影響用戶體驗
        print(f"Supabase 登出錯誤: {e}")

    # 清除所有登入資訊，避免空姓名或舊的 flash 訊息殘留。
    session.clear()
    # 先由瀏覽器清除 localStorage，再返回首頁；伺服器無法直接清除瀏覽器資料。
    return render_template('logout.html', home_url=url_for('home'))


# ==========================================
# 啟動區塊
# ==========================================
if __name__ == "__main__":
    # Render 會提供 PORT 環境變數；本機預設使用 OAuth 常見的 3000 埠。
    port = int(os.environ.get("PORT", 3000))
    # 生產環境中 debug 應設為 False，但在 Render 介面可透過環境變數控制
    app.run(host='0.0.0.0', port=port)