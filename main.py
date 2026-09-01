import asyncio
import aiohttp
import os
import json
import logging
from typing import Optional, Tuple
from aiogram import Bot, Dispatcher, types

# ---------- CONFIG ----------
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("Set BOT_TOKEN environment variable")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

LOG_FILE = "dex_bot.log"
logging.basicConfig(level=logging.INFO, filename=LOG_FILE, filemode="a",
                    format="%(asctime)s %(levelname)s %(message)s")

# Monitoring interval (seconds)
POLL_INTERVAL = 10

# Default alert thresholds (fractions)
DEFAULT_ALERTS = [0.60, 0.65, 0.70, 0.80]

# DexScreener endpoints (official)
DEX_PAIRS_BASE = "https://api.dexscreener.com/latest/dex/pairs/"
DEX_TOKENS_BASE = "https://api.dexscreener.com/latest/dex/tokens/"
DEX_SEARCH_BASE = "https://api.dexscreener.com/latest/dex/search?q="

# In-memory storage
# key -> token info
# key is a string: "<chain>/<pair>" for pair links, or "<contract>" for contract-based adds
TOKENS = {}

# ---------- UTILITIES ----------

def safe_float(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0

def key_encode(chain: str, pair: str) -> str:
    return f"{chain}/{pair}"

def key_decode(key: str) -> Tuple[str, str]:
    # If key contains '/', treat as chain/pair
    if "/" in key:
        parts = key.split("/", 1)
        return parts[0], parts[1]
    # fallback: assume robinhood chain if only pair provided
    return "robinhood", key

async def http_get_json(url: str) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    logging.error("Invalid JSON from %s: %s", url, text[:1000])
                    return None
                return data
    except Exception as e:
        logging.exception("HTTP GET failed for %s: %s", url, e)
        return None

# ---------- DEXScreener fetchers (official endpoints) ----------

async def fetch_pairs_api(chain: str, pair_address: str) -> Optional[dict]:
    url = f"{DEX_PAIRS_BASE}{chain}/{pair_address}"
    data = await http_get_json(url)
    logging.info("PAIRS API %s -> %s", url, "OK" if data else "NO DATA")
    if data and "pairs" in data and data["pairs"]:
        return data["pairs"][0]
    return None

async def fetch_tokens_api(contract: str) -> Optional[list]:
    url = f"{DEX_TOKENS_BASE}{contract}"
    data = await http_get_json(url)
    logging.info("TOKENS API %s -> %s", url, "OK" if data else "NO DATA")
    if data and "pairs" in data and data["pairs"]:
        return data["pairs"]
    return None

async def fetch_search_api(query: str) -> Optional[list]:
    url = f"{DEX_SEARCH_BASE}{query}"
    data = await http_get_json(url)
    logging.info("SEARCH API %s -> %s", url, "OK" if data else "NO DATA")
    if data and "pairs" in data and data["pairs"]:
        return data["pairs"]
    return None

# ---------- Selection and metric computation ----------

def select_best_pair(pairs: list) -> dict:
    # Sort by liquidity, volume, holders (desc)
    pairs_sorted = sorted(
        pairs,
        key=lambda p: (
            safe_float(p.get("liquidity", 0)),
            safe_float(p.get("volume", 0)),
            safe_float(p.get("holders", 0))
        ),
        reverse=True
    )
    return pairs_sorted[0]

def compute_market_metric(pair: dict) -> Tuple[float, Optional[str]]:
    """
    Return (value, source)
    Try in order: marketCap, fdv, priceUsd * circulatingSupply
    """
    mc = safe_float(pair.get("marketCap"))
    if mc > 0:
        return mc, "marketCap"

    fdv = safe_float(pair.get("fdv"))
    if fdv > 0:
        return fdv, "fdv"

    price = safe_float(pair.get("priceUsd"))
    base = pair.get("baseToken") or {}
    circ = safe_float(base.get("circulatingSupply"))
    if price > 0 and circ > 0:
        return price * circ, "price*circulatingSupply"

    return 0.0, None

# ---------- Resolver (combined mode) ----------

async def resolve_pair_from_input(raw: str) -> Optional[dict]:
    """
    Try to resolve a pair using combined strategy:
    1) If raw contains dexscreener.com -> extract chain and pair -> PAIRS API
    2) If raw looks like a contract -> TOKENS API -> select best pair
    3) SEARCH API fallback
    Returns the resolved pair dict or None.
    """
    # 1) Link case
    if "dexscreener.com" in raw:
        try:
            segments = raw.rstrip("/").split("/")
            chain = segments[-2]
            pair_address = segments[-1]
        except Exception:
            return None

        # Try PAIRS API first (official)
        pair = await fetch_pairs_api(chain, pair_address)
        if pair:
            pair["_resolved_key"] = key_encode(chain, pair_address)
            return pair

        # If PAIRS empty, try tokens API using pair_address as contract (some links use contract)
        pairs = await fetch_tokens_api(pair_address)
        if pairs:
            best = select_best_pair(pairs)
            best["_resolved_key"] = key_encode(chain, pair_address)
            return best

        # Try search API with pair_address
        pairs = await fetch_search_api(pair_address)
        if pairs:
            best = select_best_pair(pairs)
            best["_resolved_key"] = key_encode(chain, pair_address)
            return best

        return None

    # 2) Raw is contract or pair id
    contract = raw
    # Try TOKENS API
    pairs = await fetch_tokens_api(contract)
    if pairs:
        best = select_best_pair(pairs)
        # try to determine chain/pair from best if available
        chain = best.get("chainId") or "unknown"
        pair_id = best.get("pairAddress") or contract
        best["_resolved_key"] = key_encode(str(chain), str(pair_id))
        return best

    # 3) SEARCH API
    pairs = await fetch_search_api(contract)
    if pairs:
        best = select_best_pair(pairs)
        chain = best.get("chainId") or "unknown"
        pair_id = best.get("pairAddress") or contract
        best["_resolved_key"] = key_encode(str(chain), str(pair_id))
        return best

    return None

# ---------- Monitoring loop ----------

async def monitor(chat_id: int):
    while True:
        for key, token in list(TOKENS.items()):
            # key may be stored as "<chain>/<pair>" or original contract string
            chain, pair_id = key_decode(key)
            # Try to fetch pair by chain/pair if possible
            pair = None
            # If token stored with explicit pair info, prefer PAIRS API
            if "/" in key:
                pair = await fetch_pairs_api(chain, pair_id)
            # If not found, try resolving from stored contract or key
            if not pair:
                pair = await resolve_pair_from_input(token.get("_input", key))
            if not pair:
                logging.info("No pair data for %s, skipping", key)
                continue

            # compute metric (MC or fallback)
            metric_value, metric_source = compute_market_metric(pair)
            # If no metric, fallback to price alerts mode
            if metric_value <= 0:
                # switch to price mode if not already
                if token.get("mode") != "price":
                    token["mode"] = "price"
                    token["price_ath"] = None
                    token["mc_source"] = None
                    logging.info("Switching %s to price alerts (no MC available)", key)
                # handle price-based alerts
                price = safe_float(pair.get("priceUsd"))
                if price <= 0:
                    logging.info("No price for %s, skipping", key)
                    continue

                # init price ATH
                if token.get("price_ath") is None:
                    token["price_ath"] = price
                    continue

                # update price ATH
                if price > token["price_ath"]:
                    token["price_ath"] = price
                    token["triggered"].clear()

                # compute drop from price ATH
                drop = (token["price_ath"] - price) / token["price_ath"] if token["price_ath"] > 0 else 0

                emoji_map = {0.60: "🟥", 0.65: "🟧", 0.70: "🟨", 0.80: "🟪"}
                for threshold in token["alerts"]:
                    if drop < threshold and threshold in token["triggered"]:
                        token["triggered"].remove(threshold)
                    if drop >= threshold and threshold not in token["triggered"]:
                        token["triggered"].add(threshold)
                        emoji = emoji_map.get(threshold, "⚠️")
                        kb = types.InlineKeyboardMarkup()
                        kb.add(
                            types.InlineKeyboardButton("Re-arm", callback_data=f"rearm|{key}"),
                            types.InlineKeyboardButton("Delete", callback_data=f"delete|{key}")
                        )
                        await bot.send_message(
                            chat_id,
                            f"{emoji} {token['name']} price dropped {int(threshold*100)}% from ATH\n"
                            f"Price ATH: ${token['price_ath']:,}\n"
                            f"Now: ${price:,}\n"
                            f"Source: priceUsd\n"
                            f"Link: https://dexscreener.com/{chain}/{pair_id}",
                            reply_markup=kb
                        )
                continue  # next token

            # metric_value > 0 -> use MC/fdv/price*circulatingSupply
            if token.get("mode") != "mc":
                token["mode"] = "mc"
                token["ath_mc"] = None
                token["mc_source"] = metric_source

            # init ATH
            if token.get("ath_mc") is None:
                token["ath_mc"] = metric_value
                token["mc_source"] = metric_source
                logging.info("Initialized ATH MC for %s: %s (source=%s)", key, metric_value, metric_source)
                continue

            # update ATH
            if metric_value > token["ath_mc"]:
                token["ath_mc"] = metric_value
                token["triggered"].clear()

            drop = (token["ath_mc"] - metric_value) / token["ath_mc"] if token["ath_mc"] > 0 else 0

            emoji_map = {0.60: "🟥", 0.65: "🟧", 0.70: "🟨", 0.80: "🟪"}
            for threshold in token["alerts"]:
                if drop < threshold and threshold in token["triggered"]:
                    token["triggered"].remove(threshold)
                if drop >= threshold and threshold not in token["triggered"]:
                    token["triggered"].add(threshold)
                    emoji = emoji_map.get(threshold, "⚠️")
                    kb = types.InlineKeyboardMarkup()
                    kb.add(
                        types.InlineKeyboardButton("Re-arm", callback_data=f"rearm|{key}"),
                        types.InlineKeyboardButton("Delete", callback_data=f"delete|{key}")
                    )
                    await bot.send_message(
                        chat_id,
                        f"{emoji} {token['name']} dropped {int(threshold*100)}% from ATH ({token.get('mc_source')})\n"
                        f"ATH: ${token['ath_mc']:,}\n"
                        f"Now: ${metric_value:,}\n"
                        f"Link: https://dexscreener.com/{chain}/{pair_id}",
                        reply_markup=kb
                    )

        await asyncio.sleep(POLL_INTERVAL)

# ---------- Callbacks ----------

@dp.callback_query_handler(lambda c: c.data.startswith("rearm|"))
async def rearm_alert(call: types.CallbackQuery):
    _, key = call.data.split("|", 1)
    if key in TOKENS:
        TOKENS[key]["triggered"].clear()
        await call.message.answer(f"Alerts re-armed for {TOKENS[key]['name']}.")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("delete|"))
async def delete_token(call: types.CallbackQuery):
    _, key = call.data.split("|", 1)
    if key in TOKENS:
        name = TOKENS[key]["name"]
        del TOKENS[key]
        await call.message.answer(f"Token {name} deleted.")
    await call.answer()

# ---------- Commands ----------

@dp.message_handler(commands=["add"])
async def add_token(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage:\n/add <dexscreener_link_or_contract_or_pair>")
        return

    raw = parts[1].strip()
    pair = await resolve_pair_from_input(raw)
    if not pair:
        await msg.answer("Token/pair not found via PAIRS, TOKENS, or SEARCH API.")
        return

    # Determine key and metadata
    resolved_key = pair.get("_resolved_key")
    if not resolved_key:
        # fallback: try to build from chainId/pairAddress fields
        chain = pair.get("chainId") or "robinhood"
        pair_addr = pair.get("pairAddress") or pair.get("pair") or raw
        resolved_key = key_encode(str(chain), str(pair_addr))

    base = pair.get("baseToken") or {}
    name = base.get("name") or pair.get("name") or "Unknown"
    contract = base.get("address") or ""

    # store token
    TOKENS[resolved_key] = {
        "name": name,
        "contract": contract,
        "mode": None,            # "mc" or "price"
        "ath_mc": None,
        "price_ath": None,
        "mc_source": None,
        "alerts": DEFAULT_ALERTS.copy(),
        "triggered": set(),
        "_input": raw
    }

    await msg.answer(
        f"Added token:\n"
        f"Name: {name}\n"
        f"Contract: {contract}\n"
        f"Key: {resolved_key}\n"
        f"Tracking MarketCap (fallback to price alerts if MC unavailable)."
    )

@dp.message_handler(commands=["remove"])
async def remove_token_cmd(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage:\n/remove <chain/pair_or_key>")
        return
    key = parts[1].strip()
    if key in TOKENS:
        name = TOKENS[key]["name"]
        del TOKENS[key]
        await msg.answer(f"Token {name} removed.")
    else:
        await msg.answer("Token not found.")

@dp.message_handler(commands=["list"])
async def list_tokens(msg: types.Message):
    if not TOKENS:
        await msg.answer("No tokens being tracked.")
        return
    lines = ["Tracked tokens:"]
    for k, t in TOKENS.items():
        mode = t.get("mode") or "pending"
        ath = t.get("ath_mc") if mode == "mc" else t.get("price_ath")
        lines.append(f"• {t['name']} (Key: {k}, Mode: {mode}, ATH: {ath})")
    await msg.answer("\n".join(lines))

@dp.message_handler(commands=["reset"])
async def reset_alerts(msg: types.Message):
    for t in TOKENS.values():
        t["triggered"].clear()
    await msg.answer("All alerts reset.")

@dp.message_handler(commands=["debug"])
async def debug_pair(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage:\n/debug <dexscreener_link_or_pair_or_contract>")
        return
    raw = parts[1].strip()
    pair = await resolve_pair_from_input(raw)
    if not pair:
        await msg.answer("Pair not found or API returned empty.")
        return
    text = json.dumps(pair, indent=2)[:4000]
    await msg.answer(f"Raw pair JSON (truncated):\n<pre>{text}</pre>", parse_mode="HTML")

@dp.message_handler(commands=["commands"])
async def commands(msg: types.Message):
    await msg.answer(
        "/start – start monitoring\n"
        "/add <link_or_contract_or_pair> – add token\n"
        "/remove <key> – remove token\n"
        "/list – list tracked tokens\n"
        "/reset – reset alerts\n"
        "/debug <link_or_pair_or_contract> – show raw pair JSON\n"
        "/commands – show commands\n"
    )

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.answer("Bot started. Add tokens using /add <dexscreener_link_or_contract_or_pair>.")
    # start monitor task once per chat
    asyncio.create_task(monitor(msg.chat.id))

# ---------- Entrypoint ----------

async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())










