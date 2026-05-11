import urllib.request, json, time, os, base64

GROQ_KEY    = os.environ['GROQ_API_KEY']
NTFY_SERVER = 'http://158.180.57.245:7777'
NTFY_TOPIC  = 'trading-alerts'
NTFY_B64    = base64.b64encode(b'trading:Ntfy@IKR2026').decode()
WEBHOOK_URL = 'http://158.180.57.245:8080'
TIMEOUT_SEC = 300
TOR_PROXY   = 'socks5h://127.0.0.1:9050'
TOR_COOKIE  = '/var/run/tor/control.authcookie'

def _new_tor_circuit():
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as c:
            c.authenticate(cookie_file=TOR_COOKIE)
            c.signal(Signal.NEWNYM)
        time.sleep(2)
    except Exception:
        pass

def _tor_get(url, retries=2):
    import requests
    for attempt in range(retries):
        s = requests.Session()
        s.proxies = {'http': TOR_PROXY, 'https': TOR_PROXY}
        s.headers['User-Agent'] = (
            'Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0')
        r = s.get(url, timeout=20)
        if r.status_code == 200:
            return r
        if r.status_code == 429 and attempt < retries - 1:
            _new_tor_circuit()
    return None

def get_market_sentiment(symbol):
    """
    Pull signal from three sources via Tor:
      1. r/wallstreetbets DD posts
      2. r/options recent posts
      3. Google News RSS fallback
    Returns (sources_list, context_text, reddit_url).
    """
    reddit_url = f'https://www.reddit.com/r/wallstreetbets/search/?q={symbol}&sort=new'
    sources = []
    items = []

    # WSB DD posts
    try:
        r = _tor_get(
            f'https://www.reddit.com/r/wallstreetbets/search.json'
            f'?q={symbol}+flair%3ADD&restrict_sr=1&sort=new&t=month&limit=3')
        if r:
            for p in r.json()['data']['children']:
                d = p['data']
                items.append(f'[WSB DD] {d["title"]}')
                sources.append(f'WSB DD: {d["title"][:80]} — https://reddit.com{d["permalink"]}')
    except Exception:
        pass

    # r/options recent
    try:
        r = _tor_get(
            f'https://www.reddit.com/r/options/search.json'
            f'?q={symbol}&restrict_sr=1&sort=new&t=week&limit=4')
        if r:
            for p in r.json()['data']['children']:
                d = p['data']
                items.append(f'[r/options] {d["title"]}')
                sources.append(f'r/options: {d["title"][:80]} — https://reddit.com{d["permalink"]}')
    except Exception:
        pass

    # WSB general search
    try:
        r = _tor_get(
            f'https://www.reddit.com/r/wallstreetbets/search.json'
            f'?q={symbol}&restrict_sr=1&sort=new&t=week&limit=3')
        if r:
            for p in r.json()['data']['children']:
                d = p['data']
                title = f'[WSB] {d["title"]}'
                if title not in items:
                    items.append(title)
                    sources.append(f'WSB: {d["title"][:80]} — https://reddit.com{d["permalink"]}')
    except Exception:
        pass

    if items:
        return sources, ' | '.join(items), reddit_url

    # Fallback: Google News RSS
    try:
        import xml.etree.ElementTree as ET
        url = (f'https://news.google.com/rss/search'
               f'?q={symbol}+stock+options&hl=en-US&gl=US&ceid=US:en')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        r = urllib.request.urlopen(req, timeout=8).read()
        root = ET.fromstring(r)
        news_items = root.findall('.//item')[:4]
        titles = [i.find('title').text for i in news_items]
        links  = [i.find('link').text  for i in news_items]
        sources = [f'Google News: {t[:80]} — {l}' for t, l in zip(titles, links)]
        text = ' | '.join(titles)
        return sources, text, reddit_url
    except Exception:
        return ['No sources available'], 'News unavailable', reddit_url

def groq_analyse(symbol, action, qty, price, context=''):
    prompt = (
        f'You are a trading analyst reviewing a paper options trade.\n'
        f'Trade: {action} {qty} {symbol} call option @ USD {price} total premium\n\n'
        f'Community sentiment and news:\n{context or "No context available"}\n\n'
        f'Write a structured analysis with these sections:\n'
        f'SENTIMENT: one sentence on overall market mood\n'
        f'RISK: one sentence on key risk factors\n'
        f'CATALYST: one sentence on what could move the stock\n'
        f'VERDICT: APPROVE or REJECT with a reason under 12 words\n\n'
        f'Be direct and concise. No bullet points, just the four labelled lines.'
    )
    data = json.dumps({
        'model': 'llama-3.3-70b-versatile',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 200
    }).encode()
    req = urllib.request.Request(
        'https://api.groq.com/openai/v1/chat/completions', data=data,
        headers={'Authorization': f'Bearer {GROQ_KEY}',
                 'Content-Type': 'application/json',
                 'User-Agent': 'Mozilla/5.0'})
    r = json.loads(urllib.request.urlopen(req).read())
    return r['choices'][0]['message']['content'].strip()

def _post_report(order_id, symbol, action, qty, price, analysis, sources):
    payload = json.dumps({
        'symbol': symbol, 'action': action, 'qty': qty, 'price': price,
        'analysis': analysis, 'sources': sources, 'ts': time.time()
    }).encode()
    req = urllib.request.Request(
        f'{WEBHOOK_URL}/report/{order_id}', data=payload,
        headers={'Content-Type': 'application/json'})
    req.get_method = lambda: 'POST'
    urllib.request.urlopen(req)

def send_approval_request(symbol, action, qty, price, analysis, sources, reddit_url=''):
    order_id = f'{symbol}_{int(time.time())}'

    # Store full report on webhook server
    _post_report(order_id, symbol, action, qty, price, analysis, sources)

    # Extract verdict line for notification body
    verdict_line = next(
        (l for l in analysis.splitlines() if l.startswith('VERDICT:')),
        analysis.splitlines()[-1] if analysis else '')
    message = f'{action} {qty} {symbol} @ USD {price}\n\n{verdict_line}'

    payload = {
        'topic': NTFY_TOPIC,
        'title': 'Trade Approval Required',
        'message': message,
        'priority': 4,
        'tags': ['chart_with_upwards_trend'],
        'actions': [
            {'action': 'view', 'label': 'View Report',
             'url': f'{WEBHOOK_URL}/report/{order_id}',
             'clear': False},
            {'action': 'http', 'label': 'Approve',
             'url': f'{WEBHOOK_URL}/approve/{order_id}',
             'method': 'POST', 'clear': True},
            {'action': 'http', 'label': 'Reject',
             'url': f'{WEBHOOK_URL}/reject/{order_id}',
             'method': 'POST', 'clear': True}
        ]
    }
    req = urllib.request.Request(
        NTFY_SERVER, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Basic {NTFY_B64}'})
    urllib.request.urlopen(req)
    return order_id

def wait_for_decision(order_id, timeout=TIMEOUT_SEC):
    print(f'Waiting for decision on {order_id}...')
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = urllib.request.urlopen(
                f'{WEBHOOK_URL}/decision/{order_id}', timeout=3)
            d = json.loads(r.read())
            if d['decision'] in ('approved', 'rejected'):
                urllib.request.urlopen(urllib.request.Request(
                    f'{WEBHOOK_URL}/decision/{order_id}', method='DELETE'))
                return d['decision']
        except Exception:
            pass
        time.sleep(3)
    return 'timeout'

def request_approval(symbol, action, qty, price):
    print(f'Fetching sentiment for {symbol} (WSB DD + r/options + Google News fallback)...')
    sources, context, reddit_url = get_market_sentiment(symbol)
    print(f'Sources found: {len(sources)}')
    print('Running Groq analysis...')
    analysis = groq_analyse(symbol, action, qty, price, context)
    print(f'Analysis:\n{analysis}')
    order_id = send_approval_request(symbol, action, qty, price, analysis, sources, reddit_url)
    print('Notification sent. Waiting for your approval...')
    decision = wait_for_decision(order_id)
    print(f'Decision: {decision}')
    return decision == 'approved'
