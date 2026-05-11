from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse
import json, time, os

app = FastAPI()
DECISIONS_FILE = '/tmp/trade_decisions.json'
REPORTS_FILE   = '/tmp/trade_reports.json'

def load(path):
    if os.path.exists(path):
        return json.load(open(path))
    return {}

def save(path, data):
    json.dump(data, open(path, 'w'))

@app.post('/approve/{order_id}')
async def approve(order_id: str):
    data = load(DECISIONS_FILE)
    if order_id in data:
        return JSONResponse({'status': 'already_decided', 'decision': data[order_id]['decision']})
    data[order_id] = {'decision': 'approved', 'ts': time.time()}
    save(DECISIONS_FILE, data)
    return JSONResponse({'status': 'approved', 'order': order_id})

@app.post('/reject/{order_id}')
async def reject(order_id: str):
    data = load(DECISIONS_FILE)
    if order_id in data:
        return JSONResponse({'status': 'already_decided', 'decision': data[order_id]['decision']})
    data[order_id] = {'decision': 'rejected', 'ts': time.time()}
    save(DECISIONS_FILE, data)
    return JSONResponse({'status': 'rejected', 'order': order_id})

@app.get('/decision/{order_id}')
async def decision(order_id: str):
    data = load(DECISIONS_FILE)
    if order_id in data:
        return JSONResponse(data[order_id])
    return JSONResponse({'decision': 'pending'})

@app.delete('/decision/{order_id}')
async def clear(order_id: str):
    data = load(DECISIONS_FILE)
    data.pop(order_id, None)
    save(DECISIONS_FILE, data)
    return JSONResponse({'status': 'cleared'})

@app.post('/report/{order_id}')
async def store_report(order_id: str, request_body: dict):
    reports = load(REPORTS_FILE)
    reports[order_id] = request_body
    save(REPORTS_FILE, reports)
    return JSONResponse({'status': 'stored'})

@app.get('/report/{order_id}', response_class=HTMLResponse)
async def view_report(order_id: str):
    reports = load(REPORTS_FILE)
    if order_id not in reports:
        return HTMLResponse('<h2>Report not found</h2>', status_code=404)
    r = reports[order_id]

    def _source_to_html(s):
        if ' — http' in s:
            label, url = s.rsplit(' — ', 1)
            return f'<li><a href="{url.strip()}" target="_blank">{label.strip()}</a></li>'
        return f'<li>{s}</li>'

    sources_html = ''.join(_source_to_html(s) for s in r.get('sources', []))

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trade Report — {r.get('symbol')}</title>
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
<span class="badge buy">{r.get('action')} {r.get('qty')} {r.get('symbol')} @ USD {r.get('price')}</span>
<div class="meta">Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r.get('ts', time.time())))}</div>

<h2>Groq Analysis</h2>
<div class="analysis">{r.get('analysis', '')}</div>

<h2>Sources Used</h2>
<ul>{sources_html}</ul>
</body>
</html>""")
