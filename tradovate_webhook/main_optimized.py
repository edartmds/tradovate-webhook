# 🚀 SPEED OPTIMIZED VERSION - TRADOVATE WEBHOOK MAIN
import os
import logging
import json
import asyncio
import traceback
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient  # 🚀 Use optimized client
import uvicorn
import httpx
import hashlib

# 🚀 SPEED OPTIMIZED DUPLICATE DETECTION
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime, "alert_hash": str}}
completed_trades = {}  # {symbol: {"last_completed_direction": "buy"/"sell", "completion_time": datetime}}
active_orders = []  # Track active order IDs to manage cancellation
DUPLICATE_THRESHOLD_SECONDS = 15  # 🚀 SPEED: Reduced from 30 to 15 seconds 
COMPLETED_TRADE_COOLDOWN = 15  # 🚀 SPEED: Reduced cooldown

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
logging.info(f"Loaded WEBHOOK_SECRET: {WEBHOOK_SECRET}")

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, "webhook_trades.log")
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = FastAPI()
client = TradovateClient()

# Health check endpoints for monitoring/bot requests
@app.get("/")
async def root():
    """Root endpoint for health checks"""
    return {"status": "ok", "service": "tradovate-webhook", "version": "optimized"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.on_event("startup")
async def startup_event():
    logging.info("=== 🚀 SPEED OPTIMIZED APPLICATION STARTING UP ===")
    try:
        await client.authenticate()
        logging.info(f"=== ⚡ AUTHENTICATION SUCCESSFUL ===")
        logging.info(f"Account ID: {client.account_id}")
        logging.info(f"Account Spec: {client.account_spec}")
        logging.info(f"Access Token: {'***' if client.access_token else 'None'}")
        
        # 🚀 SPEED: Parallel startup cleanup
        logging.info("=== ⚡ FAST STARTUP CLEANUP ===")
        try:
            cleanup_start = time.time()
            # Execute cleanup operations in parallel
            success = await client.force_close_all_positions_immediately()
            cleanup_time = time.time() - cleanup_start
            
            if success:
                logging.info(f"✅ Startup cleanup completed in {cleanup_time:.2f}s")
            else:
                logging.warning(f"⚠️ Startup cleanup partially failed in {cleanup_time:.2f}s")
        except Exception as e:
            logging.warning(f"Startup cleanup failed (non-critical): {e}")
            
    except Exception as e:
        logging.error(f"=== AUTHENTICATION FAILED ===")
        logging.error(f"Error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """🚀 SPEED: Clean shutdown with connection pool cleanup"""
    logging.info("=== 🚀 APPLICATION SHUTTING DOWN ===")
    try:
        await client.close_client()
        logging.info("✅ HTTP client connections closed")
    except Exception as e:
        logging.error(f"Error during shutdown: {e}")


# 🚀 SPEED OPTIMIZED: Fast order monitoring with reduced polling
async def monitor_all_orders_fast(order_tracking, symbol, stop_order_data=None):
    """
    🚀 SPEED OPTIMIZED monitoring with faster polling and early exits
    """
    logging.info(f"🚀 Starting FAST order monitoring for {symbol}")
    entry_filled = False
    stop_placed = False
    monitoring_start_time = asyncio.get_event_loop().time()
    max_monitoring_time = 1800  # 🚀 SPEED: Reduced to 30 minutes

    if not stop_order_data:
        logging.error("CRITICAL: No stop_order_data provided when starting monitoring")
    else:
        logging.info(f"Will use this STOP data when entry fills: {stop_order_data}")

    # 🚀 SPEED: Dynamic polling - faster when active, slower when stable
    fast_poll_interval = 0.3  # Very fast polling when active
    slow_poll_interval = 1.0  # Slower when waiting
    poll_interval = fast_poll_interval

    # 🚀 SPEED: Reuse HTTP client
    client_http = await client._get_http_client()
    headers = {"Authorization": f"Bearer {client.access_token}"}

    while True:
        try:
            active_orders = {}
            logging.info(f"Order tracking state: {order_tracking}")

            # 🚀 SPEED: Parallel order status checks
            status_tasks = []
            for label, order_id in order_tracking.items():
                if order_id is None:
                    continue
                url = f"https://demo-api.tradovate.com/v1/order/{order_id}"
                status_tasks.append(client_http.get(url, headers=headers))

            if status_tasks:
                responses = await asyncio.gather(*status_tasks, return_exceptions=True)
                
                # Process responses
                labels = [label for label, order_id in order_tracking.items() if order_id is not None]
                for i, response in enumerate(responses):
                    if isinstance(response, Exception):
                        continue
                        
                    label = labels[i]
                    order_id = order_tracking[label]
                    
                    try:
                        response.raise_for_status()
                        order_status = response.json()
                        status = order_status.get("status")

                        # 🚀 SPEED: Fast processing of order states
                        if label == "ENTRY" and status and status.lower() == "filled" and not entry_filled:
                            entry_filled = True
                            poll_interval = fast_poll_interval  # Speed up after entry filled
                            logging.info(f"⚡ ENTRY order filled for {symbol}. Placing STOP and TP orders.")

                            # 🚀 SPEED: Fast OSO placement after entry
                            if stop_order_data and "T1" in stop_order_data:
                                oso_payload = {
                                    "accountSpec": client.account_spec,
                                    "accountId": client.account_id,
                                    "action": stop_order_data.get("action"),
                                    "symbol": stop_order_data.get("symbol"),
                                    "orderQty": stop_order_data.get("orderQty", 1),
                                    "orderType": "Stop",
                                    "price": stop_order_data.get("stopPrice"),
                                    "timeInForce": "GTC",
                                    "isAutomated": True,
                                    "bracket1": {
                                        "action": "Sell" if stop_order_data.get("action") == "Buy" else "Buy",
                                        "orderType": "Limit",
                                        "price": stop_order_data.get("T1"),
                                        "timeInForce": "GTC"
                                    }
                                }

                                try:
                                    response = await client_http.post(
                                        f"https://demo-api.tradovate.com/v1/order/placeOSO",
                                        headers={"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"},
                                        json=oso_payload
                                    )
                                    response.raise_for_status()
                                    oso_result = response.json()

                                    if "orderId" in oso_result:
                                        logging.info(f"⚡ OSO order placed successfully: {oso_result}")
                                        stop_placed = True
                                    else:
                                        raise ValueError(f"Failed to place OSO order: {oso_result}")
                                except Exception as e:
                                    logging.error(f"Error placing OSO order: {e}")

                        elif label == "STOP" and status and status.lower() == "filled":
                            logging.info(f"⚡ STOP order filled for {symbol}. Exiting trade.")
                            trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                            mark_trade_completed(symbol, trade_direction)

                            # 🚀 SPEED: Fast TP cancellation
                            if order_tracking.get("TP1"):
                                try:
                                    cancel_resp = await client_http.post(
                                        f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['TP1']}", 
                                        headers=headers
                                    )
                                    if cancel_resp.status_code == 200:
                                        logging.info(f"⚡ TP1 order cancelled after STOP fill.")
                                except Exception as e:
                                    logging.error(f"Exception while cancelling TP1 order: {e}")
                            return  # 🚀 SPEED: Early exit

                        elif label == "TP1" and status and status.lower() == "filled":
                            logging.info(f"⚡ TP1 order filled for {symbol}. Trade completed successfully.")
                            trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                            mark_trade_completed(symbol, trade_direction)

                            # 🚀 SPEED: Fast STOP cancellation
                            if order_tracking.get("STOP"):
                                try:
                                    cancel_resp = await client_http.post(
                                        f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['STOP']}", 
                                        headers=headers
                                    )
                                    if cancel_resp.status_code == 200:
                                        logging.info(f"⚡ STOP order cancelled after TP1 fill.")
                                except Exception as e:
                                    logging.error(f"Exception while cancelling STOP order: {e}")
                            return  # 🚀 SPEED: Early exit

                        elif status in ["Working", "Accepted"]:
                            active_orders[label] = order_id

                    except Exception as e:
                        logging.error(f"Error processing order {order_id}: {e}")

            # 🚀 SPEED: Early exit conditions
            if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
                logging.warning(f"Order monitoring timeout reached for {symbol}. Stopping.")
                return

            if not active_orders:
                logging.info("⚡ No active orders remaining. Stopping monitoring.")
                return

            # 🚀 SPEED: Dynamic polling adjustment
            if entry_filled and not stop_placed:
                poll_interval = fast_poll_interval  # Keep fast polling until stop is placed
            elif entry_filled and stop_placed:
                poll_interval = slow_poll_interval  # Slow down after everything is set
            else:
                poll_interval = fast_poll_interval  # Fast until entry fills

            await asyncio.sleep(poll_interval)

        except Exception as e:
            logging.error(f"Error in order monitoring: {e}")
            await asyncio.sleep(1)  # 🚀 SPEED: Reduced error recovery time


def create_alert_hash(symbol, action, price, t1, stop):
    """🚀 SPEED: Optimized hash creation"""
    alert_string = f"{symbol}_{action}_{price}_{t1}_{stop}"
    return hashlib.md5(alert_string.encode()).hexdigest()


def is_duplicate_alert(symbol, action, alert_hash):
    """🚀 SPEED: Optimized duplicate detection with faster logic"""
    now = datetime.now()
    
    # Check for identical alerts within threshold
    if symbol in last_alert:
        last_entry = last_alert[symbol]
        time_diff = (now - last_entry["timestamp"]).total_seconds()
        
        # 🚀 SPEED: Only block truly identical alerts within short timeframe
        if time_diff < DUPLICATE_THRESHOLD_SECONDS and last_entry["alert_hash"] == alert_hash:
            logging.info(f"⚡ BLOCKED: Identical alert for {symbol} within {DUPLICATE_THRESHOLD_SECONDS} seconds")
            return True
    
    return False


def mark_trade_completed(symbol, direction):
    """🚀 SPEED: Fast trade completion marking"""
    completed_trades[symbol] = {
        "last_completed_direction": direction.lower(),
        "completion_time": datetime.now()
    }
    logging.info(f"⚡ Trade completed for {symbol} direction {direction}")


@app.post("/webhook")
async def webhook_optimized(req: Request):
    """🚀 SPEED OPTIMIZED webhook handler with parallel processing"""
    
    execution_start = time.time()  # 🚀 Track total execution time
    
    try:
        # 🚀 SPEED: Fast request processing with validation
        body = await req.body()
        
        # Check for empty body
        if not body:
            logging.warning("⚠️ Empty request body received (likely health check)")
            return {"status": "error", "message": "Empty request body"}
        
        try:
            data = json.loads(body.decode())
        except json.JSONDecodeError:
            logging.warning(f"⚠️ Invalid JSON received: {body[:100]}")  # Log first 100 chars
            return {"status": "error", "message": "Invalid JSON format"}
        
        # Validate required fields
        if not isinstance(data, dict):
            logging.warning("⚠️ Request data is not a JSON object")
            return {"status": "error", "message": "Request must be JSON object"}
        
        logging.info(f"⚡ Webhook received: {json.dumps(data, indent=2)}")
        
        # Extract data with defaults for speed
        symbol = data.get("symbol", "NQ")
        action = data.get("action", "buy").capitalize()
        price = float(data.get("price", 0))
        t1 = float(data.get("T1", price + 10))
        stop = float(data.get("STOP", price - 10))
        
        # 🚀 SPEED: Fast duplicate detection
        alert_hash = create_alert_hash(symbol, action, price, t1, stop)
        
        if is_duplicate_alert(symbol, action, alert_hash):
            execution_time = time.time() - execution_start
            logging.info(f"⚡ Request completed in {execution_time:.3f}s (duplicate blocked)")
            return {"status": "duplicate_blocked", "execution_time": f"{execution_time:.3f}s"}
        
        # Update tracking
        last_alert[symbol] = {
            "direction": action.lower(),
            "timestamp": datetime.now(),
            "alert_hash": alert_hash
        }
        
        # 🚀 SPEED: Determine order type quickly
        order_config = await client.determine_optimal_order_type(symbol, action, price)
        order_type = order_config.get("orderType", "Stop")
        stop_price = order_config.get("stopPrice", price)
        
        logging.info(f"⚡ SPEED MODE: {order_type} order detected - optimizing for fastest execution")
        
        # 🚀 SPEED: Parallel position closure and order setup
        cleanup_start = time.time()
        
        # Step 1: Fast position and order cleanup
        success = await client.force_close_all_positions_immediately()
        cleanup_time = time.time() - cleanup_start
        
        if success:
            logging.info(f"✅ Position cleanup completed in {cleanup_time:.3f}s")
        else:
            logging.warning(f"⚠️ Position cleanup issues in {cleanup_time:.3f}s - proceeding")
        
        # 🚀 SPEED: Build OSO payload for immediate execution
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"
        
        oso_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": order_type,
            "timeInForce": "GTC",
            "isAutomated": True,
            # Take Profit bracket (bracket1)
            "bracket1": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Limit",
                "price": t1,
                "timeInForce": "GTC",
                "isAutomated": True
            }
        }
        
        # Add price field based on order type
        if order_type == "Stop":
            oso_payload["stopPrice"] = stop_price
        else:
            oso_payload["price"] = price
            
        # 🚀 SPEED: Fast order placement
        order_start = time.time()
        logging.info(f"⚡ PLACING FAST OSO ORDER")
        
        try:
            oso_response = await client.place_oso_order(oso_payload)
            order_time = time.time() - order_start
            
            logging.info(f"✅ OSO order placed in {order_time:.3f}s: {json.dumps(oso_response, indent=2)}")
            
            # 🚀 SPEED: Extract order IDs for fast monitoring
            entry_order_id = oso_response.get("orderId")
            bracket_orders = oso_response.get("bracketOrders", [])
            
            order_tracking = {"ENTRY": entry_order_id}
            
            if bracket_orders:
                for bracket in bracket_orders:
                    bracket_type = bracket.get("orderType", "").upper()
                    if "LIMIT" in bracket_type or bracket.get("price") == t1:
                        order_tracking["TP1"] = bracket.get("orderId")
                    elif "STOP" in bracket_type or bracket.get("stopPrice") == stop:
                        order_tracking["STOP"] = bracket.get("orderId")
            
            # 🚀 SPEED: Prepare monitoring data
            stop_order_data = {
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "stopPrice": stop,
                "T1": t1
            }
            
            # 🚀 SPEED: Start fast monitoring in background
            asyncio.create_task(monitor_all_orders_fast(order_tracking, symbol, stop_order_data))
            
            execution_time = time.time() - execution_start
            
            return {
                "status": "success",
                "message": f"⚡ SPEED OPTIMIZED: OSO order placed successfully",
                "order_id": entry_order_id,
                "symbol": symbol,
                "action": action,
                "order_type": order_type,
                "execution_time": f"{execution_time:.3f}s",
                "cleanup_time": f"{cleanup_time:.3f}s",
                "order_placement_time": f"{order_time:.3f}s"
            }
            
        except Exception as e:
            logging.error(f"❌ OSO order placement failed: {e}")
            execution_time = time.time() - execution_start
            raise HTTPException(status_code=500, detail=f"Order placement failed: {e}")
            
    except Exception as e:
        execution_time = time.time() - execution_start
        logging.error(f"❌ Webhook processing failed in {execution_time:.3f}s: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {e}")


# 🚀 SPEED: Fast server configuration
if __name__ == "__main__":
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        # 🚀 SPEED OPTIMIZATIONS:
        loop="uvloop",  # Use fastest event loop
        workers=1,      # Single worker for optimal performance
        access_log=False,  # Disable access logging for speed
        reload=False    # Disable reload for production speed
    )
