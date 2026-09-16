import os
from pathlib import Path


class Settings:
    ysweet_url: str = os.environ.get("YSWEET_URL", "ys://devkey@localhost:7700")
    ysweet_public_url: str = os.environ.get("YSWEET_PUBLIC_URL", "ws://localhost:7700")
    admin_token: str = os.environ.get("ADMIN_TOKEN", "dev-admin")
    data_dir: Path = Path(os.environ.get("DATA_DIR", "./data"))
    db_path: Path = data_dir / "goblin.db"
    attachments_dir: Path = data_dir / "attachments"
    mirror_dir: Path = data_dir / "mirror"
    mirror_interval: float = float(os.environ.get("MIRROR_INTERVAL", "30"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.attachments_dir.mkdir(parents=True, exist_ok=True)
settings.mirror_dir.mkdir(parents=True, exist_ok=True)
