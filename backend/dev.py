"""Explicit local mock startup; never uses real Notion/Gemini/Feishu credentials."""
import json
import os
from pathlib import Path
from .app import create_app

fixture=json.loads(Path(__file__).with_name('mock_activities.json').read_text())
app=create_app(os.environ.get('TIMEMAP_DB','/tmp/timemap-mock.sqlite3'),os.environ['TIMEMAP_DEV_TOKEN'],activities=fixture,origins=['http://127.0.0.1:8001','http://localhost:8001'],eager=False)
