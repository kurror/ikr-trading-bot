from fastapi.testclient import TestClient
import trading_bot.webhook as wh

client = TestClient(wh.app)

REPORT_PAYLOAD = {
    'symbol': 'AAPL',
    'action': 'BUY CALL',
    'qty': 1,
    'price': 710.0,
    'analysis': (
        'SENTIMENT: Bullish on tech sector.\n'
        'RISK: Upcoming earnings volatility.\n'
        'CATALYST: AI demand driving growth.\n'
        'VERDICT: APPROVE strong momentum.'
    ),
    'sources': [
        'WSB DD: AAPL deep dive — https://reddit.com/r/wallstreetbets/comments/abc/',
        'r/options: AAPL calls — https://reddit.com/r/options/comments/xyz/',
    ],
    'ts': 1778500000,
}


# --- Decision: approve / reject ---

def test_approve_new_order():
    r = client.post('/approve/order1')
    assert r.status_code == 200
    assert r.json() == {'status': 'approved', 'order': 'order1'}


def test_reject_new_order():
    r = client.post('/reject/order1')
    assert r.status_code == 200
    assert r.json() == {'status': 'rejected', 'order': 'order1'}


def test_first_decision_wins_approve_then_reject():
    client.post('/approve/order2')
    r = client.post('/reject/order2')
    assert r.status_code == 200
    assert r.json()['status'] == 'already_decided'
    assert r.json()['decision'] == 'approved'


def test_first_decision_wins_reject_then_approve():
    client.post('/reject/order3')
    r = client.post('/approve/order3')
    assert r.status_code == 200
    assert r.json()['status'] == 'already_decided'
    assert r.json()['decision'] == 'rejected'


def test_duplicate_tap_does_not_overwrite():
    client.post('/approve/order4')
    client.post('/reject/order4')  # ignored
    r = client.get('/decision/order4')
    assert r.json()['decision'] == 'approved'


# --- Decision polling ---

def test_pending_before_any_decision():
    r = client.get('/decision/unknown_order')
    assert r.json() == {'decision': 'pending'}


def test_poll_returns_approved():
    client.post('/approve/order5')
    r = client.get('/decision/order5')
    assert r.json()['decision'] == 'approved'


def test_poll_returns_rejected():
    client.post('/reject/order6')
    r = client.get('/decision/order6')
    assert r.json()['decision'] == 'rejected'


def test_clear_decision():
    client.post('/approve/order7')
    client.delete('/decision/order7')
    r = client.get('/decision/order7')
    assert r.json() == {'decision': 'pending'}


# --- Report storage and rendering ---

def test_store_report():
    r = client.post('/report/rep1', json=REPORT_PAYLOAD)
    assert r.status_code == 200
    assert r.json() == {'status': 'stored'}


def test_view_report_returns_html():
    client.post('/report/rep2', json=REPORT_PAYLOAD)
    r = client.get('/report/rep2')
    assert r.status_code == 200
    assert 'text/html' in r.headers['content-type']


def test_report_contains_symbol_and_verdict():
    client.post('/report/rep3', json=REPORT_PAYLOAD)
    html = client.get('/report/rep3').text
    assert 'AAPL' in html
    assert 'VERDICT' in html
    assert 'APPROVE' in html


def test_report_sources_are_clickable_links():
    client.post('/report/rep4', json=REPORT_PAYLOAD)
    html = client.get('/report/rep4').text
    assert '<a href="https://reddit.com/r/wallstreetbets/comments/abc/"' in html
    assert '<a href="https://reddit.com/r/options/comments/xyz/"' in html


def test_report_not_found_returns_404():
    r = client.get('/report/does_not_exist')
    assert r.status_code == 404
