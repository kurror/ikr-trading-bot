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
TARGET_DTE         = 245    # Jan '27 expiry (~245 days out)
SYMBOL             = 'UBER'
DIRECTION          = 'bearish'  # 'bullish' = buy calls, 'bearish' = buy puts


class OptionsStrategy(Strategy):
    parameters = {'symbol': SYMBOL, 'direction': DIRECTION}

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
        try:
            import urllib.request, json
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read())
            return data['chart']['result'][0]['meta']['regularMarketPrice']
        except Exception:
            return None

    def _pick_option(self, underlying, direction):
        """
        Pick an OTM option using yfinance chain.
        bullish  → buy call, first strike >= spot
        bearish  → buy put,  first strike <= spot (nearest OTM below)
        Returns (expiry, strike, spot, premium, right)
        """
        spot = self._spot_price(underlying.symbol)
        if not spot:
            self.log_message('Cannot get spot price from IB or Yahoo Finance.')
            return None, None, None, None, None

        try:
            import yfinance as yf
            ticker = yf.Ticker(underlying.symbol)
            expiry_dates = ticker.options
        except Exception as e:
            self.log_message(f'Cannot fetch option expiries from yfinance: {e}')
            return None, None, None, None, None

        if not expiry_dates:
            self.log_message('No option expiries returned.')
            return None, None, None, None, None

        target = (date.today() + timedelta(days=TARGET_DTE)).isoformat()
        future = [e for e in expiry_dates if e >= target]
        if not future:
            self.log_message('No expiry found beyond target DTE.')
            return None, None, None, None, None
        expiry_str = future[0]
        expiry = date.fromisoformat(expiry_str)

        try:
            chain = ticker.option_chain(expiry_str)
        except Exception as e:
            self.log_message(f'Cannot fetch option chain for {expiry_str}: {e}')
            return None, None, None, None, None

        if direction == 'bearish':
            right = 'PUT'
            otm = chain.puts[chain.puts['strike'] <= spot].sort_values('strike', ascending=False)
        else:
            right = 'CALL'
            otm = chain.calls[chain.calls['strike'] >= spot].sort_values('strike')

        if otm.empty:
            self.log_message(f'No OTM {right}s available.')
            return None, None, None, None, None

        row = otm.iloc[0]
        strike = float(row['strike'])
        bid, ask = float(row.get('bid', 0)), float(row.get('ask', 0))
        premium = round((bid + ask) / 2 if bid and ask else float(row['lastPrice']), 2)
        return expiry, strike, spot, premium, right

    def on_trading_iteration(self):
        symbol    = self.parameters['symbol']
        direction = self.parameters['direction']
        underlying = Asset(symbol=symbol, asset_type='stock')

        expiry, strike, spot, premium, right = self._pick_option(underlying, direction)
        if not expiry:
            return

        option = Asset(
            symbol=symbol,
            asset_type='option',
            expiration=expiry,
            strike=strike,
            right=right,
        )

        existing = self.get_position(option)
        if existing:
            self.log_message(f'Already holding {option}. Skipping.')
            return

        cost = round(premium * 100, 2)

        portfolio_value = self.get_portfolio_value()
        cash = self.get_cash()
        invested = portfolio_value - cash
        if invested + cost > MAX_PREMIUM_BUDGET:
            self.log_message(
                f'Budget cap reached (invested ${invested:.0f}, '
                f'cap ${MAX_PREMIUM_BUDGET}). Skipping.')
            return

        dte = (expiry - date.today()).days
        action = f'BUY {right}'
        desc = (f'{right}  strike ${strike}  expiry {expiry} ({dte}d)  '
                f'premium ~${cost:.0f}  spot ${spot:.2f}')
        self.log_message(f'Candidate: {desc}')

        approved = request_approval(
            symbol=symbol,
            action=action,
            qty=1,
            price=cost,
        )

        if approved:
            order = self.create_order(option, 1, 'buy')
            self.submit_order(order)
            self.log_message(f'Order submitted: {desc}')
        else:
            self.log_message('Order rejected or timed out.')


broker = InteractiveBrokers(INTERACTIVE_BROKERS_CONFIG)
strategy = OptionsStrategy(broker=broker)
trader = Trader()
trader.add_strategy(strategy)
trader.run_all()
