FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# Cloud Run: concurrency=1 (the evidence ledger is process-wide)
CMD exec uvicorn finpilot.api:app --host 0.0.0.0 --port ${PORT:-8080}
