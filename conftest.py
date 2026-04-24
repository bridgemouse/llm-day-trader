# conftest.py
# Adds project root to sys.path so pytest can find project packages without install.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
