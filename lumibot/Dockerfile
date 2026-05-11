FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir lumibot yfinance
COPY strategy.py .
CMD ["python3", "strategy.py"]
