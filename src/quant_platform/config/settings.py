from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Load a small .env file without making configuration depend on a library."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Quant Research Platform"
    app_env: str = "development"
    secret_key: str = "development-only-key"
    database_url: str = "sqlite:///instance/quant_platform.db"
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = False
    redis_required: bool = False
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_host: str = "127.0.0.1"
    web_port: int = 5000
    scheduler_enabled: bool = True
    scheduler_in_web: bool = True
    scheduler_timezone: str = "Asia/Taipei"
    tw_data_schedule: str = "13:35"
    us_data_schedule: str = "06:30"
    finmind_base_url: str = "https://api.finmindtrade.com/api/v4/data"
    finmind_token: str = ""
    pit_auto_ingestion_enabled: bool = False
    pit_default_entities: str = "tw_futures_daily:TX,tw_options_daily:TXO"
    pit_lookback_days: int = 7
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    report_email_to: str = ""
    smtp_starttls: bool = True
    google_oauth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:5000/auth/google/callback"
    google_allowed_domain: str = ""
    auth_session_days: int = 14
    auth_cookie_secure: bool = False
    api_write_token: str = ""
    openai_enabled: bool = False
    openai_api_key: str = ""
    openai_response_model: str = "gpt-5.6-luna"
    openai_embedding_enabled: bool = False
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 1024
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: int = 45
    rag_local_dimensions: int = 384
    local_embedding_backend: str = "hash"
    local_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    local_embedding_device: str = "auto"
    local_embedding_batch_size: int = 32
    local_embedding_offline: bool = True

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(Path.cwd() / ".env")
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            secret_key=os.getenv("SECRET_KEY", "development-only-key"),
            database_url=os.getenv("DATABASE_URL", "sqlite:///instance/quant_platform.db"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            redis_enabled=os.getenv("REDIS_ENABLED", "false").lower() in {"1", "true", "yes"},
            redis_required=os.getenv("REDIS_REQUIRED", "false").lower() in {"1", "true", "yes"},
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("API_PORT", "8000")),
            web_host=os.getenv("WEB_HOST", "127.0.0.1"),
            web_port=int(os.getenv("WEB_PORT", "5000")),
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes"},
            scheduler_in_web=os.getenv("SCHEDULER_IN_WEB", "true").lower() in {"1", "true", "yes"},
            scheduler_timezone=os.getenv("SCHEDULER_TIMEZONE", "Asia/Taipei"),
            tw_data_schedule=os.getenv("TW_DATA_SCHEDULE", "13:35"),
            us_data_schedule=os.getenv("US_DATA_SCHEDULE", "06:30"),
            finmind_base_url=os.getenv(
                "FINMIND_BASE_URL", "https://api.finmindtrade.com/api/v4/data"
            ),
            finmind_token=os.getenv("FINMIND_TOKEN", ""),
            pit_auto_ingestion_enabled=os.getenv(
                "PIT_AUTO_INGESTION_ENABLED", "false"
            ).lower() in {"1", "true", "yes"},
            pit_default_entities=os.getenv(
                "PIT_DEFAULT_ENTITIES", "tw_futures_daily:TX,tw_options_daily:TXO"
            ),
            pit_lookback_days=max(1, min(31, int(os.getenv("PIT_LOOKBACK_DAYS", "7")))),
            email_enabled=os.getenv("EMAIL_ENABLED", "false").lower() in {"1", "true", "yes"},
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            report_email_to=os.getenv("REPORT_EMAIL_TO", ""),
            smtp_starttls=os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"},
            google_oauth_enabled=os.getenv("GOOGLE_OAUTH_ENABLED", "false").lower() in {"1", "true", "yes"},
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            google_redirect_uri=os.getenv(
                "GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/auth/google/callback"
            ),
            google_allowed_domain=os.getenv("GOOGLE_ALLOWED_DOMAIN", ""),
            auth_session_days=int(os.getenv("AUTH_SESSION_DAYS", "14")),
            auth_cookie_secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
            api_write_token=os.getenv("API_WRITE_TOKEN", ""),
            openai_enabled=os.getenv("OPENAI_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_response_model=os.getenv("OPENAI_RESPONSE_MODEL", "gpt-5.6-luna"),
            openai_embedding_enabled=os.getenv(
                "OPENAI_EMBEDDING_ENABLED", "false"
            ).lower() in {"1", "true", "yes"},
            openai_embedding_model=os.getenv(
                "OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"
            ),
            openai_embedding_dimensions=int(
                os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1024")
            ),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_timeout_seconds=int(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
            rag_local_dimensions=int(os.getenv("RAG_LOCAL_DIMENSIONS", "384")),
            local_embedding_backend=os.getenv("LOCAL_EMBEDDING_BACKEND", "hash").lower(),
            local_embedding_model=os.getenv(
                "LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
            ),
            local_embedding_device=os.getenv("LOCAL_EMBEDDING_DEVICE", "auto").lower(),
            local_embedding_batch_size=max(
                1, min(256, int(os.getenv("LOCAL_EMBEDDING_BATCH_SIZE", "32")))
            ),
            local_embedding_offline=os.getenv(
                "LOCAL_EMBEDDING_OFFLINE", "true"
            ).lower() in {"1", "true", "yes"},
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
