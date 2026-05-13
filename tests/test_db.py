import trading_bot.db as db

# conftest.py autouse fixture gives each test a fresh isolated DB


def test_upsert_and_get_report():
    db.upsert_report('o1', 'UBER', 'BUY PUT', 1, 500.0, 'analysis text', ['src1'], 'paper')
    t = db.get_trade('o1')
    assert t['symbol'] == 'UBER'
    assert t['action'] == 'BUY PUT'
    assert t['price'] == 500.0
    assert t['sources'] == ['src1']
    assert t['decision'] == 'pending'
    assert t['mode'] == 'paper'


def test_upsert_overwrites_report_fields():
    db.upsert_report('o2', 'UBER', 'BUY PUT', 1, 500.0, 'old analysis', [], 'paper')
    db.upsert_report('o2', 'UBER', 'BUY PUT', 1, 500.0, 'new analysis', ['src'], 'paper')
    t = db.get_trade('o2')
    assert t['analysis'] == 'new analysis'


def test_set_decision_approved():
    db.upsert_report('o3', 'UBER', 'BUY PUT', 1, 500.0, '', [], 'paper')
    result = db.set_decision('o3', 'approved')
    assert result is True
    assert db.get_trade('o3')['decision'] == 'approved'


def test_set_decision_first_wins():
    db.upsert_report('o4', 'UBER', 'BUY PUT', 1, 500.0, '', [], 'paper')
    db.set_decision('o4', 'approved')
    result = db.set_decision('o4', 'rejected')
    assert result is False
    assert db.get_trade('o4')['decision'] == 'approved'


def test_set_decision_without_prior_report():
    result = db.set_decision('o5', 'rejected')
    assert result is True
    assert db.get_trade('o5')['decision'] == 'rejected'


def test_get_decision_status_unknown_order():
    assert db.get_decision_status('no_such_order') is None


def test_get_decision_status_pending():
    db.upsert_report('o6', 'UBER', 'BUY PUT', 1, 500.0, '', [], 'paper')
    status = db.get_decision_status('o6')
    assert status['decision'] == 'pending'


def test_get_decision_status_approved():
    db.upsert_report('o7', 'UBER', 'BUY PUT', 1, 500.0, '', [], 'paper')
    db.set_decision('o7', 'approved')
    status = db.get_decision_status('o7')
    assert status['decision'] == 'approved'


def test_consume_decision_hides_result():
    db.upsert_report('o8', 'UBER', 'BUY PUT', 1, 500.0, '', [], 'paper')
    db.set_decision('o8', 'approved')
    db.consume_decision('o8')
    status = db.get_decision_status('o8')
    assert status['decision'] == 'pending'


def test_consume_preserves_history():
    db.upsert_report('o9', 'UBER', 'BUY PUT', 1, 500.0, '', [], 'paper')
    db.set_decision('o9', 'approved')
    db.consume_decision('o9')
    trade = db.get_trade('o9')
    assert trade['decision'] == 'approved'
    assert trade['consumed'] == 1


def test_get_all_trades_order():
    db.upsert_report('a1', 'UBER', 'BUY PUT', 1, 100.0, '', [], 'paper')
    db.upsert_report('a2', 'AAPL', 'BUY CALL', 1, 200.0, '', [], 'paper')
    trades = db.get_all_trades()
    assert len(trades) == 2
    assert trades[0]['order_id'] == 'a2'  # newest first


def test_get_trade_not_found():
    assert db.get_trade('nonexistent') is None
