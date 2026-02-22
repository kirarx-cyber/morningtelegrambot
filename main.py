import asyncio
import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
NEWS_URL = "https://newsapi.org/v2/everything"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6176
SUBSCRIBERS_FILE = Path("subscribers.json")
REQUEST_TIMEOUT_SECONDS = 15
MAX_DESCRIPTION_LEN = 260


def load_subscribers() -> set[int]:
    if not SUBSCRIBERS_FILE.exists():
        return set()

    try:
        data = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return set()
        return {int(item) for item in data}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Cannot load subscribers: %s", exc)
        return set()


def save_subscribers(subscribers: set[int]) -> None:
    SUBSCRIBERS_FILE.write_text(
        json.dumps(sorted(subscribers), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _utc_day_range_for_yesterday() -> tuple[str, str]:
    now_utc = datetime.now(timezone.utc)
    today_utc = now_utc.date()
    yesterday_utc = today_utc - timedelta(days=1)
    return yesterday_utc.isoformat(), today_utc.isoformat()


async def fetch_weather(client: httpx.AsyncClient) -> dict[str, Any]:
    params = {
        "lat": MOSCOW_LAT,
        "lon": MOSCOW_LON,
        "units": "metric",
        "lang": "ru",
        "appid": OPENWEATHER_API_KEY,
    }

    response = await client.get(WEATHER_URL, params=params)
    if response.status_code == 429:
        raise RuntimeError("OpenWeather API rate limit exceeded")
    response.raise_for_status()

    payload = response.json()
    return {
        "temp": round(float(payload["main"]["temp"])),
        "feels_like": round(float(payload["main"]["feels_like"])),
        "humidity": int(payload["main"]["humidity"]),
        "description": str(payload["weather"][0]["description"]).capitalize(),
    }


async def fetch_positive_news(client: httpx.AsyncClient) -> dict[str, str] | None:
    date_from, date_to = _utc_day_range_for_yesterday()
    params = {
        "q": "good OR positive OR inspiring OR breakthrough OR success",
        "language": "en",
        "sortBy": "popularity",
        "pageSize": 20,
        "from": date_from,
        "to": date_to,
        "apiKey": NEWS_API_KEY,
    }

    response = await client.get(NEWS_URL, params=params)
    if response.status_code == 429:
        raise RuntimeError("News API rate limit exceeded")
    response.raise_for_status()

    payload = response.json()
    if payload.get("status") == "error":
        raise RuntimeError(str(payload.get("message", "Unknown News API error")))

    articles = payload.get("articles", [])
    for article in articles:
        title = (article.get("title") or "").strip()
        description = (article.get("description") or "").strip()
        url = (article.get("url") or "").strip()

        if title and description and url:
            if len(description) > MAX_DESCRIPTION_LEN:
                description = description[: MAX_DESCRIPTION_LEN - 3].rstrip() + "..."
            return {"title": title, "description": description, "url": url}

    return None


def build_message(weather: dict[str, Any] | None, news: dict[str, str] | None) -> str:
    lines = ["Доброе утро ☀️", "", "📍 Москва"]

    if weather is None:
        lines.append("🌡 Погода: временно недоступна (API не ответил или лимит исчерпан).")
    else:
        lines.extend(
            [
                f"🌡 Температура: {weather['temp']}°C",
                f"🤍 Ощущается как: {weather['feels_like']}°C",
                f"💧 Влажность: {weather['humidity']}%",
                f"🌤 Описание: {weather['description']}",
            ]
        )

    lines.extend(["", "📰 Хорошая новость дня:"])
    if news is None:
        lines.append(
            "Сегодня не удалось получить позитивную новость "
            "(не найдена или API временно недоступен)."
        )
    else:
        lines.extend([news["title"], news["description"], news["url"]])

    return "\n".join(lines)


async def build_daily_message() -> str:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        weather_task = fetch_weather(client)
        news_task = fetch_positive_news(client)
        weather_result, news_result = await asyncio.gather(
            weather_task,
            news_task,
            return_exceptions=True,
        )

    weather_data: dict[str, Any] | None = None
    news_data: dict[str, str] | None = None

    if isinstance(weather_result, Exception):
        logger.warning("Weather request failed: %s", weather_result)
    else:
        weather_data = weather_result

    if isinstance(news_result, Exception):
        logger.warning("News request failed: %s", news_result)
    else:
        news_data = news_result

    return build_message(weather_data, news_data)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    subscribers = context.bot_data.setdefault("subscribers", set())
    if not isinstance(subscribers, set):
        subscribers = set(subscribers)

    subscribers.add(chat_id)
    context.bot_data["subscribers"] = subscribers
    save_subscribers(subscribers)

    if update.effective_message:
        await update.effective_message.reply_text(
            "Подписка активирована.\n"
            "Каждый день в 09:00 (Москва) вы будете получать погоду и хорошую новость."
        )


async def send_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers = context.bot_data.get("subscribers", set())
    if not subscribers:
        logger.info("No subscribers yet, skip digest")
        return

    message = await build_daily_message()
    stale_chat_ids: set[int] = set()

    for chat_id in set(subscribers):
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot send message to %s: %s", chat_id, exc)
            err = str(exc).lower()
            if "forbidden" in err or "chat not found" in err:
                stale_chat_ids.add(chat_id)

    if stale_chat_ids:
        updated = set(subscribers) - stale_chat_ids
        context.bot_data["subscribers"] = updated
        save_subscribers(updated)


def validate_env() -> None:
    missing: list[str] = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not OPENWEATHER_API_KEY:
        missing.append("OPENWEATHER_API_KEY")
    if not NEWS_API_KEY:
        missing.append("NEWS_API_KEY")

    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


def main() -> None:
    validate_env()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.bot_data["subscribers"] = load_subscribers()

    app.add_handler(CommandHandler("start", start_handler))

    app.job_queue.run_daily(
        callback=send_daily_digest,
        time=time(hour=9, minute=0, tzinfo=MOSCOW_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="daily_digest",
    )

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
