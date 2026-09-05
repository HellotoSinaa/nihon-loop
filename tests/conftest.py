import os
import tempfile

# Must run before `app.db` (and anything importing it) is loaded, since
# app/db.py reads DATABASE_URL at import time.
_tmp_dir = tempfile.mkdtemp(prefix="nihon-loop-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test_nihon.db"
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.example.com")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("GOOGLE_API_KEY", "")
os.environ.setdefault("POWERLOBSTER_WEBHOOK_SECRET", "")
