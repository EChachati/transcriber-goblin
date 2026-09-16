import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="tg-test-"))
os.environ.setdefault("ADMIN_TOKEN", "test-admin")
os.environ.setdefault("YSWEET_URL", "ys://devkey@localhost:7700")
os.environ.setdefault("YSWEET_PUBLIC_URL", "ws://localhost:7700")
