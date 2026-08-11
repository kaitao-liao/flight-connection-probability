FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLIGHT_CONNECTION_ENV=production \
    FLIGHT_CONNECTION_DB=/app/data/production/flights_production.duckdb

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir --requirement requirements-runtime.txt

COPY backend/flight_connection/__init__.py ./backend/flight_connection/__init__.py
COPY backend/flight_connection/api.py ./backend/flight_connection/api.py
COPY backend/flight_connection/service.py ./backend/flight_connection/service.py
COPY backend/flight_connection/schemas.py ./backend/flight_connection/schemas.py
COPY backend/flight_connection/delay_model.py ./backend/flight_connection/delay_model.py
COPY backend/flight_connection/simulator.py ./backend/flight_connection/simulator.py
COPY backend/flight_connection/acquire.py ./backend/flight_connection/acquire.py
COPY data/production/flights_production.duckdb ./data/production/flights_production.duckdb

RUN chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').getenv('PORT', '8000') + '/health', timeout=4)"

CMD ["sh", "-c", "exec uvicorn backend.flight_connection.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
