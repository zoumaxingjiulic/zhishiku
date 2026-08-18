from fastapi import FastAPI

app = FastAPI(
    title="Enterprise Knowledge Base API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """Liveness probe; dependency checks will be added with the ingestion API."""
    return {"status": "ok", "service": "knowledge-base-api"}

