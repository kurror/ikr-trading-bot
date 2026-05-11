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
    """Fetch Reddit posts via Tor, fall back to Google News RSS."""
    reddit_url = f'https://www.reddit.com/r/options/search/?q={symbol}&sort=new'

    try:
        r = _tor_get(
            f'https://www.reddit.com/r/options/search.json'
            f'?q={symbol}&sort=new&limit=5&t=week')
        if r:
            posts = r.json()['data']['children']
            titles = [p['data']['title'] for p in posts[:5]]
            text = ' | '.join(titles) if titles else 'No Reddit posts found'
            return text, reddit_url
    except Exception:
        pass

    # Fallback: Google News RSS (always works from OCI)
    try:
        import xml.etree.ElementTree as ET
        url = (f'https://news.google.com/rss/search'
               f'?q={symbol}+stock+options&hl=en-US&gl=US&ceid=US:en')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        r = urllib.request.urlopen(req, timeout=8).read()
        root = ET.fromstring(r)
        titles = [i.find('title').text for i in root.findall('.//item')[:3]]
        text = ' | '.join(titles) if titles else 'No news found'
        return text, reddit_url
    except Exception:
        return 'News unavailable', reddit_url

def groq_analyse(symbol, action, qty, price, context=''):
    prompt = (
        f'You are a concise trading assistant. Analyse this paper trade.\n'
        f'Trade: {action} {qty} {symbol} @ USD {price}\n'
        f'Context: {context or "none"}\n\n'
        f'Reply in exactly 2 lines:\n'
        f'Line 1: one-sentence market analysis\n'
        f'Line 2: APPROVE or REJECT with reason under 10 words'
    )
    data = json.dumps({
        'model': 'llama-3.3-70b-versatile',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 80
    }).encode()
    req = urllib.request.Request(
        'https://api.groq.com/openai/v1/chat/completions', data=data,
        headers={'Authorization': f'Bearer {GROQ_KEY}',
                 'Content-Type': 'application/json',
                 'User-Agent': 'Mozilla/5.0'})
    r = json.loads(urllib.request.urlopen(req).read())
    return r['choices'][0]['message']['content'].strip()

def send_approval_request(symbol, action, qty, price, analysis, reddit_url=''):
    order_id = f'{symbol}_{int(time.time())}'
    message = f'{action} {qty} {symbol} @ USD {price}\n\n{analysis}'
    payload = {
        'topic': NTFY_TOPIC,
        'title': 'Trade Approval Required',
        'message': message,
        'priority': 4,
        'tags': ['chart_with_upwards_trend'],
        'actions': [
            {'action': 'view', 'label': 'Reddit Source',
             'url': reddit_url or f'https://www.reddit.com/r/options/search/?q={symbol}',
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
    print(f'Fetching sentiment for {symbol} (Tor/Reddit with Google News fallback)...')
    context, reddit_url = get_market_sentiment(symbol)
    print(f'Context: {context[:120]}')
    print('Running Groq analysis...')
    analysis = groq_analyse(symbol, action, qty, price, context)
    print(f'Analysis:\n{analysis}')
    order_id = send_approval_request(symbol, action, qty, price, analysis, reddit_url)
    print('Notification sent. Waiting for your approval...')
    decision = wait_for_decision(order_id)
    print(f'Decision: {decision}')
    return decision == 'approved'
