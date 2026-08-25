# e2e_smoke.py — сквозная проверка конвейера (этап 1.5, canary фазы 1)
"""Проверяет, что ВСЯ система работает вместе: state.db → индексация →
страница → эмбеддинг → поиск. Использует temp-окружение (не живую БД),
мокает LLM (extract/embed) — проверяет код, не API."""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # родитель scripts/ — чтобы import wiki_v2
sys.path.insert(0, _HERE)

tmp = Path(tempfile.mkdtemp(prefix="wiki_e2e_"))
os.environ["WIKI_PATH"] = str(tmp / "wiki")
os.environ["HERMES_STATE_DB"] = str(tmp / "state.db")
(tmp / "wiki").mkdir(exist_ok=True)

# 1) тестовая сессия
conn = sqlite3.connect(os.environ["HERMES_STATE_DB"])
conn.executescript("""
CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                       role TEXT, content TEXT, timestamp REAL);
""")
conn.execute("INSERT INTO sessions VALUES ('e2e1','Тест E2E',?)", (time.time() - 3600,))
conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('e2e1','user','Как подключить немотрон через API?',1)")
conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('e2e1','assistant','Нужен NVIDIA_API_KEY и endpoint.',2)")
conn.commit()
conn.close()
print("1. state.db создан")

from wiki_v2 import config

config.reload()
from wiki_v2.index_db import IndexDB
from wiki_v2.indexer import main as index_main
from wiki_v2.search import search as search_fn

good = {"summary": "Подключение немотрона: NVIDIA_API_KEY + endpoint integrate.api.nvidia.com.",
        "key_topics": ["немотрон", "nvidia"], "decisions": [], "facts": [],
        "links": [], "entities": [], "concepts": [], "quality": "ok"}
import numpy as np

fake_vec = np.random.rand(1024).astype("float32")

# 2) индексация (мок LLM)
with mock.patch("wiki_v2.indexer.extract_content", return_value=good), \
     mock.patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
    n = index_main()
print("2. индексация обработала:", n)

# 3) страница + эмбеддинг в БД
idx = IndexDB(str(tmp / "wiki" / ".index_v2.db"))
pages = idx.all_pages()
emb = idx.get_all_embeddings()
ver = idx.conn.execute("PRAGMA user_version").fetchone()[0]
idx.close()
print("3. страниц:", len(pages), "| эмбеддингов:", len(emb), "| user_version:", ver)

# 4) поиск (мок embed)
with mock.patch("wiki_v2.search.embed", return_value=[fake_vec]):
    hits, _ = search_fn("как подключить немотрон")
print("4. поиск хитов:", len(hits))

ok = n == 1 and len(pages) == 1 and len(emb) == 1 and hits
print("E2E:", "✅ КОНВЕЙЕР РАБОТАЕТ ЦЕЛИКОМ" if ok else "❌ ПРОБЛЕМА В ИНТЕГРАЦИИ")
sys.exit(0 if ok else 1)
