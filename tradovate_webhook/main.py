import os
import logging
import json
import asyncio
import traceback
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import hashlib


# 🔥 RELAXED DUPLICATE DETECTION FOR AUTOMATED TRADING
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime, "alert_hash": str}}
completed_trades = {}  # {symbol: {"last_completed_direction": "buy"/"sell", "completion_time": datetime}}
active_orders = []  # Track active order IDs to manage cancellation
DUPLICATE_THRESHOLD_SECONDS = 5  # 5 seconds - only prevent rapid-fire identical alerts
COMPLETED_TRADE_COOLDOWN = 30  # 30 seconds - minimal cooldown for automated trading


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

@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "tradovate-webhook",
        "timestamp": datetime.now().isoformat(),
        "endpoints": ["/webhook", "/tradingview", "/"]
    }


# Symbol mappings for alerts
SYMBOL_MAPPINGS = {
    "NQ1!": "NQU5",    # Example mapping
    "ES1!": "ESU5",    # Example mapping
    # Add more as needed
}


def get_mapped_symbol(alert_symbol: str) -> str:
    """Map alert symbol to Tradovate symbol"""
    mapped = SYMBOL_MAPPINGS.get(alert_symbol, alert_symbol)
    logging.info(f"Mapped symbol to: {mapped}")
    return mapped


@app.on_event("startup")
async def startup_event():
    """Startup tasks"""
    logging.info("=== APPLICATION STARTING UP ===")
    try:
        await client.authenticate()
        
        # Clean slate on startup - close existing positions
        try:
            positions = await client.close_all_positions()
            logging.info(f"Startup cleanup: Closed {len(positions)} existing positions")
        except Exception as e:
            logging.warning(f"Startup position cleanup failed: {e}")
            
        # Cancel existing orders
        try:
            orders = await client.cancel_all_pending_orders()
            logging.info(f"Startup cleanup: Cancelled {len(orders)} existing pending orders")
        except Exception as e:
            logging.warning(f"Startup order cleanup failed: {e}")
            
    except Exception as e:
        logging.error(f"=== AUTHENTICATION FAILED ===")
        logging.error(f"Error: {e}")
        raise e


def hash_alert(data: dict) -> str:
    """Generate a hash for alert data to detect duplicates"""
    # Create a string representation of the essential alert data
    alert_string = f"{data.get('symbol')}-{data.get('action')}-{data.get('PRICE')}-{data.get('T1')}-{data.get('STOP')}"
    return hashlib.md5(alert_string.encode()).hexdigest()


def is_duplicate_alert(symbol: str, alert_data: dict) -> bool:
    """
    Check if this is a duplicate alert within the threshold window.
    Now only blocks rapid-fire identical alerts, allowing full automation.
    """
    current_time = datetime.now()
    alert_hash = hash_alert(alert_data)
    
    if symbol in last_alert:
        last_alert_time = last_alert[symbol]["timestamp"]
        last_alert_hash = last_alert[symbol]["alert_hash"]
        time_diff = (current_time - last_alert_time).total_seconds()
        
        # Only block if it's the exact same alert within the threshold
        if last_alert_hash == alert_hash and time_diff < DUPLICATE_THRESHOLD_SECONDS:
            logging.warning(f"🚫 DUPLICATE ALERT BLOCKED: {symbol} - identical alert within {time_diff:.1f}s")
            return True
            
        logging.info(f"✅ ALERT ALLOWED: {symbol} - different alert or outside {DUPLICATE_THRESHOLD_SECONDS}s window")
    else:
        logging.info(f"✅ FIRST ALERT: {symbol} - no previous alerts recorded")
    
    return False


def mark_trade_completed(symbol: str, direction: str):
    """Mark a trade as completed for tracking purposes"""
    current_time = datetime.now()
    completed_trades[symbol] = {
        "last_completed_direction": direction.lower(),
        "completion_time": current_time
    }
    logging.info(f"📝 Trade marked complete: {symbol} {direction} at {current_time}")


async def monitor_entry_and_place_brackets(entry_order_id: str, symbol: str, bracket_data: dict):
    """
    Monitor entry order for fill, then place flipped bracket orders.
    """
    logging.info(f"🔍 Starting bracket monitoring for entry order {entry_order_id}")
    
    max_monitoring_time = 3600  # 1 hour timeout
    start_time = asyncio.get_event_loop().time()
    
    while True:
        try:
            # Check if entry order is filled using the proper API method
            order_status = await client.get_order_by_id(entry_order_id)
            
            if order_status is None:
                logging.warning(f"⚠️ Entry order {entry_order_id} not found - may have been filled and removed from active orders")
                # Order not found might mean it was filled and no longer in active orders
                # Let's assume it was filled and proceed with bracket placement
                logging.info(f"🎉 ASSUMING ENTRY FILLED! Placing flipped bracket orders for {symbol}")
            else:
                status = order_status.get("ordStatus", "").lower()
                logging.info(f"📊 Entry order {entry_order_id} status: {status}")
                
                if status not in ["filled", "partialfill", "completely_filled"]:
                    if status in ["cancelled", "rejected", "expired"]:
                        logging.warning(f"⚠️ Entry order {entry_order_id} was {status} - no brackets will be placed")
                        return  # Exit monitoring
                    
                    # Continue monitoring if still pending/working
                    # Check timeout
                    if asyncio.get_event_loop().time() - start_time > max_monitoring_time:
                        logging.warning(f"⏰ Monitoring timeout for entry order {entry_order_id}")
                        return
                        
                    # Wait before next check
                    await asyncio.sleep(2)
                    continue
                
                logging.info(f"🎉 ENTRY FILLED! Placing flipped bracket orders for {symbol}")
            
            # Place Take Profit (Limit) order - Original STOP becomes TP
            tp_payload = {
                "accountSpec": bracket_data["account_spec"],
                "accountId": bracket_data["account_id"],
                "action": bracket_data["opposite_action"],  # Opposite of entry action
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Limit",
                "price": bracket_data["take_profit_price"],  # Original STOP
                "timeInForce": "GTC",
                "isAutomated": True
            }
            
            # Place Stop Loss order - Original T1 becomes SL
            sl_payload = {
                "accountSpec": bracket_data["account_spec"],
                "accountId": bracket_data["account_id"],
                "action": bracket_data["opposite_action"],  # Opposite of entry action
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Stop",
                "stopPrice": bracket_data["stop_loss_price"],  # Original T1
                "timeInForce": "GTC",
                "isAutomated": True
            }
            
            # Place both bracket orders
            try:
                # Place Take Profit
                tp_result = await client.place_order(tp_payload)
                logging.info(f"✅ TAKE PROFIT placed: {tp_result}")
                
                # Place Stop Loss
                sl_result = await client.place_order(sl_payload)
                logging.info(f"✅ STOP LOSS placed: {sl_result}")
                
                logging.info(f"🔥 FLIPPED BRACKETS COMPLETE: {symbol}")
                logging.info(f"🔥 TP: {bracket_data['take_profit_price']} (was original STOP)")
                logging.info(f"🔥 SL: {bracket_data['stop_loss_price']} (was original T1)")
                
                # Mark trade as properly set up
                mark_trade_completed(symbol, bracket_data["flipped_action"])
                
            except Exception as e:
                logging.error(f"❌ Failed to place bracket orders: {e}")
            
            return  # Exit monitoring
                    
        except Exception as e:
            logging.error(f"❌ Error in bracket monitoring: {e}")
            await asyncio.sleep(5)


@app.post("/webhook")
async def webhook(req: Request):
    """Main webhook endpoint for trading signals"""
    logging.info("=== WEBHOOK ENDPOINT HIT ===")
    
    # 🔥 COMPREHENSIVE REQUEST LOGGING for debugging
    logging.info(f"📨 Request headers: {dict(req.headers)}")
    logging.info(f"📨 Request method: {req.method}")
    logging.info(f"📨 Request URL: {req.url}")
    
    try:
        # Handle authentication if required (flexible for testing)
        auth_header = req.headers.get("authorization")
        
        # 🔥 FLEXIBLE AUTHENTICATION: Allow testing without webhook secret
        if WEBHOOK_SECRET:
            # If webhook secret is set, check authentication
            if auth_header != f"Bearer {WEBHOOK_SECRET}":
                logging.warning(f"❌ Invalid webhook authentication. Expected: 'Bearer {WEBHOOK_SECRET}', Got: '{auth_header}'")
                logging.warning("📝 TIP: For testing, you can remove WEBHOOK_SECRET environment variable")
                # For now, allow requests through for testing - comment out the line below to enforce auth
                # raise HTTPException(status_code=401, detail="Invalid authentication")
                logging.info("🔓 AUTHENTICATION BYPASSED FOR TESTING - proceeding anyway")
        else:
            logging.info("🔓 No webhook secret configured - authentication disabled for testing")
        
        # Parse request
        content_type = req.headers.get("content-type", "").lower()
        logging.info(f"Content-Type: {content_type}")
        
        if "application/json" in content_type:
            data = await req.json()
            logging.info(f"JSON Data: {data}")
        else:
            # Handle raw text format
            body = await req.body()
            raw_text = body.decode("utf-8")
            logging.info(f"Raw body: {raw_text}")
            
            # Parse text format
            data = {}
            for line in raw_text.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key.strip()] = value.strip()
            
            logging.info(f"Raw alert text: {raw_text}")
        
        # Parse required fields
        symbol = data.get("symbol")
        action = data.get("action")
        price_str = data.get("PRICE") or data.get("price")
        t1_str = data.get("T1") or data.get("t1")
        stop_str = data.get("STOP") or data.get("stop")
        
        # Log parsed values for debugging
        logging.info(f"Parsed symbol = {symbol}")
        logging.info(f"Parsed action = {action}")
        logging.info(f"Parsed PRICE = {price_str}")
        logging.info(f"Parsed T1 = {t1_str}")
        logging.info(f"Parsed STOP = {stop_str}")
        
        # Validate required fields
        if not all([symbol, action, price_str, t1_str, stop_str]):
            missing = [f for f, v in [("symbol", symbol), ("action", action), ("PRICE", price_str), ("T1", t1_str), ("STOP", stop_str)] if not v]
            raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")
        
        # Convert to proper types
        try:
            t1 = float(t1_str)
            stop = float(stop_str)
            price = float(price_str)
            logging.info(f"Converted T1 to float: {t1}")
            logging.info(f"Converted STOP to float: {stop}")
            logging.info(f"Converted PRICE to float: {price}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid numeric values: {e}")
        
        # Update data with converted values
        data.update({"symbol": symbol, "action": action, "PRICE": price, "T1": t1, "STOP": stop})
        logging.info(f"Complete parsed alert data: {data}")
        
        logging.info(f"=== PARSED ALERT DATA: {data} ===")
        
        # Map symbol if needed
        symbol = get_mapped_symbol(symbol)
        
        # 🔥 FLIPPED STRATEGY: REVERSE everything
        # Original: BUY -> New: SELL
        # Original: SELL -> New: BUY  
        # Original: T1 (take profit) -> New: STOP (stop loss)
        # Original: STOP (stop loss) -> New: T1 (take profit)
        
        logging.info(f"Original fields - Symbol: {symbol}, Action: {action}, Price: {price}, T1: {t1}, Stop: {stop}")
        
        # FLIP the action (BUY <-> SELL)
        if action.lower() == "buy":
            flipped_action = "Sell"
        elif action.lower() == "sell":
            flipped_action = "Buy"
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action: {action}")
            
        # SWAP T1 and STOP levels  
        # Original T1 becomes new STOP, Original STOP becomes new T1
        original_t1 = t1
        original_stop = stop
        t1 = original_stop    # New T1 = Original STOP
        stop = original_t1    # New STOP = Original T1
        
        logging.info(f"REVERSED ORDERS - Original: {action} -> New: {flipped_action}")
        logging.info(f"SWAPPED LEVELS - Original T1: {original_t1} -> New Stop: {stop}, Original Stop: {original_stop} -> New T1: {t1}")
        logging.info(f"Final fields - Symbol: {symbol}, Action: {flipped_action}, Price: {price}, T1: {t1}, Stop: {stop}")
        
        # For bracket orders, we need the opposite action (to close the position)
        opposite_action = "Buy" if flipped_action == "Sell" else "Sell"
        
        # Check for duplicates using the FLIPPED action for proper tracking
        if is_duplicate_alert(symbol, data):
            return {"status": "duplicate", "message": "Duplicate alert ignored"}
        
        # 🔄 UPDATED DUPLICATE TRACKING: Track the FLIPPED action to allow proper alternation
        logging.info(f"🔄 UPDATING DUPLICATE TRACKING: Original {action} → Flipped {flipped_action}")
        
        # Create flipped data for proper duplicate detection of actual trades placed
        flipped_data = data.copy()
        flipped_data["action"] = flipped_action  # Track the actual action being placed
        flipped_data["original_action"] = action  # Keep original for reference
        
        # Update the tracking with the FLIPPED action to allow proper signal alternation
        current_time = datetime.now()
        flipped_hash = hash_alert(flipped_data)
        last_alert[symbol] = {
            "direction": flipped_action.lower(),  # Track the actual trade direction
            "timestamp": current_time,
            "alert_hash": flipped_hash,
            "original_direction": action.lower()  # Keep original for reference
        }
        
        logging.info(f"✅ ALERT APPROVED: {symbol} {action} - Proceeding with FLIPPED automated trading")
        logging.info(f"🔄 STRATEGY: Will place {flipped_action} order instead of {action}")
        logging.info(f"🔄 BRACKETS: TP={stop}, SL={t1} (swapped from original)")
        logging.info(f"🔄 TRACKING: Now tracking {flipped_action} direction for future duplicate detection")
        
        # STEP 1: Close all existing positions to prevent over-leveraging  
        logging.info("🔥 === CLOSING ALL EXISTING POSITIONS === 🔥")
        try:
            success = await client.force_close_all_positions_immediately()
            if success:
                logging.info("✅ All existing positions successfully closed")
            else:
                logging.error("❌ CRITICAL: Failed to close all positions - proceeding anyway")
        except Exception as e:
            logging.error(f"❌ CRITICAL ERROR closing positions: {e}")

        # STEP 2: Cancel all existing pending orders to prevent over-leveraging
        logging.info("=== CANCELLING ALL PENDING ORDERS ===")
        try:
            cancelled_orders = await client.cancel_all_pending_orders()
            logging.info(f"Successfully cancelled {len(cancelled_orders)} pending orders")
        except Exception as e:
            logging.warning(f"Failed to cancel some orders: {e}")

        # STEP 3: Place SIMPLE market entry order
        logging.info(f"=== PLACING SIMPLE MARKET ORDER ===")
        logging.info(f"Original Alert: {action} {symbol} at {price}")
        logging.info(f"FLIPPED ORDER: {flipped_action} {symbol} (Market execution)")
        
        entry_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": flipped_action,  # FLIPPED: Use opposite of alert action
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Market",     # SIMPLE: Always use market orders
            "timeInForce": "GTC",
            "isAutomated": True
        }
        
        logging.info(f"🎯 MARKET ORDER: Entry will execute immediately at current market price")
        logging.info(f"=== ENTRY ORDER PAYLOAD ===")
        logging.info(f"{json.dumps(entry_payload, indent=2)}")
        
        # Prepare bracket data for when entry fills
        bracket_data = {
            "symbol": symbol,
            "flipped_action": flipped_action,
            "opposite_action": opposite_action,
            "take_profit_price": stop,    # Original STOP becomes TP
            "stop_loss_price": t1,       # Original T1 becomes SL
            "account_spec": client.account_spec,
            "account_id": client.account_id
        }
        logging.info(f"🔄 BRACKET DATA PREPARED: TP={stop} (was STOP), SL={t1} (was T1)")
        
        # STEP 4: Place entry order and monitor for fill
        logging.info("=== PLACING SIMPLE ENTRY ORDER ===")
        
        try:
            # Place market entry order
            start_time = time.time()
            entry_result = await client.place_order(entry_payload)
            execution_time = (time.time() - start_time) * 1000
           
            logging.info(f"✅ ENTRY ORDER PLACED SUCCESSFULLY in {execution_time:.2f}ms")
            logging.info(f"🎉 ENTRY TRADE: {flipped_action} {symbol} (brackets will be placed after fill)")
            logging.info(f"📊 Original Alert: {action} → Flipped to: {flipped_action}")
            logging.info(f"Entry Result: {entry_result}")
            
            # Start monitoring for entry fill and bracket placement
            if 'orderId' in entry_result:
                entry_order_id = entry_result['orderId']
                logging.info(f"🔍 Starting monitoring for entry order {entry_order_id}")
                
                # Start background task to monitor and place brackets
                asyncio.create_task(monitor_entry_and_place_brackets(
                    entry_order_id, 
                    symbol, 
                    bracket_data
                ))
            else:
                logging.warning("⚠️ No orderId in entry result - cannot monitor for brackets")
           
            logging.info(f"📝 Recording successful FLIPPED trade placement: {symbol} {flipped_action}")
           
            return {
                "status": "success",
                "entry_order": entry_result,
                "execution_time_ms": execution_time,
                "order_type": "Market",
                "symbol": symbol,
                "original_alert": action,
                "flipped_action": flipped_action,
                "pending_brackets": {
                    "take_profit": stop,
                    "stop_loss": t1
                }
            }
           
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000 if 'start_time' in locals() else 0
            logging.error(f"❌ Order placement failed after {execution_time:.2f}ms: {e}")
            
            import traceback
            logging.error(f"Order Error traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Order placement failed: {str(e)}")

    except Exception as e:
        logging.error(f"=== ERROR IN WEBHOOK ===")
        logging.error(f"Error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/")
async def root_webhook(req: Request):
    """Handle webhook signals sent to root path instead of /webhook"""
    logging.info("=== WEBHOOK RECEIVED AT ROOT PATH (/) ===")
    try:
        return await webhook(req)
    except Exception as e:
        logging.error(f"Error in root_webhook: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Root webhook error: {str(e)}")

@app.post("/tradingview")
async def tradingview_webhook(req: Request):
    """Alternative webhook endpoint for TradingView"""
    logging.info("=== WEBHOOK RECEIVED AT /tradingview ===")
    try:
        return await webhook(req)
    except Exception as e:
        logging.error(f"Error in tradingview_webhook: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"TradingView webhook error: {str(e)}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    logging.info(f"Starting Tradovate webhook server on port {port}")
    logging.info("=== FLIPPED STRATEGY ACTIVE ===")
    logging.info("📊 BUY signals → SELL orders")  
    logging.info("📊 SELL signals → BUY orders")
    logging.info("📊 T1/STOP levels swapped for proper bracket placement")
    logging.info("📊 Market orders for immediate execution")
    uvicorn.run(app, host="0.0.0.0", port=port)
