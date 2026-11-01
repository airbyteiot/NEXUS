
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "nexus_tasks.db"


def connect_db():
    return sqlite3.connect(DB_PATH)


def create_tables():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                task_date TEXT NOT NULL,
                task_time TEXT,
                notes TEXT,
                status TEXT DEFAULT 'Pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def add_task(title, task_date, task_time="", notes=""):
    with connect_db() as conn:
        conn.execute("""
            INSERT INTO tasks (title, task_date, task_time, notes)
            VALUES (?, ?, ?, ?)
        """, (title, task_date, task_time, notes))


def get_tasks():
    with connect_db() as conn:
        cursor = conn.execute("""
            SELECT id, title, task_date, task_time, notes, status
            FROM tasks
            ORDER BY task_date, task_time
        """)
        return cursor.fetchall()


def complete_task(task_id):
    with connect_db() as conn:
        conn.execute("""
            UPDATE tasks SET status = 'Completed'
            WHERE id = ?
        """, (task_id,))


def delete_task(task_id):
    with connect_db() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


create_tables()
