# Main(forward demo)
import os
import logging
import json
import asyncio
import traceback
"""Forward (non-reversed) live trading FastAPI service."""

import os
import logging
import json
import asyncio
import traceback
import time
import math
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import hashlib


last_alert = {}
completed_trades = {}
active_orders = []
DUPLICATE_THRESHOLD_SECONDS = 30
COMPLETED_TRADE_COOLDOWN = 30


TICK_SIZES = {
    "NQ": 0.25,
    "MNQ": 0.25
}


def get_tick_size(symbol: str) -> float:
    if not symbol:
        return 0.01
    for prefix, tick in TICK_SIZES.items():
        if symbol.startswith(prefix):
            return tick
    return 0.01


def round_price_to_tick(value: float, symbol: str, mode: str = "nearest") -> float:
    if value is None:
        return value
    tick = get_tick_size(symbol)
    if tick <= 0:
        return round(value, 2)
    steps = value / tick
    if mode == "down":
        aligned = math.floor(steps) * tick
    elif mode == "up":
        aligned = math.ceil(steps) * tick
    else:
        aligned = round(steps) * tick
    return round(aligned, 2)


WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
logging.info(f"Loaded WEBHOOK_SECRET: {WEBHOOK_SECRET}")


LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "webhook_trades.log")

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(console_handler)
logging.getLogger("httpx").setLevel(logging.WARNING)


app = FastAPI()
client = TradovateClient()
symbol_locks = {}
background_tasks = set()
active_entry_orders = {}
active_brackets = {}
symbol_background_tasks = {}


async def get_http_client():
    global persistent_http_client
    if persistent_http_client is None:
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        timeout = httpx.Timeout(10.0, connect=5.0)
        persistent_http_client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            http2=True
        )
    return persistent_http_client


@app.on_event("startup")
async def startup_event():
    logging.info("=== FORWARD APPLICATION STARTUP ===")
    logging.info("*** LIVE TRADING MODE (MNQZ) ***")
    try:
        await client.authenticate()
        logging.info("Live authentication succeeded")
        logging.info(f"Account ID: {client.account_id}")
        logging.info(f"Account Spec: {client.account_spec}")
        logging.info("Initiating startup cleanup")
        try:
            closed_positions = await client.close_all_positions()
            logging.info(f"Closed {len(closed_positions)} positions on startup")
            cancelled_orders = await client.cancel_all_pending_orders()
            logging.info(f"Cancelled {len(cancelled_orders)} pending orders on startup")
        except Exception as cleanup_error:
            logging.warning(f"Startup cleanup warning: {cleanup_error}")
    except Exception as e:
        logging.error("Live authentication failed")
        logging.error(e)
        logging.error(traceback.format_exc())
        raise


@app.on_event("shutdown")
async def shutdown_event():
    global persistent_http_client
    if persistent_http_client:
        await persistent_http_client.aclose()
        persistent_http_client = None
    logging.info("Forward application shutdown complete")


async def cancel_all_orders(symbol):
    list_url = "https://live-api.tradovate.com/v1/order/list"
    cancel_url = "https://live-api.tradovate.com/v1/order/cancel"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    for _ in range(3):
        resp = await http_client.get(list_url, headers=headers)
        resp.raise_for_status()
        orders = resp.json()
        open_orders = []
        for order in orders:
            order_symbol = order.get("symbol")
            status = order.get("status") or order.get("ordStatus")
            if order_symbol != symbol:
                continue
            if status in ("Filled", "Cancelled", "Rejected"):
                continue
            open_orders.append(order)
        if not open_orders:
            break
        cancel_tasks = []
        for order in open_orders:
            oid = order.get("id")
            if oid:
                cancel_tasks.append(asyncio.create_task(
                    cancel_single_order(http_client, cancel_url, oid, symbol, order.get("status"))
                ))
        if cancel_tasks:
            await asyncio.gather(*cancel_tasks, return_exceptions=True)
        await asyncio.sleep(0.1)


async def cancel_single_order(http_client, cancel_url, order_id, symbol, status):
    try:
        await http_client.post(f"{cancel_url}/{order_id}", headers={"Authorization": f"Bearer {client.access_token}"})
        logging.info(f"Cancelled {order_id} ({symbol}) status={status}")
    except Exception as e:
        logging.error(f"Cancel failed {order_id} ({symbol}): {e}")


async def cancel_order_direct(order_id: int, symbol: str, label: str = "") -> bool:
    if not order_id:
        return False
    http_client = await get_http_client()
    try:
        resp = await http_client.post(
            f"https://live-api.tradovate.com/v1/order/cancel/{order_id}",
            headers={"Authorization": f"Bearer {client.access_token}"}
        )
        resp.raise_for_status()
        logging.info(f"✅ Direct cancel {label} order {order_id} ({symbol})")
        return True
    except Exception as e:
        logging.error(f"Direct cancel failed {order_id} ({symbol}): {e}")
        return False


async def flatten_position(symbol):
    url = "https://live-api.tradovate.com/v1/position/closeposition"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    await http_client.post(url, headers=headers, json={"symbol": symbol})


async def wait_until_no_open_orders(symbol, timeout=5):
    url = "https://live-api.tradovate.com/v1/order/list"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    start = asyncio.get_event_loop().time()
    while True:
        resp = await http_client.get(url, headers=headers)
        resp.raise_for_status()
        orders = resp.json()
        open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") in ("Working", "Accepted")]
        if not open_orders:
            return
        if asyncio.get_event_loop().time() - start > timeout:
            logging.warning(f"Timeout waiting for {symbol} orders to clear")
            return
        await asyncio.sleep(0.1)


def parse_alert_to_tradovate_json(alert_text: str, account_id: int) -> dict:
    parsed_data = {}
    if alert_text.startswith("="):
        try:
            json_part, remaining = alert_text[1:].split("\n", 1)
            parsed_data.update(json.loads(json_part))
            alert_text = remaining
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Error parsing JSON block: {e}")
    for line in alert_text.split("\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            parsed_data[key.strip()] = value.strip()
        elif line.strip().upper() in ("BUY", "SELL"):
            parsed_data["action"] = line.strip().capitalize()
    logging.info(f"Parsed alert: {parsed_data}")
    for field in ("symbol", "action"):
        if not parsed_data.get(field):
            raise ValueError(f"Missing or invalid field: {field}")
    for target in ("T1", "STOP", "PRICE"):
        if target in parsed_data:
            parsed_data[target] = float(parsed_data[target])
    return parsed_data


def hash_alert(data: dict) -> str:
    essential_fields = {
        "symbol": data.get("symbol"),
        "action": data.get("action"),
        "PRICE": data.get("PRICE"),
        "T1": data.get("T1"),
        "STOP": data.get("STOP")
    }
    alert_string = json.dumps(essential_fields, sort_keys=True)
    return hashlib.sha256(alert_string.encode()).hexdigest()


def is_duplicate_alert(symbol: str, action: str, data: dict) -> bool:
    current_time = datetime.now()
    alert_hash = hash_alert(data)
    last_data = last_alert.get(symbol)
    if last_data:
        time_diff = (current_time - last_data["timestamp"]).total_seconds()
        if last_data.get("alert_hash") == alert_hash and time_diff < DUPLICATE_THRESHOLD_SECONDS:
            logging.warning(f"Rapid duplicate blocked {symbol} {action}")
            return True
    last_alert[symbol] = {
        "direction": action.lower(),
        "timestamp": current_time,
        "alert_hash": alert_hash
    }
    logging.info(f"Alert accepted {symbol} {action}")
    return False


def mark_trade_completed(symbol: str, direction: str):
    completed_trades[symbol] = {
        "last_completed_direction": direction.lower(),
        "completion_time": datetime.now()
    }
    logging.info(f"Trade completion marked {symbol} {direction}")


def cleanup_old_tracking_data():
    current_time = datetime.now()
    cutoff = current_time - timedelta(seconds=max(DUPLICATE_THRESHOLD_SECONDS, COMPLETED_TRADE_COOLDOWN) * 2)
    stale_symbols = [s for s, data in last_alert.items() if data["timestamp"] < cutoff]
    for sym in stale_symbols:
        last_alert.pop(sym, None)
    stale_completed = [s for s, data in completed_trades.items() if data["completion_time"] < cutoff]
    for sym in stale_completed:
        completed_trades.pop(sym, None)


async def monitor_order_fill(order_id: int) -> bool:
    while True:
        try:
            status_payload = await client.get_order_status(order_id)
            status = status_payload.get("ordStatus") or status_payload.get("status")
            logging.info(f"Order {order_id} status {status}")
            if status == "Filled":
                return True
            if status in ("Cancelled", "Rejected", "Expired"):
                return False
            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Order monitor error {order_id}: {e}")
            await asyncio.sleep(1)


async def monitor_entry_and_manage_brackets(symbol: str, action: str, t1: float, stop: float, entry_order_id: int):
    try:
        entry_filled = await monitor_order_fill(entry_order_id)
        if not entry_filled:
            logging.warning(f"Entry {entry_order_id} not filled for {symbol}")
            active_entry_orders.pop(symbol, None)
            return
        active_entry_orders.pop(symbol, None)
        logging.info(f"Entry order {entry_order_id} filled for {symbol}")
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"
        if action.lower() == "sell":
            tp_price = round_price_to_tick(t1, symbol, "down")
            sl_price = round_price_to_tick(stop, symbol, "up")
        else:
            tp_price = round_price_to_tick(t1, symbol, "up")
            sl_price = round_price_to_tick(stop, symbol, "down")
        if abs(tp_price - sl_price) < get_tick_size(symbol):
            gap = get_tick_size(symbol) * 4
            if action.lower() == "sell":
                sl_price = round_price_to_tick(sl_price + gap, symbol, "up")
            else:
                sl_price = round_price_to_tick(sl_price - gap, symbol, "down")
            logging.warning(f"Adjusted SL for spacing: {sl_price}")
        tp_payload = {
            "accountId": client.account_id,
            "accountSpec": client.account_spec,
            "action": opposite_action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Limit",
            "price": tp_price,
            "timeInForce": "GTC",
            "isAutomated": True
        }
        sl_payload = {
            "accountId": client.account_id,
            "accountSpec": client.account_spec,
            "action": opposite_action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": sl_price,
            "timeInForce": "GTC",
            "isAutomated": True
        }
        tp_result, sl_result = await asyncio.gather(
            client.place_order(symbol=symbol, action=opposite_action, order_data=tp_payload),
            client.place_order(symbol=symbol, action=opposite_action, order_data=sl_payload)
        )
        tp_order_id = tp_result.get("orderId")
        sl_order_id = sl_result.get("orderId")
        if not tp_order_id or not sl_order_id:
            raise ValueError("Failed to place TP/SL orders")
        active_brackets[symbol] = {"TP": tp_order_id, "SL": sl_order_id}
        logging.info(f"Brackets live for {symbol}: TP {tp_order_id}, SL {sl_order_id}")
        await manage_bracket_orders(symbol, action, tp_order_id, sl_order_id)
    except Exception as e:
        logging.error(f"Bracket management error {symbol}: {e}")
        try:
            await cancel_all_orders(symbol)
        except Exception as cleanup_err:
            logging.error(f"Bracket cleanup failed {symbol}: {cleanup_err}")
        finally:
            active_entry_orders.pop(symbol, None)
            active_brackets.pop(symbol, None)


async def manage_bracket_orders(symbol: str, entry_action: str, tp_order_id: int, sl_order_id: int):
    if not (tp_order_id or sl_order_id):
        return
    http_client = await get_http_client()
    headers = {"Authorization": f"Bearer {client.access_token}"}
    order_map = {"TP": tp_order_id, "SL": sl_order_id}
    while True:
        active = 0
        for label, order_id in order_map.items():
            if not order_id:
                continue
            try:
                resp = await http_client.get(f"https://live-api.tradovate.com/v1/order/{order_id}", headers=headers)
                if resp.status_code == 404:
                    active += 1
                    await asyncio.sleep(0.2)
                    continue
                resp.raise_for_status()
                status_payload = resp.json()
                status = status_payload.get("status") or status_payload.get("ordStatus")
                if status in ("Working", "Accepted", "Pending", "New"):
                    active += 1
                    continue
                if status == "Filled":
                    logging.info(f"{label} order {order_id} filled for {symbol}")
                    mark_trade_completed(symbol, entry_action)
                    other_label = "SL" if label == "TP" else "TP"
                    other_id = order_map.get(other_label)
                    if other_id:
                        cancelled = await cancel_order_direct(other_id, symbol, other_label)
                        if not cancelled:
                            try:
                                await client.cancel_order(other_id)
                                logging.info(f"Fallback-cancelled {other_label} order {other_id}")
                            except Exception as cancel_err:
                                logging.error(f"Failed to cancel {other_label} {other_id}: {cancel_err}")
                    await cancel_all_orders(symbol)
                    active_entry_orders.pop(symbol, None)
                    active_brackets.pop(symbol, None)
                    return
                if status in ("Cancelled", "Rejected", "Expired"):
                    order_map[label] = None
                    if symbol in active_brackets:
                        active_brackets[symbol][label] = None
                    continue
                active += 1
            except Exception as e:
                logging.error(f"Bracket monitor error {label} {order_id}: {e}")
                active += 1
        if active == 0:
            logging.info(f"Bracket monitoring finished for {symbol}")
            active_brackets.pop(symbol, None)
            return
        await asyncio.sleep(0.5)


async def cancel_tracked_entry(symbol: str):
    entry_id = active_entry_orders.get(symbol)
    if not entry_id:
        return
    try:
        await client.cancel_order(entry_id)
        logging.info(f"Cancelled tracked entry {entry_id} for {symbol}")
    except Exception as e:
        logging.warning(f"Failed to cancel tracked entry {entry_id}: {e}")
    finally:
        active_entry_orders.pop(symbol, None)


async def cancel_active_bracket(symbol: str):
    bracket = active_brackets.get(symbol)
    if not bracket:
        return
    for label in ("TP", "SL"):
        order_id = bracket.get(label)
        if not order_id:
            continue
        cancelled = await cancel_order_direct(order_id, symbol, f"bracket-{label}")
        if not cancelled:
            try:
                await client.cancel_order(order_id)
                logging.info(f"Cancelled bracket {label} order {order_id}")
            except Exception as e:
                logging.warning(f"Failed to cancel bracket {label} order {order_id}: {e}")
    active_brackets.pop(symbol, None)


async def cancel_symbol_tasks(symbol: str):
    tasks = symbol_background_tasks.pop(symbol, set())
    for task in list(tasks):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logging.info(f"Cancelled background task for {symbol}")
        except Exception as e:
            logging.warning(f"Background task for {symbol} error: {e}")


async def aggressive_account_cleanup(symbol: str):
    logging.info("=== Account-wide cleanup ===")
    cleanup_tasks = [
        ("force_close_positions", asyncio.create_task(client.force_close_all_positions_immediately())),
        ("cancel_pending_orders", asyncio.create_task(client.cancel_all_pending_orders())),
        ("cancel_symbol_orders", asyncio.create_task(cancel_all_orders(symbol)))
    ]
    results = await asyncio.gather(*[task for _, task in cleanup_tasks], return_exceptions=True)
    for (name, _), result in zip(cleanup_tasks, results):
        if isinstance(result, Exception):
            logging.warning(f"Cleanup step {name} failed: {result}")
        else:
            logging.info(f"Cleanup step {name} completed")


async def handle_trade_logic(data: dict):
    entry_order_id = None
    symbol = data.get("symbol")
    action = data.get("action")
    price = float(data.get("PRICE"))
    t1 = float(data.get("T1"))
    stop = float(data.get("STOP"))
    logging.info(f"Fields => symbol:{symbol} action:{action} price:{price} tp:{t1} stop:{stop}")
    if not all([symbol, action, price, t1, stop]):
        missing = [k for k, v in {"symbol": symbol, "action": action, "PRICE": price, "T1": t1, "STOP": stop}.items() if not v]
        raise HTTPException(status_code=400, detail=f"Missing fields: {missing}")
    if symbol in ("CME_MINI:MNQ1!", "MNQ1!", "MNQZ5"):
        symbol = "MNQZ5"
    elif symbol in ("CME_MINI:NQ1!", "NQ1!", "NQZ5"):
        symbol = "NQZ5"
    data["symbol"] = symbol
    data["PRICE"] = price
    data["T1"] = t1
    data["STOP"] = stop
    if is_duplicate_alert(symbol, action, data):
        logging.warning(f"Duplicate blocked {symbol} {action}")
        return {
            "status": "rejected",
            "reason": "rapid_fire_duplicate",
            "message": f"Rapid-fire duplicate alert blocked for {symbol} {action}"
        }
    logging.info(f"Alert approved {symbol} {action}")
    try:
        await cancel_symbol_tasks(symbol)
        await cancel_tracked_entry(symbol)
        await cancel_active_bracket(symbol)
        await cancel_all_orders(symbol)
        await client.close_position(symbol)
    except Exception as sym_cleanup_err:
        logging.warning(f"Symbol cleanup issue {symbol}: {sym_cleanup_err}")
    try:
        await aggressive_account_cleanup(symbol)
    except Exception as account_cleanup_err:
        logging.warning(f"Account cleanup issue: {account_cleanup_err}")
    entry_limit_price = round_price_to_tick(
        price,
        symbol,
        "down" if action.lower() == "buy" else "up"
    )
    if action.lower() == "sell":
        tp_price = round_price_to_tick(t1, symbol, "down")
        sl_price = round_price_to_tick(stop, symbol, "up")
    else:
        tp_price = round_price_to_tick(t1, symbol, "up")
        sl_price = round_price_to_tick(stop, symbol, "down")
    if abs(tp_price - sl_price) < get_tick_size(symbol):
        gap = get_tick_size(symbol) * 4
        if action.lower() == "sell":
            sl_price = round_price_to_tick(sl_price + gap, symbol, "up")
        else:
            sl_price = round_price_to_tick(sl_price - gap, symbol, "down")
        logging.warning(f"Adjusted SL for separation: {sl_price}")
    opposite_action = "Sell" if action.lower() == "buy" else "Buy"
    oso_payload = {
        "accountSpec": client.account_spec,
        "accountId": client.account_id,
        "action": action.capitalize(),
        "symbol": symbol,
        "orderQty": 1,
        "orderType": "Limit",
        "price": entry_limit_price,
        "timeInForce": "GTC",
        "isAutomated": True,
        "bracket1": {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": opposite_action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Limit",
            "price": tp_price,
            "timeInForce": "GTC",
            "isAutomated": True
        },
        "bracket2": {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": opposite_action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": sl_price,
            "timeInForce": "GTC",
            "isAutomated": True
        }
    }
    logging.info(
        "Submitting forward OSO: entry=%s tp=%s sl=%s",
        entry_limit_price,
        tp_price,
        sl_price
    )
    entry_result = await client.place_oso_order(oso_payload)
    entry_order_id = entry_result.get("orderId")
    tp_order_id = entry_result.get("oso1Id")
    sl_order_id = entry_result.get("oso2Id")
    if not entry_order_id or not tp_order_id or not sl_order_id:
        raise ValueError("OSO response missing IDs")
    active_entry_orders[symbol] = entry_order_id
    active_brackets[symbol] = {"TP": tp_order_id, "SL": sl_order_id}
    monitoring_task = asyncio.create_task(
        manage_bracket_orders(symbol, action, tp_order_id, sl_order_id)
    )
    background_tasks.add(monitoring_task)
    symbol_task_set = symbol_background_tasks.setdefault(symbol, set())
    symbol_task_set.add(monitoring_task)

    def _cleanup_task(task, sym=symbol):
        background_tasks.discard(task)
        task_set = symbol_background_tasks.get(sym)
        if task_set:
            task_set.discard(task)
            if not task_set:
                symbol_background_tasks.pop(sym, None)

    monitoring_task.add_done_callback(_cleanup_task)
    return {
        "status": "entry_working",
        "entry_order_id": entry_order_id,
        "tp_order_id": tp_order_id,
        "sl_order_id": sl_order_id,
        "message": "Forward OSO limit entry placed with TP/SL"
    }


@app.post("/webhook")
async def webhook(req: Request):
    logging.info("=== FORWARD WEBHOOK HIT ===")
    try:
        content_type = req.headers.get("content-type", "")
        raw_body = await req.body()
        logging.info(f"Content-Type: {content_type}")
        logging.info(f"Raw body: {raw_body.decode('utf-8')}")
        if content_type == "application/json":
            data = await req.json()
        elif content_type.startswith("text/plain"):
            data = parse_alert_to_tradovate_json(raw_body.decode("utf-8"), client.account_id)
        else:
            raise HTTPException(status_code=400, detail="Unsupported content type")
        if "symbol" not in data:
            raise HTTPException(status_code=400, detail="Symbol missing in alert payload")
        symbol = data.get("symbol")
        lock = symbol_locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            logging.info(f"Lock acquired for {symbol}")
            cleanup_old_tracking_data()
            return await handle_trade_logic(data)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/")
async def root_post(req: Request):
    return await webhook(req)


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {
        "status": "active",
        "service": "tradovate-webhook-forward",
        "strategy": "FORWARD",
        "message": "Forward webhook live. Send POST /webhook"
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main_forward:app", host="0.0.0.0", port=port)



