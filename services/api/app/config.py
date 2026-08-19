import os
from dataclasses import dataclass


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mysql_dsn: str = os.getenv("MYSQL_DSN", "")
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
    minio_bucket: str = os.getenv("MINIO_BUCKET", "enterprise-kb")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "")
    milvus_uri: str = os.getenv("MILVUS_URI", "http://milvus:19530")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "kb_content_units_v1")
    opensearch_url: str = os.getenv("OPENSEARCH_URL", "https://opensearch:9200")
    opensearch_username: str = os.getenv("OPENSEARCH_USERNAME", "admin")
    opensearch_password: str = os.getenv("OPENSEARCH_PASSWORD", "")
    opensearch_index: str = os.getenv("OPENSEARCH_INDEX", "kb-content-units-v1")
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
    auth_cookie_secure: bool = env_bool("AUTH_COOKIE_SECURE")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    admin_display_name: str = os.getenv("ADMIN_DISPLAY_NAME", "平台管理员")
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES") or str(200 * 1024 * 1024))
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "local_hash")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "")
    local_embedding_dim: int = int(os.getenv("LOCAL_EMBEDDING_DIM", "384"))
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    rerank_base_url: str = os.getenv("RERANK_BASE_URL", "")
    rerank_api_key: str = os.getenv("RERANK_API_KEY", "")
    rerank_model: str = os.getenv("RERANK_MODEL", "")
    local_test_mode: bool = env_bool("LOCAL_TEST_MODE", True)


settings = Settings()
