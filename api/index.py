import sys
from pathlib import Path

# Add the root directory to sys.path so 'backend' module can be resolved
sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.app.main import app
