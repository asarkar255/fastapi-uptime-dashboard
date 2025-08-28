FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config ./config
ENV SERVICES_CONFIG=/app/config/services.yaml
ENV CHECK_INTERVAL_SECONDS=60
ENV GLOBAL_TIMEOUT_SECONDS=8.0
ENV CONCURRENCY_LIMIT=10
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
