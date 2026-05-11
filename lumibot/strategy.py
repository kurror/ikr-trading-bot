import sys
sys.path.insert(0, '/home/ubuntu/trading-bot')
from trade_notifier import request_approval

from lumibot.brokers import InteractiveBrokers
from lumibot.strategies import Strategy
from lumibot.traders import Trader
from lumibot.entities import Asset
from datetime import date, timedelta

INTERACTIVE_BROKERS_CONFIG = {
    'IP': '127.0.0.1',
    'SOCKET_PORT': 4004,
    'CLIENT_ID': '10',
}

MAX_PREMIUM_BUDGET = 2000   # max total USD spent on open option positions
TARGET_DTE         = 30     # aim for ~30 days to expiration
SYMBOL             = 'AAPL'


class LongCallStrategy(Strategy):
    parameters = {'symbol': SYMBOL}

    def initialize(self):
        self.sleeptime = '1D'

    def _spot_price(self, symbol):
        """Get spot price from IB (delayed ok), fall back to Yahoo Finance."""
        try:
            self.broker.ib.reqMarketDataType(3)  # 3 = delayed
        except Exception:
            pass
        price = self.get_last_price(Asset(symbol=symbol, asset_type='stock'))
        if price and price == price:  # not None, not NaN
            return price
        # Fallback: Yahoo Finance (no subscription needed)
        try:
            import urllib.request, json
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read())
            return data['chart']['result'][0]['meta']['regularMarketPrice']
        except Exception:
            return None

    def _pick_call(self, underlying):
        """Find a slightly OTM call with ~TARGET_DTE days to expiry using yfinance chain."""
        spot = self._spot_price(underlying.symbol)
        if not spot:
            self.log_message('Cannot get spot price from IB or Yahoo Finance.')
            return None, None, None, None

        try:
            import yfinance as yf
            ticker = yf.Ticker(underlying.symbol)
            expiry_dates = ticker.options  # sorted list of 'YYYY-MM-DD' strings
        except Exception as e:
            self.log_message(f'Cannot fetch option expiries from yfinance: {e}')
            return None, None, None, None

        if not expiry_dates:
            self.log_message('No option expiries returned.')
            return None, None, None, None

        target = (date.today() + timedelta(days=TARGET_DTE)).isoformat()
        future = [e for e in expiry_dates if e >= target]
        if not future:
            self.log_message('No expiry found beyond target DTE.')
            return None, None, None, None
        expiry_str = future[0]
        expiry = date.fromisoformat(expiry_str)

        try:
            chain = ticker.option_chain(expiry_str)
            calls = chain.calls
        except Exception as e:
            self.log_message(f'Cannot fetch call chain for {expiry_str}: {e}')
            return None, None, None, None

        otm_calls = calls[calls['strike'] >= spot].sort_values('strike')
        if otm_calls.empty:
            self.log_message('No OTM calls available.')
            return None, None, None, None

        row = otm_calls.iloc[0]
        strike = float(row['strike'])
        # Use mid-price if bid/ask available, else lastPrice
        bid, ask = float(row.get('bid', 0)), float(row.get('ask', 0))
        premium = round((bid + ask) / 2 if bid and ask else float(row['lastPrice']), 2)
        return expiry, strike, spot, premium

    def on_trading_iteration(self):
        underlying = Asset(symbol=self.parameters['symbol'], asset_type='stock')

        expiry, strike, spot, premium = self._pick_call(underlying)
        if not expiry:
            return

        call = Asset(
            symbol=self.parameters['symbol'],
            asset_type='option',
            expiration=expiry,
            strike=strike,
            right='CALL',
        )

        # Check existing open call positions — never add to losers, avoid duplicates
        existing = self.get_position(call)
        if existing:
            self.log_message(f'Already holding {call}. Skipping.')
            return

        cost = round(premium * 100, 2)

        # Budget check: total open option value vs cap
        portfolio_value = self.get_portfolio_value()
        cash = self.get_cash()
        invested = portfolio_value - cash
        if invested + cost > MAX_PREMIUM_BUDGET:
            self.log_message(
                f'Budget cap reached (invested ${invested:.0f}, '
                f'cap ${MAX_PREMIUM_BUDGET}). Skipping.')
            return

        dte = (expiry - date.today()).days
        desc = (f'CALL  strike ${strike}  expiry {expiry} ({dte}d)  '
                f'premium ~${cost:.0f}  spot ${spot:.2f}')
        self.log_message(f'Candidate: {desc}')

        approved = request_approval(
            symbol=self.parameters['symbol'],
            action='BUY CALL',
            qty=1,
            price=cost,
        )

        if approved:
            order = self.create_order(call, 1, 'buy')
            self.submit_order(order)
            self.log_message(f'Order submitted: {desc}')
        else:
            self.log_message('Order rejected or timed out.')


broker = InteractiveBrokers(INTERACTIVE_BROKERS_CONFIG)
strategy = LongCallStrategy(broker=broker)
trader = Trader()
trader.add_strategy(strategy)
trader.run_all()
