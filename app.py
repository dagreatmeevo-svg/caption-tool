import importlib.util
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"

sys.path.insert(0, str(BACKEND_DIR))

spec = importlib.util.spec_from_file_location("caption_tool_backend", BACKEND_DIR / "main.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load backend/main.py")

backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

app = backend.app
