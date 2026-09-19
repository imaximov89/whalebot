import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

import requests
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

load_dotenv()

logging.basicConfig(level=logging.ERROR, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bingx_signal_bot")
logger.setLevel(logging.ERROR)


def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        logger.warning("Invalid %s value %r, using default %s", name, value, default)
        return default
    return parsed


def get_stop_loss_percent() -> float:
    value = get_env_float("STOP_LOSS", 0.02)
    return value / 100 if value > 1 else value


def numeric(value: str) -> Decimal:
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    return Decimal(cleaned)


def build_symbol(raw_symbol: str, quote_asset: str = "USDT") -> str:
    symbol = raw_symbol.strip().upper().replace("/", "").replace("-", "")
    if symbol.endswith(quote_asset.upper()):
        return symbol
    return f"{symbol}{quote_asset.upper()}"


def parse_signal_message(message_text: str) -> Optional[Dict[str, object]]:
    text = message_text.strip()
    if not text:
        return None

    pair_match = re.search(r"(?i)\b(?P<base>[A-Z][A-Z0-9]{1,10})\s*(?:/|-)\s*(?P<quote>[A-Z][A-Z0-9]{1,10})\b", text)
    if pair_match:
        symbol = build_symbol(f"{pair_match.group('base')}/{pair_match.group('quote')}")
    else:
        symbol_match = re.search(r"(?im)(?:^|[^A-Z0-9])(?P<symbol>[A-Z][A-Z0-9]{1,10})\b", text)
        if not symbol_match:
            symbol_match = re.search(r"(?i)\b(?P<symbol>[A-Z][A-Z0-9]{1,10})\b", text)
        if not symbol_match:
            logger.warning("Could not find symbol in message: %s", message_text)
            return None
        symbol = build_symbol(symbol_match.group("symbol").upper())
    entry = None
    take1 = None
    take2 = None
    stop = None

    patterns = {
        "entry": r"(?iu)(?:Вход|Entry(?:\s*Price)?|Цена\s*входа)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)",
        "take1": r"(?iu)(?:Тейк\s*(?:1|profit)?|Take\s*(?:1|profit)?|Take1|TP\s*(?:1|profit)?|Target(?:\s*Profit)?(?:\s*1)?|T1)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)",
        "take2": r"(?iu)(?:Тейк\s*2|Take\s*2|Take2|Take\s*Profit\s*2|TP\s*2|Target\s*2|T2)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)",
        "stop": r"(?iu)(?:Стоп(?:\s*Лосс)?|Stop(?:\s*Loss)?|SL)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)",
    }

    take1_match = re.search(r"(?iu)(?:Тейк\s*(?:1|profit)|Take(?:\s*Profit)?(?:\s*1)?|Take1|TP(?:\s*1|\s*Profit)?|Target(?:\s*Profit)?(?:\s*1)?|T1)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)", text)
    if take1_match:
        take1 = numeric(take1_match.group(1))

    take2_match = re.search(r"(?iu)(?:Тейк\s*2|Take(?:\s*Profit)?\s*2|Take2|TP\s*2|Target\s*2|T2)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)", text)
    if take2_match:
        take2 = numeric(take2_match.group(1))

    if take1 is None:
        take1_fallback = re.search(r"(?iu)(?:Тейк\s*(?:1|profit)|Take(?:\s*Profit)?(?:\s*1)?|Take1|TP(?:\s*1|\s*Profit)?|Target(?:\s*Profit)?(?:\s*1)?|T1)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*[·•\-–—]\s*(?:Тейк\s*2|Take(?:\s*Profit)?\s*2|Take2|TP\s*2|Target\s*2|T2)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)", text)
        if take1_fallback:
            take1 = numeric(take1_fallback.group(1))
            take2 = numeric(take1_fallback.group(2))

    for name, pattern in patterns.items():
        if name in {"take1", "take2"}:
            continue
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            try:
                if name == "entry":
                    entry = numeric(value)
                elif name == "stop":
                    stop = numeric(value)
            except InvalidOperation:
                logger.warning("Could not parse numeric from %s in message: %s", name, message_text)

    if entry is None:
        entry_match = re.search(r"(?iu)(?:Вход|Entry(?:\s*Price)?|Цена\s*входа)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)", text)
        if entry_match:
            entry = numeric(entry_match.group(1))

    if stop is None:
        stop_match = re.search(r"(?iu)(?:Стоп(?:\s*Лосс)?|Stop(?:\s*Loss)?|SL)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)", text)
        if stop_match:
            stop = numeric(stop_match.group(1))

    if entry is None or take1 is None:
        logger.warning("Incomplete signal: %s", message_text)
        return None

    if take2 is None:
        take2 = take1

    side = "BUY" if (take1 > entry and (stop is None or stop < entry)) else "SELL"
    if stop is not None and stop > entry and take1 < entry:
        side = "SELL"

    return {
        "symbol": symbol,
        "entry": entry,
        "take1": take1,
        "take2": take2,
        "stop": stop,
        "side": side,
    }


def calc_stop_loss_price(entry: Decimal, side: str, stop_loss_percent: float) -> Decimal:
    if side == "BUY":
        return entry * (Decimal("1") - Decimal(str(stop_loss_percent)))
    return entry * (Decimal("1") + Decimal(str(stop_loss_percent)))


def create_signed_payload(api_key: str, secret_key: str, payload: Dict[str, object]) -> Dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    payload_with_ts = {**payload, "timestamp": timestamp, "recvWindow": payload.get("recvWindow", 60000)}
    payload_json = json.dumps(payload_with_ts, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(secret_key.encode("utf-8"), payload_json.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-BX-APIKEY": api_key,
        "X-BX-SIGNATURE": signature,
        "Content-Type": "application/json",
        "payload": payload_json,
    }


def bingx_request(method: str, path: str, api_key: str, secret_key: str, payload: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    base_url = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com")
    url = f"{base_url}{path}"
    request_payload = payload or {}
    headers = create_signed_payload(api_key, secret_key, request_payload)
    response = requests.request(method, url, headers={
        "X-BX-APIKEY": headers["X-BX-APIKEY"],
        "X-BX-SIGNATURE": headers["X-BX-SIGNATURE"],
        "Content-Type": headers["Content-Type"],
    }, data=headers["payload"], timeout=30)
    try:
        parsed = response.json()
    except ValueError:
        raise RuntimeError(f"BingX API returned non-JSON response: {response.text}")
    if response.status_code >= 400:
        raise RuntimeError(f"BingX request failed ({response.status_code}): {parsed}")
    return parsed


def send_trade(signal: Dict[str, object]) -> None:
    api_key = os.getenv("BINGX_API_KEY")
    secret_key = os.getenv("BINGX_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("BINGX_API_KEY and BINGX_SECRET_KEY must be set in the environment.")

    stop_loss_percent = get_stop_loss_percent()
    position_size = Decimal(str(get_env_float("BINGX_POSITION_SIZE", 1.0)))
    symbol = build_symbol(str(signal["symbol"]), os.getenv("BINGX_QUOTE_ASSET", "USDT"))
    entry = Decimal(str(signal["entry"]))
    take1 = Decimal(str(signal["take1"]))
    take2 = Decimal(str(signal["take2"]))
    side = str(signal["side"]).upper()
    entry_order_side = side
    close_order_side = "SELL" if side == "BUY" else "BUY"
    position_side = "LONG" if side == "BUY" else "SHORT"
    stop_loss_price = calc_stop_loss_price(entry, side, stop_loss_percent)


    open_payload = {
        "symbol": symbol,
        "side": entry_order_side,
        "positionSide": position_side,
        "type": "LIMIT",
        "quantity": format(position_size, "f"),
        "price": format(entry, "f"),
        "timeInForce": "GTC",
        "reduceOnly": False,
    }
    open_order = bingx_request("POST", "/openApi/swap/v2/order", api_key, secret_key, open_payload)

    half_quantity = position_size / Decimal("2")
    close_first = {
        "symbol": symbol,
        "side": close_order_side,
        "positionSide": position_side,
        "type": "LIMIT",
        "quantity": format(half_quantity, "f"),
        "price": format(take1, "f"),
        "timeInForce": "GTC",
        "reduceOnly": True,
    }
    close_first_response = bingx_request("POST", "/openApi/swap/v2/order", api_key, secret_key, close_first)

    close_second = {
        "symbol": symbol,
        "side": close_order_side,
        "positionSide": position_side,
        "type": "LIMIT",
        "quantity": format(half_quantity, "f"),
        "price": format(take2, "f"),
        "timeInForce": "GTC",
        "reduceOnly": True,
    }
    close_second_response = bingx_request("POST", "/openApi/swap/v2/order", api_key, secret_key, close_second)

    stop_loss_order = {
        "symbol": symbol,
        "side": close_order_side,
        "positionSide": position_side,
        "type": "STOP",
        "quantity": format(position_size, "f"),
        "stopPrice": format(stop_loss_price, "f"),
        "timeInForce": "GTC",
        "reduceOnly": True,
    }
    stop_loss_response = bingx_request("POST", "/openApi/swap/v2/order", api_key, secret_key, stop_loss_order)


async def handle_new_message(event):
    chat_id = int(os.getenv("TELEGRAM_TARGET_CHAT_ID", "-1003784200144"))
    if event.chat_id != chat_id:
        return

    text = (event.raw_text or event.text or "").strip()
    if not text:
        return

    signal = parse_signal_message(text)
    if signal is None:
        logger.warning("Unable to parse signal from chat message: %s", text)
        return

    try:
        send_trade(signal)
    except Exception as exc:
        logger.exception("Error opening trade: %s", exc)


async def main() -> None:
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in the environment.")

    session_string = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
    if session_string:
        client = TelegramClient(StringSession(session_string), int(api_id), api_hash)
    else:
        legacy_session_name = os.getenv("TELEGRAM_SESSION_NAME", "whalebot").strip()
        safe_session_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", legacy_session_name) or "whalebot"
        session_dir = os.path.join(os.getcwd(), ".sessions")
        os.makedirs(session_dir, exist_ok=True)
        session_file = os.path.join(session_dir, f"{safe_session_name}.session")
        client = TelegramClient(session_file, int(api_id), api_hash)

    @client.on(events.NewMessage())
    async def handler(event):
        await handle_new_message(event)

    await client.start()
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
