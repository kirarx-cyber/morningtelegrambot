# Telegram Daily Morning Bot

Asynchronous Telegram bot on `python-telegram-bot v20+`.
Every day at **08:00 Europe/Moscow** it sends:
- Current weather in Moscow (temperature, feels like, humidity, description)
- One positive news item for the previous day

## Project structure
- `main.py`
- `requirements.txt`
- `.env.example`
- `.env` (local only, do not commit)

## Requirements
- Python 3.10+
- Telegram bot token
- OpenWeather API key
- News API key

## Setup
1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create `.env` from `.env.example` and fill values:

```env
TELEGRAM_TOKEN=...
OPENWEATHER_API_KEY=...
NEWS_API_KEY=...
```

3. Run bot:

```bash
python main.py
```

4. In Telegram send `/start` to the bot.

## Behavior
- `/start` stores your `chat_id` in `subscribers.json` and confirms subscription.
- Daily digest is sent at `08:00` Moscow time using PTB `JobQueue`.
- If APIs fail / return no news / limit is exceeded, fallback text is sent instead of crashing.

## Deploy notes (Render / Railway / VPS)
- Use a long-running worker service, command: `python main.py`
- Set env vars in service settings:
  - `TELEGRAM_TOKEN`
  - `OPENWEATHER_API_KEY`
  - `NEWS_API_KEY`
- Persist `subscribers.json` if your platform has ephemeral filesystem.

## Security
Do not commit `.env` or publish real API keys/tokens.
If keys were shared publicly, rotate them in provider dashboards.
