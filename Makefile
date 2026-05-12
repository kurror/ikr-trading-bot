.PHONY: install test lint clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest

lint:
	.venv/bin/python3 -m py_compile trading-bot/webhook.py trading-bot/trade_notifier.py lumibot/strategy.py
	@echo "Syntax OK"

clean:
	rm -rf .venv .pytest_cache __pycache__ trading-bot/__pycache__
