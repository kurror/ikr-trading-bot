import time
import streamlit as st
import pandas as pd
import requests

try:
    from trading_bot import db
except ImportError:
    import db

WEBHOOK = 'http://localhost:8080'

st.set_page_config(page_title='IKR Trading Dashboard', page_icon='📈', layout='wide')
st.title('IKR Trading Dashboard')

trades = db.get_all_trades()
pending  = [t for t in trades if t['decision'] == 'pending' and not t['consumed']]
approved = [t for t in trades if t['decision'] == 'approved']
rejected = [t for t in trades if t['decision'] == 'rejected']

# --- Metrics row ---
c1, c2, c3, c4 = st.columns(4)
c1.metric('Total Trades', len(trades))
c2.metric('Pending Approval', len(pending))
c3.metric('Approved', len(approved))
c4.metric('Rejected', len(rejected))

st.divider()

tab_pending, tab_history, tab_strategy = st.tabs(['Pending Approvals', 'Trade History', 'Strategy'])

# --- Pending approvals ---
with tab_pending:
    if st.button('Refresh', key='refresh_pending'):
        st.rerun()

    if not pending:
        st.info('No pending approvals.')
    else:
        for t in pending:
            header = (f"{t.get('symbol', '?')} — {t.get('action', '?')} "
                      f"{t.get('qty', '?')} contracts @ ${t.get('price', '?')}")
            with st.expander(header, expanded=True):
                st.code(t.get('analysis', 'No analysis available.'), language=None)

                if t.get('sources'):
                    st.markdown('**Sources**')
                    for s in t['sources']:
                        if ' — http' in s:
                            label, url = s.rsplit(' — ', 1)
                            st.markdown(f'- [{label.strip()}]({url.strip()})')
                        else:
                            st.markdown(f'- {s}')

                col_a, col_b, _ = st.columns([1, 1, 4])
                if col_a.button('✅ Approve', key=f'ap_{t["order_id"]}'):
                    requests.post(f'{WEBHOOK}/approve/{t["order_id"]}', timeout=5)
                    st.rerun()
                if col_b.button('❌ Reject', key=f're_{t["order_id"]}'):
                    requests.post(f'{WEBHOOK}/reject/{t["order_id"]}', timeout=5)
                    st.rerun()

# --- Trade history ---
with tab_history:
    if not trades:
        st.info('No trades recorded yet.')
    else:
        rows = []
        for t in trades:
            rows.append({
                'Date':     time.strftime('%Y-%m-%d %H:%M', time.gmtime(t.get('ts_created') or 0)),
                'Symbol':   t.get('symbol', ''),
                'Action':   t.get('action', ''),
                'Qty':      t.get('qty', ''),
                'Premium':  f"${t.get('price', 0):,.2f}",
                'Decision': t.get('decision', 'pending').upper(),
                'Mode':     t.get('mode', 'paper').upper(),
            })
        df = pd.DataFrame(rows)

        decision_filter = st.multiselect(
            'Filter by decision',
            ['PENDING', 'APPROVED', 'REJECTED'],
            default=['PENDING', 'APPROVED', 'REJECTED'],
        )
        df = df[df['Decision'].isin(decision_filter)]
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Approval rate
        total_decided = len(approved) + len(rejected)
        if total_decided:
            rate = len(approved) / total_decided * 100
            st.caption(f'Approval rate: {rate:.0f}% ({len(approved)} approved / {len(rejected)} rejected)')

# --- Strategy config ---
with tab_strategy:
    st.subheader('Active Strategy')
    st.json({
        'symbol':     'UBER',
        'direction':  'bearish',
        'instrument': 'PUT options',
        'target_dte': 245,
        'max_premium_budget_usd': 2000,
        'mode':       'paper',
        'broker':     'Interactive Brokers',
        'data_source': 'yfinance (option chain) + IB delayed (spot)',
        'llm':        'Groq llama-3.3-70b-versatile',
        'sentiment_sources': ['WSB DD (Tor)', 'r/options (Tor)', 'Google News RSS'],
    })
    st.caption('Edit `lumibot/strategy.py` to change symbol, direction, or DTE. Rebuild Docker after changes.')

st.caption(f'Last loaded: {time.strftime("%H:%M:%S")}')
