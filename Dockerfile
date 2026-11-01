FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src ./src
COPY db ./db
COPY data/splits.json ./data/splits.json
COPY data/meter_catalog.csv ./data/meter_catalog.csv
COPY data/recommendations.csv ./data/recommendations.csv
COPY data/live_meters.csv ./data/live_meters.csv
# Downloaded datasets go to a volume the app user can write (DATA_DIR in docker-compose.yml).
RUN mkdir -p /app/cache && chown app /app/cache
USER app
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
