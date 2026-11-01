import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "nexus.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                fact TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

def add_memory(category, fact):
    with connect() as conn:
        conn.execute(
            "INSERT INTO memories(category, fact) VALUES (?, ?)",
            (category, fact)
        )

def list_memories():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM memories ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

def search_memories(query, limit=8):
    terms = [w.lower().strip(".,?!:;()[]{}") for w in query.split() if len(w) > 2]
    rows = list_memories()
    scored = []
    for row in rows:
        text = (row["category"] + " " + row["fact"]).lower()
        score = sum(1 for term in terms if term in text)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]

def remove_memory(memory_id):
    with connect() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
