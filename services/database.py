import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

DEFAULT_DB = "/tmp/assistant.db" if not os.access(".", os.W_OK) else os.environ.get("DB_PATH", "/tmp/assistant.db")
DB_PATH = os.environ.get("DB_PATH", DEFAULT_DB)

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            platform_user_id TEXT NOT NULL UNIQUE,
            name TEXT,
            last_active_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            due_datetime TIMESTAMP,
            status TEXT DEFAULT 'pending',
            reminder_count INTEGER DEFAULT 0,
            next_reminder_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            key_name TEXT,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            original_filename TEXT,
            executive_summary TEXT,
            legal_analysis TEXT,
            risk_level TEXT,
            action_items TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()

def upsert_user(platform: str, platform_user_id: str, name: Optional[str] = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO users (platform, platform_user_id, name, last_active_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(platform_user_id) DO UPDATE SET
                name = COALESCE(?, users.name),
                last_active_at = ?
        """, (platform, platform_user_id, name, now, name, now))
        conn.commit()

def get_all_users():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        return [dict(row) for row in cursor.fetchall()]

def add_task(user_id: str, title: str, due_datetime: datetime) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        due_str = due_datetime.isoformat()
        cursor.execute("""
            INSERT INTO tasks (user_id, title, due_datetime, status, next_reminder_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, title, due_str, due_str))
        conn.commit()
        return cursor.lastrowid

def get_due_tasks() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        now_str = datetime.now().isoformat()
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE status = 'pending' AND next_reminder_at <= ?
        """, (now_str,))
        return [dict(row) for row in cursor.fetchall()]

def update_task_chaser(task_id: int, next_reminder: datetime, new_count: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks 
            SET next_reminder_at = ?, reminder_count = ?
            WHERE id = ?
        """, (next_reminder.isoformat(), new_count, task_id))
        conn.commit()

def complete_task(task_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
        conn.commit()

def complete_task_by_title(user_id: str, search_keyword: str) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks 
            SET status = 'completed' 
            WHERE user_id = ? AND status = 'pending' AND title LIKE ?
        """, (user_id, f"%{search_keyword}%"))
        conn.commit()
        return cursor.rowcount > 0

def get_pending_tasks_for_user(user_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE user_id = ? AND status = 'pending'
            ORDER BY due_datetime ASC
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]

def save_memory(user_id: str, content: str, category: str = 'general', key_name: str = '') -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO memories (user_id, category, key_name, content)
            VALUES (?, ?, ?, ?)
        """, (user_id, category, key_name, content))
        conn.commit()
        return cursor.lastrowid

def search_memories(user_id: str, query: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        pattern = f"%{query}%"
        cursor.execute("""
            SELECT * FROM memories
            WHERE user_id = ? AND (content LIKE ? OR key_name LIKE ?)
            ORDER BY created_at DESC LIMIT 5
        """, (user_id, pattern, pattern))
        return [dict(row) for row in cursor.fetchall()]

def save_report(report_id: str, user_id: str, title: str, filename: str, summary: str, legal: str, risk: str, action_items: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reports (id, user_id, title, original_filename, executive_summary, legal_analysis, risk_level, action_items)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (report_id, user_id, title, filename, summary, legal, risk, action_items))
        conn.commit()

def get_report(report_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
