from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Google Cloud
    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_REGION: str = "us-central1"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3-flash-preview"   # primary — Gemini API key
    VERTEX_MODEL: str = "gemini-2.5-flash"          # fallback — Vertex AI

    # Elastic
    ELASTIC_URL: str = ""
    ELASTIC_API_KEY: str = ""
    ELASTIC_MCP_URL: str = ""

    # Slack alerts
    SLACK_WEBHOOK_URL: str = ""
    SLACK_ALERT_THRESHOLD: int = 60  # alert when buyer_risk_score >= this value

    # App
    APP_URL: str = "http://localhost:8080"
    PORT: int = 8080

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

OUTPUT_PATH = Path("outputs")
