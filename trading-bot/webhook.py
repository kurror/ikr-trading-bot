from fastapi import FastAPI
from fastapi.responses import JSONResponse
import json, time, os

app = FastAPI()
DECISIONS_FILE = '/tmp/trade_decisions.json'

def load():
    if os.path.exists(DECISIONS_FILE):
        return json.load(open(DECISIONS_FILE))
    return {}

def save(data):
    json.dump(data, open(DECISIONS_FILE, 'w'))

@app.post('/approve/{order_id}')
async def approve(order_id: str):
    data = load()
    if order_id in data:
        return JSONResponse({'status': 'already_decided', 'decision': data[order_id]['decision']}, status_code=409)
    data[order_id] = {'decision': 'approved', 'ts': time.time()}
    save(data)
    return JSONResponse({'status': 'approved', 'order': order_id})

@app.post('/reject/{order_id}')
async def reject(order_id: str):
    data = load()
    if order_id in data:
        return JSONResponse({'status': 'already_decided', 'decision': data[order_id]['decision']}, status_code=409)
    data[order_id] = {'decision': 'rejected', 'ts': time.time()}
    save(data)
    return JSONResponse({'status': 'rejected', 'order': order_id})

@app.get('/decision/{order_id}')
async def decision(order_id: str):
    data = load()
    if order_id in data:
        return JSONResponse(data[order_id])
    return JSONResponse({'decision': 'pending'})

@app.delete('/decision/{order_id}')
async def clear(order_id: str):
    data = load()
    data.pop(order_id, None)
    save(data)
    return JSONResponse({'status': 'cleared'})
