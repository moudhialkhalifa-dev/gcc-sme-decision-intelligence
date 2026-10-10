"""Runtime configuration from environment variables only (no secrets in code)."""
import os


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./local.db")
    data_dir: str = os.getenv("DATA_DIR", "./var")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "150"))
    api_key: str | None = os.getenv("API_KEY") or None
    cors_origins: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
    demo_csv: str = os.getenv("DEMO_CSV", "../data/raw/online_retail_II.csv")
    demo_config: str = os.getenv("DEMO_CONFIG", "../configs/online_retail_ii.json")
    jobs_sync: bool = os.getenv("JOBS_SYNC", "0") == "1"
    static_dir: str | None = os.getenv("STATIC_DIR") or None


settings = Settings()
