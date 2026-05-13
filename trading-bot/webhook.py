from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse
import time

try:
    from trading_bot import db
except ImportError:
    import db

app = FastAPI()


@app.post('/approve/{order_id}')
async def approve(order_id: str):
    if not db.set_decision(order_id, 'approved'):
        trade = db.get_trade(order_id)
        return JSONResponse({'status': 'already_decided',
                             'decision': trade['decision'] if trade else 'unknown'})
    return JSONResponse({'status': 'approved', 'order': order_id})


@app.post('/reject/{order_id}')
async def reject(order_id: str):
    if not db.set_decision(order_id, 'rejected'):
        trade = db.get_trade(order_id)
        return JSONResponse({'status': 'already_decided',
                             'decision': trade['decision'] if trade else 'unknown'})
    return JSONResponse({'status': 'rejected', 'order': order_id})


@app.get('/decision/{order_id}')
async def decision(order_id: str):
    status = db.get_decision_status(order_id)
    if not status or status['decision'] == 'pending':
        return JSONResponse({'decision': 'pending'})
    return JSONResponse(status)


@app.delete('/decision/{order_id}')
async def clear(order_id: str):
    db.consume_decision(order_id)
    return JSONResponse({'status': 'cleared'})


@app.post('/report/{order_id}')
async def store_report(order_id: str, request_body: dict):
    db.upsert_report(
        order_id,
        symbol=request_body.get('symbol', ''),
        action=request_body.get('action', ''),
        qty=request_body.get('qty', 0),
        price=request_body.get('price', 0),
        analysis=request_body.get('analysis', ''),
        sources=request_body.get('sources', []),
        mode=request_body.get('mode', 'paper'),
    )
    return JSONResponse({'status': 'stored'})


@app.get('/report/{order_id}', response_class=HTMLResponse)
async def view_report(order_id: str):
    trade = db.get_trade(order_id)
    if not trade:
        return HTMLResponse('<h2>Report not found</h2>', status_code=404)

    def _source_to_html(s):
        if ' — http' in s:
            label, url = s.rsplit(' — ', 1)
            return f'<li><a href="{url.strip()}" target="_blank">{label.strip()}</a></li>'
        return f'<li>{s}</li>'

    sources_html = ''.join(_source_to_html(s) for s in trade.get('sources', []))
    ts = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(trade.get('ts_created') or time.time()))

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trade Report — {trade.get('symbol')}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; background: #0f0f0f; color: #e0e0e0; }}
  h1 {{ font-size: 1.4rem; color: #fff; }}
  .badge {{ display: inline-block; padding: .25rem .6rem; border-radius: 4px; font-size: .85rem; font-weight: bold; margin-bottom: 1rem; }}
  .buy {{ background: #1a3a1a; color: #4caf50; }}
  .meta {{ color: #888; font-size: .9rem; margin-bottom: 1.5rem; }}
  h2 {{ font-size: 1rem; color: #aaa; text-transform: uppercase; letter-spacing: .05em; margin-top: 1.5rem; }}
  .analysis {{ background: #1a1a1a; border-left: 3px solid #4caf50; padding: 1rem; border-radius: 4px; white-space: pre-wrap; line-height: 1.6; }}
  ul {{ background: #1a1a1a; padding: 1rem 1rem 1rem 2rem; border-radius: 4px; }}
  li {{ margin-bottom: .4rem; font-size: .9rem; color: #ccc; }}
  a {{ color: #4caf50; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Trade Report</h1>
<span class="badge buy">{trade.get('action')} {trade.get('qty')} {trade.get('symbol')} @ USD {trade.get('price')}</span>
<div class="meta">Generated {ts}</div>

<h2>Groq Analysis</h2>
<div class="analysis">{trade.get('analysis', '')}</div>

<h2>Sources Used</h2>
<ul>{sources_html}</ul>
</body>
</html>""")
