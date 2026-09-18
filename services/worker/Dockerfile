FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/worker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/python ./shared/python
RUN pip install --no-cache-dir ./shared/python
COPY services/worker/app ./app

HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD python -m enterprise_kb.health
CMD ["python", "-m", "app.main"]

