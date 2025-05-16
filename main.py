from flask import Flask, request, jsonify
from tradovate_client import TradovateClient

app = Flask(__name__)

client = TradovateClient()
client.authenticate()  # Authenticate once on startup

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Received webhook data:", data)

    if not data or 'symbol' not in data or 'side' not in data or 'entry' not in data:
        return jsonify({"error": "Missing required fields"}), 400

    symbol = data['symbol']
    side = data['side'].upper()
    entry = float(data['entry'])
    stop = float(data['stop'])
    targets = [float(data.get(f't{i}')) for i in range(1, 4) if data.get(f't{i}')]

    qty = int(data.get('qty', 1))
    long_trade = side == "BUY"

    # Calculate individual quantities for T1, T2, T3
    tp_qtys = [qty // 3] * 3
    remainder = qty % 3
    for i in range(remainder):
        tp_qtys[i] += 1

    order_plan = []

    # Entry order (Stop)
    order_plan.append({
        "label": "ENTRY",
        "orderType": "Stop",
        "action": "Buy" if long_trade else "Sell",
        "stopPrice": entry,
        "qty": qty
    })

    # Take Profits (Limit orders)
    for i, target in enumerate(targets):
        order_plan.append({
            "label": f"T{i+1}",
            "orderType": "Limit",
            "action": "Sell" if long_trade else "Buy",
            "price": target,
            "qty": tp_qtys[i]
        })

    # Stop Loss (Stop order)
    order_plan.append({
        "label": "STOP",
        "orderType": "Stop",
        "action": "Sell" if long_trade else "Buy",
        "stopPrice": stop,
        "qty": qty
    })

    responses = []

    for order in order_plan:
        order_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": order["action"],
            "orderQty": order["qty"],
            "orderType": order["orderType"],
            "timeInForce": "GTC",
            "isAutomated": True
        }

        # Add correct price field
        if order["orderType"] == "Limit":
            order_payload["price"] = order["price"]
        elif order["orderType"] == "Stop":
            order_payload["stopPrice"] = order["stopPrice"]

        print(f"Placing {order['label']} order: {order_payload}")
        response = client.place_order(symbol, order["action"], order["qty"], order_payload)
        responses.append({order["label"]: response})

    return jsonify({"status": "orders placed", "details": responses}), 200

if __name__ == '__main__':
    app.run(port=5000)
