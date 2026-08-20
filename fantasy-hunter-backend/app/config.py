from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FH_", extra="ignore")

    # SQLite locally; swap for a postgresql+psycopg:// URL without touching the models.
    database_url: str = f"sqlite:///{(BASE_DIR / 'fantasy_hunter.db').as_posix()}"

    fpl_base_url: str = "https://fantasy.premierleague.com/api"
    # The FPL API is unofficial and undocumented. Be a polite client.
    http_timeout_seconds: float = 20.0
    http_max_concurrency: int = 8
    user_agent: str = "fantasy-hunter/0.1 (local development)"

    # Seconds a cached FPL response stays fresh. Bootstrap changes at most every
    # few minutes even in-play, so there is no reason to hit origin per request.
    cache_ttl_seconds: int = 300

    # The Angular dev server runs on 4300 (4200 and 4500 are in use on this
    # machine). The dev server proxies /api, so CORS is only a fallback for
    # hitting the API directly from a browser.
    cors_origins: list[str] = ["http://localhost:4300", "http://127.0.0.1:4300"]


settings = Settings()
