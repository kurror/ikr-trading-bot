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

    def _pick_call(self, underlying):
        """Find a slightly OTM call with ~TARGET_DTE days to expiry."""
        spot = self.get_last_price(underlying)
        if not spot:
            self.log_message('Cannot get spot price.')
            return None, None, None

        target_date = date.today() + timedelta(days=TARGET_DTE)
        expiry = self.get_option_expiration_after_date(target_date)
        if not expiry:
            self.log_message('No expiry found.')
            return None, None, None

        strikes = self.get_strikes(underlying)
        if not strikes:
            self.log_message('No strikes returned.')
            return None, None, None

        # Pick the first strike >= spot (ATM or slightly OTM)
        otm_strikes = sorted(s for s in strikes if s >= spot)
        if not otm_strikes:
            self.log_message('No OTM strikes available.')
            return None, None, None

        strike = otm_strikes[0]
        return expiry, strike, spot

    def on_trading_iteration(self):
        underlying = Asset(symbol=self.parameters['symbol'], asset_type='stock')

        expiry, strike, spot = self._pick_call(underlying)
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

        # Estimate cost: use last price × 100 (one contract)
        premium = self.get_last_price(call)
        if not premium:
            self.log_message('Cannot price the call contract. Skipping.')
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
