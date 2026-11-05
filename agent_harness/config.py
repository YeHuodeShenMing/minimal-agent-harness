"""Minimal .env reader: literal values only; never execute shell content."""
import os
from pathlib import Path


def load_env(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1] / '.env'
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator and key.strip() in {'LLM_API_KEY', 'LLM_MODEL', 'LLM_BASE_URL', 'TAVILY_API_KEY'}:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)
