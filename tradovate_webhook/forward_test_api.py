from fastapi import FastAPI, Request, HTTPException
from forward_test_manager import test_manager
import json
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Forward Testing API", description="API for testing multiple TradingView indicator configurations")

@app.get("/")
async def root():
    return {"message": "Forward Testing API is running", "timestamp": datetime.now()}

@app.post("/test-signal")
async def receive_test_signal(req: Request):
    """
    Receive signals for forward testing only (no actual trading)
    Use this endpoint for testing indicator variations
    """
    try:
        content_type = req.headers.get("content-type")
        raw_body = await req.body()
        
        if content_type == "application/json":
            data = await req.json()
        else:
            # Parse text format similar to your main webhook
            text_data = raw_body.decode("utf-8")
            data = parse_text_alert(text_data)
        
        logging.info(f"📊 Forward test signal received: {data}")
        
        # Process through forward test manager
        test_manager.process_signal(data)
        
        return {
            "status": "success",
            "message": "Signal processed for forward testing",
            "config_id": data.get('config_id', 'auto'),
            "timestamp": datetime.now()
        }
        
    except Exception as e:
        logging.error(f"Error processing test signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results")
async def get_results():
    """Get current forward testing results"""
    try:
        leaderboard = test_manager.get_leaderboard()
        
        return {
            "status": "success",
            "timestamp": datetime.now(),
            "total_configs": len(test_manager.test_configs),
            "active_positions": len(test_manager.active_positions),
            "leaderboard": leaderboard
        }
    except Exception as e:
        logging.error(f"Error getting results: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/{config_id}")
async def get_config_results(config_id: str):
    """Get detailed results for a specific configuration"""
    try:
        if config_id not in test_manager.test_configs:
            raise HTTPException(status_code=404, detail="Configuration not found")
        
        config = test_manager.test_configs[config_id]
        results = test_manager.test_results[config_id]
        active_position = test_manager.active_positions.get(config_id)
        
        return {
            "config_id": config_id,
            "config": config,
            "metrics": results['metrics'],
            "trades": results['trades'],
            "active_position": active_position,
            "timestamp": datetime.now()
        }
    except Exception as e:
        logging.error(f"Error getting config results: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/register-config")
async def register_config(config_data: dict):
    """Register a new test configuration"""
    try:
        config_id = config_data.get('config_id')
        if not config_id:
            raise HTTPException(status_code=400, detail="config_id is required")
        
        test_manager.register_test_config(config_id, config_data)
        
        return {
            "status": "success",
            "message": f"Configuration {config_id} registered",
            "config_id": config_id
        }
    except Exception as e:
        logging.error(f"Error registering config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/close-position/{config_id}")
async def close_position(config_id: str, exit_data: dict = None):
    """Manually close a position for testing"""
    try:
        if config_id not in test_manager.active_positions:
            raise HTTPException(status_code=404, detail="No active position for this config")
        
        test_manager.close_position(config_id, exit_data)
        
        return {
            "status": "success",
            "message": f"Position closed for {config_id}",
            "config_id": config_id
        }
    except Exception as e:
        logging.error(f"Error closing position: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/export")
async def export_results():
    """Export all results to CSV"""
    try:
        filename = test_manager.export_results()
        return {
            "status": "success",
            "message": "Results exported",
            "filename": filename
        }
    except Exception as e:
        logging.error(f"Error exporting results: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def parse_text_alert(text_data: str) -> dict:
    """Parse text-based alert similar to your main webhook"""
    parsed_data = {}
    
    # Handle JSON-like structure at the beginning
    if text_data.startswith("="):
        try:
            json_part, remaining_text = text_data[1:].split("\\n", 1)
            json_data = json.loads(json_part)
            parsed_data.update(json_data)
            text_data = remaining_text
        except (json.JSONDecodeError, ValueError):
            pass
    
    # Parse key=value pairs
    for line in text_data.split("\\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            parsed_data[key] = value
        elif line.strip().upper() in ["BUY", "SELL"]:
            parsed_data["action"] = line.strip().capitalize()
    
    # Convert numeric fields
    for target in ["T1", "STOP", "PRICE"]:
        if target in parsed_data:
            try:
                parsed_data[target] = float(parsed_data[target])
            except (ValueError, TypeError):
                pass
    
    return parsed_data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
