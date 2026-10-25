import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="no_idea_test_"))
os.environ["LLM_API_KEY"] = ""
os.environ["CACHE_MODE"] = "off"
os.environ["TG_BOT_TOKEN"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
