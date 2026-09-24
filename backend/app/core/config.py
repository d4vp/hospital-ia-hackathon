"""Centralised backend configuration (read from environment / .env)."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # MongoDB
    MONGO_URI: str = "mongodb://localhost:27017"
    DB_NAME: str = "hospital_susana_lopez"

    # OpenAI (empty key => the agent runs in fallback "Plan B" mode)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_TIMEOUT_SECONDS: float = 30.0

    # Security
    JWT_SECRET: str = "change-me-with-a-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480
    CORS_ORIGINS: str = "http://localhost:8501"
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@hospital.local"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""
    BOOTSTRAP_ADMIN_NAME: str = "Administrator"

    # Data loading
    DATA_DIR: str = "app/data"
    EXCEL_FILENAME: str = "DateBaseHIS.xlsx"
    MAX_UPLOAD_MB: int = 120
    # Real bed capacity per bed group (JSON). Overrides the "distinct beds observed" proxy,
    # e.g. {"UNIDAD DE CUIDADO INTENSIVO": 12, "PEDIATRIA": 40}
    BED_CAPACITY_OVERRIDES: str = ""
    QUERY_MAX_TIME_MS: int = 8000

    # Agent
    CHAT_MEMORY_TURNS: int = 6
    DEFAULT_LANGUAGE: str = "es"

    # Alerts / n8n
    N8N_WEBHOOK_URL: str = ""
    N8N_WEBHOOK_SECRET: str = ""
    ALERT_LANGUAGE: str = "es"
    ALERT_CHECK_INTERVAL_SECONDS: int = 900
    ALERT_OCCUPANCY_THRESHOLD_PCT: float = 85.0
    ALERT_INVENTORY_DAYS_THRESHOLD: float = 5.0
    ALERT_ER_WAIT_THRESHOLD_MIN: float = 60.0
    ALERT_TRIAGE2_WAIT_THRESHOLD_MIN: float = 30.0
    ALERT_SURGERY_COMPLETION_THRESHOLD_PCT: float = 70.0

    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("DEFAULT_LANGUAGE", "ALERT_LANGUAGE")
    @classmethod
    def _supported_language(cls, value: str) -> str:
        return value if value in ("es", "en") else "es"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip() and o.strip() != "*"]

    @property
    def excel_path(self) -> Path:
        """The ONLY file the ETL is allowed to read. Clients can never choose a path."""
        return Path(self.DATA_DIR) / self.EXCEL_FILENAME

    @property
    def bed_capacity_overrides(self) -> dict[str, int]:
        import json

        try:
            data = json.loads(self.BED_CAPACITY_OVERRIDES) if self.BED_CAPACITY_OVERRIDES.strip() else {}
            return {str(k): int(v) for k, v in data.items() if int(v) > 0}
        except (ValueError, TypeError, AttributeError):
            return {}

    @property
    def openai_enabled(self) -> bool:
        return bool(self.OPENAI_API_KEY.strip())

    @property
    def insecure_jwt_secret(self) -> bool:
        return self.JWT_SECRET.startswith("change-me") or len(self.JWT_SECRET) < 32


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Collection names in one place (English, snake_case).
COLLECTIONS = {
    "admissions": "admissions",
    "inventory": "inventory",
    "medication_usage_daily": "medication_usage_daily",
    "service_demand_daily": "service_demand_daily",
    "bed_capacity": "bed_capacity",
    "metadata": "metadata",
    "users": "users",
    "alerts": "alerts",
    "conversations": "conversations",
    "agent_logs": "agent_logs",
}

# Fields that must never leave the backend (PII / specific diagnosis).
PII_PATHS = (
    "patient.name",
    "patient.birth_date",
    "patient.patient_id",
    "triage.chief_complaint",
    "diagnosis.code",
    "diagnosis.name",
)
