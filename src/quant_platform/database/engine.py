from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///"):
            database_path = Path(url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)
        is_sqlite = url.startswith("sqlite")
        connect_args = (
            {"check_same_thread": False, "timeout": 30}
            if is_sqlite
            else {}
        )
        self.engine: Engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        if is_sqlite:
            @event.listens_for(self.engine, "connect")
            def _configure_sqlite(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            with self.engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        from quant_platform.database import models  # noqa: F401

        Base.metadata.create_all(self.engine)
        self._apply_additive_compatibility_migrations()
        if self.engine.dialect.name == "sqlite":
            self._apply_sqlite_utc_migration()

    def _apply_sqlite_utc_migration(self) -> None:
        """Normalize legacy Taiwan close timestamps that SQLite stored as local wall time."""
        migration = "2026-07-20-tw-daily-bars-to-utc"
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS local_schema_migrations ("
                "name VARCHAR(120) PRIMARY KEY, applied_at DATETIME NOT NULL)"
            ))
            applied = connection.execute(
                text("SELECT 1 FROM local_schema_migrations WHERE name = :name"),
                {"name": migration},
            ).scalar_one_or_none()
            if applied is not None:
                return
            connection.execute(text(
                "UPDATE market_bars SET "
                "event_time = datetime(event_time, '-8 hours'), "
                "available_time = datetime(available_time, '-8 hours') "
                "WHERE market = 'TW' AND source = 'yahoo_finance' "
                "AND strftime('%H:%M', event_time) = '13:30' "
                "AND strftime('%H:%M', available_time) = '13:45'"
            ))
            for table_name in ("feature_values", "label_values", "regime_states"):
                connection.execute(text(
                    f"UPDATE {table_name} SET "
                    "event_time = datetime(event_time, '-8 hours'), "
                    "available_time = datetime(available_time, '-8 hours') "
                    "WHERE (symbol LIKE '%.TW' OR symbol LIKE '%.TWO' OR symbol = '^TWII') "
                    "AND strftime('%H:%M', event_time) = '13:30'"
                ))
            connection.execute(
                text(
                    "INSERT INTO local_schema_migrations(name, applied_at) "
                    "VALUES (:name, CURRENT_TIMESTAMP)"
                ),
                {"name": migration},
            )

    def _apply_additive_compatibility_migrations(self) -> None:
        """Apply safe additive changes needed before the Alembic deployment stage.

        Local SQLite users have no migration service yet. These statements only add
        nullable/defaulted metadata columns and never drop or rewrite research data.
        """
        additions = {
            "rag_query_audits": {
                "answer_provider": "VARCHAR(40) NOT NULL DEFAULT 'local'",
                "answer_model": "VARCHAR(100) NOT NULL DEFAULT 'evidence-summary-v1'",
            },
            "research_universe": {
                "company_name": "VARCHAR(200)",
                "company_abbreviation": "VARCHAR(100)",
                "industry_code": "VARCHAR(10)",
                "paid_in_capital": "BIGINT",
                "issued_shares": "BIGINT",
                "market_value_twd": "BIGINT",
                "market_rank": "INTEGER",
                "metadata_source": "VARCHAR(80)",
                "metadata_updated_at": "DATETIME",
            },
        }
        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())
        with self.engine.begin() as connection:
            for table_name, columns in additions.items():
                if table_name not in existing_tables:
                    continue
                existing_columns = {
                    item["name"] for item in inspector.get_columns(table_name)
                }
                for column_name, definition in columns.items():
                    if column_name not in existing_columns:
                        connection.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                        ))

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
