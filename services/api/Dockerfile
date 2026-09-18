FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/python ./shared/python
RUN pip install --no-cache-dir ./shared/python
COPY services/api/app ./app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=45s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/readyz', timeout=40).close()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

