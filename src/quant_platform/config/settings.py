from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


# Daily-workflow steps paused by REQUIREMENTS §13 (S1-W06). Code and data stay;
# PAUSED_MODULES in .env overrides the set ("none" resumes everything).
PAUSABLE_MODULES = {
    "point_in_time": "盤中／衍生品收集",
    "intraday_features": "盤中衍生特徵",
    "earnings_calls": "法說會收集",
    "model_governance": "模型治理全量預測載入",
    "shadow_trading": "影子交易",
    "promotions": "晉級複驗",
    "rag_index": "報告 RAG 索引",
    # Roadmap S9-W05 (owner 2026-10-04): the 13:50 workflow keeps market data, the listing check and the
    # raw data-quality snapshot; features, GPU models, factor research, walk-forward backtests,
    # ensembles, portfolios, legacy decisions, the legacy after-hours AI and its report stop.
    "legacy_research": "舊版研究流程（特徵、模型、因子、回測、整合、組合、舊盤後 AI、舊報告）",
}
DEFAULT_PAUSED_MODULES = frozenset(PAUSABLE_MODULES)


def parse_paused_modules(value: str | None) -> frozenset[str]:
    if value is None:
        return DEFAULT_PAUSED_MODULES
    text = value.strip().lower()
    if text in {"", "none"}:
        return frozenset()
    names = {part.strip() for part in text.split(",") if part.strip()}
    unknown = names - set(PAUSABLE_MODULES)
    if unknown:
        raise ValueError(f"PAUSED_MODULES 含未知項目：{', '.join(sorted(unknown))}")
    return frozenset(names)


def _night_hour(value: str) -> int:
    """Research runs only at night (REQUIREMENTS §8: 19:00–07:00)."""
    hour = int(value)
    if not (hour >= 19 or hour < 7):
        raise ValueError("RESEARCH_AGENT_HOUR 必須在 19～23 或 0～6 之間（夜間研究）")
    return hour


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
    tw_data_schedule: str = "13:50"
    us_data_schedule: str = "06:30"
    # Minutes a Taiwan run waits for today's official close table before asking
    # Yahoo per symbol (S1-W05). 0 here keeps directly built Settings (tests)
    # offline; from_env defaults to 10.
    tw_official_close_wait_minutes: int = 0
    finmind_base_url: str = "https://api.finmindtrade.com/api/v4/data"
    finmind_token: str = ""
    pit_auto_ingestion_enabled: bool = False
    pit_default_entities: str = "tw_futures_daily:TX,tw_options_daily:TXO"
    pit_lookback_days: int = 7
    paused_modules: frozenset[str] = DEFAULT_PAUSED_MODULES
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    report_email_to: str = ""
    smtp_starttls: bool = True
    # LINE Messaging API push (application/notifications.py); LINE Notify ended 2025-03-31.
    line_enabled: bool = False
    line_channel_access_token: str = ""
    line_to: str = ""
    line_daily_summary: bool = True  # also "no action today" on other trading days (REQUIREMENTS §11)
    google_oauth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:5000/auth/google/callback"
    google_allowed_domain: str = ""
    auth_session_days: int = 14
    auth_cookie_secure: bool = False
    api_write_token: str = ""
    # Cloudflare Access (S2-W01): "development" or "cloudflare-access".
    auth_mode: str = "development"
    public_url: str = ""
    access_team_domain: str = ""
    access_aud: str = ""
    access_allowed_emails: str = ""
    openai_enabled: bool = False
    openai_api_key: str = ""
    openai_response_model: str = "gpt-5.6-luna"
    openai_embedding_enabled: bool = False
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 1024
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: int = 45
    # AI researcher (S4-W04, research/agent/): OpenAI Responses API like VectorDB.
    research_agent_enabled: bool = True
    research_agent_model: str = "gpt-6-luna"
    research_agent_monthly_budget_usd: float = 3.0
    research_agent_rounds_per_night: int = 3
    research_agent_specs_per_round: int = 4
    research_agent_trials_per_night: int = 12
    research_agent_hour: int = 22
    rag_local_dimensions: int = 384
    local_embedding_backend: str = "hash"
    local_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    local_embedding_device: str = "auto"
    local_embedding_batch_size: int = 32
    local_embedding_offline: bool = True

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def is_paused(self, module: str) -> bool:
        return module in self.paused_modules

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
            tw_data_schedule=os.getenv("TW_DATA_SCHEDULE", "13:50"),
            us_data_schedule=os.getenv("US_DATA_SCHEDULE", "06:30"),
            tw_official_close_wait_minutes=max(0, min(30, int(os.getenv("TW_OFFICIAL_CLOSE_WAIT_MINUTES", "10")))),
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
            paused_modules=parse_paused_modules(os.getenv("PAUSED_MODULES")),
            email_enabled=os.getenv("EMAIL_ENABLED", "false").lower() in {"1", "true", "yes"},
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            report_email_to=os.getenv("REPORT_EMAIL_TO", ""),
            smtp_starttls=os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"},
            line_enabled=os.getenv("LINE_ENABLED", "false").lower() in {"1", "true", "yes"},
            line_channel_access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip(),
            line_to=os.getenv("LINE_TO", "").strip(),
            line_daily_summary=os.getenv("LINE_DAILY_SUMMARY", "true").lower() in {"1", "true", "yes"},
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
            auth_mode=os.getenv("AUTH_MODE", "development").strip().lower(),
            public_url=os.getenv("PUBLIC_URL", ""),
            access_team_domain=os.getenv("ACCESS_TEAM_DOMAIN", ""),
            access_aud=os.getenv("ACCESS_AUD", ""),
            access_allowed_emails=os.getenv("ACCESS_ALLOWED_EMAILS", ""),
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
            research_agent_enabled=os.getenv("RESEARCH_AGENT_ENABLED", "true").lower() in {"1", "true", "yes"},
            research_agent_model=os.getenv("RESEARCH_AGENT_MODEL", "gpt-6-luna").strip(),
            research_agent_monthly_budget_usd=max(0.0, float(os.getenv("RESEARCH_AGENT_MONTHLY_BUDGET_USD", "3"))),
            research_agent_rounds_per_night=max(0, min(10, int(os.getenv("RESEARCH_AGENT_ROUNDS_PER_NIGHT", "3")))),
            research_agent_specs_per_round=max(1, min(10, int(os.getenv("RESEARCH_AGENT_SPECS_PER_ROUND", "4")))),
            research_agent_trials_per_night=max(0, min(50, int(os.getenv("RESEARCH_AGENT_TRIALS_PER_NIGHT", "12")))),
            research_agent_hour=_night_hour(os.getenv("RESEARCH_AGENT_HOUR", "22")),
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
