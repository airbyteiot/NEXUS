import streamlit as st
import requests
import base64
import io
import html
import speech_recognition as sr
import sqlite3
import uuid
import os
import re
import subprocess
import shutil
import ctypes
import threading
import time
from urllib.parse import urlencode
from urllib.parse import quote_plus, quote
from datetime import datetime
from streamlit_mic_recorder import mic_recorder
from memory import init_db, add_memory, list_memories, search_memories, remove_memory
from tools import create_note, list_workspace_files, open_approved_website, WORKSPACE

# =============================================================================
# CONFIGURATION
# =============================================================================
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b"
CHAT_DB = "nexus_chat_history.db"

NAV_PAGES = ["Dashboard", "AI Chat", "Personal Wiki", "Computer Tools", "System Status", "My Plans & Reminders", "Attendance Checker"]
NAV_ICONS = {
    "Dashboard": "🏠",
    "AI Chat": "💬",
    "Personal Wiki": "📚",
    "Computer Tools": "🧰",
    "System Status": "📊",
    "My Plans & Reminders": "📅",
    "Attendance Checker": "🎓",
}
TOOLS_SECTIONS = ["🖥️ Apps & Windows", "✉️ Email Assistant", "📁 Files & Websites"]
EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[^@\s]+"

st.set_page_config(
    page_title="NEXUS | Personal AI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)
init_db()

# =============================================================================
# SESSION STATE
# =============================================================================
defaults = {
    "messages": [],
    "pending_prompt": None,
    "last_audio_id": None,
    "navigation": "Dashboard",
    "pending_navigation": None,
    "pending_computer_tools_section": None,
    "computer_tools_section": TOOLS_SECTIONS[0],
    "active_chat_id": None,
    "email_subject": "",
    "email_body": "",
    "email_recipient": "",
    "voice_enabled": True,
    "pending_task_candidate": None
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# Apply navigation requests before the sidebar radio widget is created.
if st.session_state.pending_navigation:
    st.session_state.navigation = st.session_state.pending_navigation
    st.session_state.pending_navigation = None
if st.session_state.pending_computer_tools_section:
    st.session_state.computer_tools_section = st.session_state.pending_computer_tools_section
    st.session_state.pending_computer_tools_section = None

# =============================================================================
# LOCAL CHAT HISTORY DATABASE
# =============================================================================
def init_chat_history():
    with sqlite3.connect(CHAT_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
        """)


def get_conversations(limit=30):
    with sqlite3.connect(CHAT_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, updated_at FROM conversations "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def load_conversation(conversation_id):
    with sqlite3.connect(CHAT_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role, content FROM chat_messages "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def save_chat_message(role, content):
    """Automatically save each user/assistant message to the local SQLite history."""
    now = datetime.now().isoformat(timespec="seconds")
    chat_id = st.session_state.active_chat_id
    with sqlite3.connect(CHAT_DB) as conn:
        if not chat_id:
            chat_id = str(uuid.uuid4())
            first_line = content.strip().splitlines()[0] if content.strip() else "New conversation"
            title = first_line[:42] + ("…" if len(first_line) > 42 else "")
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, title, now, now)
            )
            st.session_state.active_chat_id = chat_id
        else:
            existing = conn.execute(
                "SELECT title FROM conversations WHERE id = ?",
                (chat_id,)
            ).fetchone()
            if existing is None:
                first_line = content.strip().splitlines()[0] if content.strip() else "New conversation"
                title = first_line[:42] + ("…" if len(first_line) > 42 else "")
                conn.execute(
                    "INSERT INTO conversations (id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (chat_id, title, now, now)
                )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, chat_id)
            )
        conn.execute(
            "INSERT INTO chat_messages "
            "(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, now)
        )


def add_message_to_chat(role, content):
    st.session_state.messages.append({"role": role, "content": content})
    save_chat_message(role, content)


def begin_new_chat():
    st.session_state.active_chat_id = None
    st.session_state.messages = []
    st.session_state.pending_prompt = None


def open_saved_chat(chat_id):
    st.session_state.active_chat_id = chat_id
    st.session_state.messages = load_conversation(chat_id)
    st.session_state.pending_prompt = None


def delete_saved_chat(chat_id):
    with sqlite3.connect(CHAT_DB) as conn:
        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (chat_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (chat_id,))
    if st.session_state.active_chat_id == chat_id:
        begin_new_chat()


init_chat_history()


# =============================================================================
# TASKS AND REMINDERS (local SQLite)
# =============================================================================
TASK_DB = "nexus_tasks.db"


def init_task_db():
    with sqlite3.connect(TASK_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                task_date TEXT NOT NULL,
                task_time TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL
            )
        """)


def add_task(title, task_date, task_time="", notes=""):
    with sqlite3.connect(TASK_DB) as conn:
        conn.execute(
            "INSERT INTO tasks (title, task_date, task_time, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title.strip(), task_date, task_time, notes.strip(),
             datetime.now().isoformat(timespec="seconds"))
        )


def get_tasks():
    with sqlite3.connect(TASK_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY task_date, task_time, id"
        ).fetchall()
        return [dict(row) for row in rows]


def update_task_status(task_id, status):
    with sqlite3.connect(TASK_DB) as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def delete_task(task_id):
    with sqlite3.connect(TASK_DB) as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


init_task_db()

# Add notification tracking to older task databases.
with sqlite3.connect(TASK_DB) as _conn:
    _cols = [r[1] for r in _conn.execute('PRAGMA table_info(tasks)').fetchall()]
    if 'notified_at' not in _cols:
        _conn.execute('ALTER TABLE tasks ADD COLUMN notified_at TEXT DEFAULT NULL')

def _nexus_toast(title, message):
    try:
        from winotify import Notification, audio
        toast = Notification(app_id='NEXUS Personal Assistant', title='NEXUS Reminder: ' + title, msg=message or 'Your task is due now.', duration='long')
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception:
        pass

def _nexus_reminder_worker():
    while True:
        try:
            _now = datetime.now()
            _due_time = _now.strftime('%Y-%m-%d %H:%M')
            with sqlite3.connect(TASK_DB, timeout=10) as _conn:
                _conn.row_factory = sqlite3.Row
                _due = _conn.execute("SELECT id, title, notes FROM tasks WHERE status='Pending' AND COALESCE(task_time, '') != '' AND task_date || ' ' || task_time <= ? AND notified_at IS NULL", (_due_time,)).fetchall()
                for _task in _due:
                    _conn.execute('UPDATE tasks SET notified_at=? WHERE id=? AND notified_at IS NULL', (_now.isoformat(timespec='seconds'), _task['id']))
                    _nexus_toast(_task['title'], _task['notes'])
        except Exception:
            pass
        time.sleep(20)

@st.cache_resource
def _start_nexus_reminders():
    _thread = threading.Thread(target=_nexus_reminder_worker, name='NEXUSReminderWorker', daemon=True)
    _thread.start()
    return _thread

_start_nexus_reminders()

# =============================================================================
# DESIGN SYSTEM
# Harbor-night palette: deep teal surfaces with one warm lamp-amber accent.
# Fraunces (display) + DM Sans (interface). Fonts load from Google Fonts when
# online and fall back to Georgia / Segoe UI when offline.
# =============================================================================
THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap');

:root {
    --bg: #0f2326;
    --bg-side: #0b1b1e;
    --panel: #163236;
    --panel-hi: #1c3d42;
    --line: #2b4d52;
    --text: #e8f1ee;
    --muted: #93b3ae;
    --amber: #f2b66d;
    --amber-ink: #2a1a05;
    --ok: #7fd6b0;
    --bad: #ff8a7a;
    --serif: 'Fraunces', Georgia, 'Times New Roman', serif;
    --sans: 'DM Sans', 'Segoe UI', system-ui, -apple-system, sans-serif;
}

/* ---------- base ---------- */
html { font-size: 15px; }
[data-testid="stAppDeployButton"], .stDeployButton, .stAppDeployButton { display: none; }
.stApp {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
}
header[data-testid="stHeader"] { background: transparent; }
div[data-testid="stDecoration"], footer { display: none; }
.block-container { padding-top: 2.4rem; max-width: 1100px; }

.stApp h1, .stApp h2, .stApp h3 {
    font-family: var(--serif);
    font-weight: 600;
    color: var(--text);
    letter-spacing: -0.01em;
}
.stApp h1 { font-size: 2.1rem; margin-bottom: 0.2rem; }
.stApp p, .stApp li, .stApp label,
[data-testid="stMarkdownContainer"] { font-family: var(--sans); }
[data-testid="stWidgetLabel"] p, .stApp label p { color: var(--muted); }
[data-testid="stCaptionContainer"], .stApp small { color: var(--muted); }
.stApp hr { border-color: var(--line); margin: 1.4rem 0; }
.stApp a { color: var(--amber); }

/* ---------- sidebar ---------- */
.stApp [data-testid="stSidebar"] {
    background: var(--bg-side);
    border-right: 1px solid var(--line);
}
.stApp [data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
.stApp [data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
.nx-brand-wrap {
    display: flex; align-items: center; gap: 12px;
    padding: 6px 4px 4px 4px;
}
.nx-logo {
    width: 40px; height: 40px; border-radius: 11px; flex: none;
    background: var(--amber); color: var(--amber-ink);
    display: flex; align-items: center; justify-content: center;
    font-family: var(--serif); font-weight: 600; font-size: 1.35rem;
}
.nx-brand {
    font-family: var(--serif);
    font-size: 1.45rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--text);
    line-height: 1.1;
}
.nx-brand-sub { color: var(--muted); font-size: 0.8rem; margin-top: 2px; }
.nx-side-label {
    color: var(--muted); font-size: 0.78rem; font-weight: 600;
    letter-spacing: 0.02em; margin: 6px 0 8px 4px;
}
.nx-side-card {
    border: 1px solid var(--line); border-radius: 12px;
    background: var(--panel); padding: 12px 14px; margin-top: 4px;
}
.nx-engine { display: flex; align-items: center; gap: 9px; font-size: 0.92rem; font-weight: 500; }
.nx-side-meta { color: var(--muted); font-size: 0.8rem; margin-top: 8px; line-height: 1.5; }
.nx-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.nx-dot.ok { background: var(--ok); box-shadow: 0 0 0 3px rgba(127, 214, 176, 0.18); }
.nx-dot.bad { background: var(--bad); box-shadow: 0 0 0 3px rgba(255, 138, 122, 0.18); }
.stApp [data-testid="stSidebar"] hr { margin: 1rem 0; }

/* ---------- radio: pills in the main area, rows in the sidebar ---------- */
.stApp div[role="radiogroup"] { gap: 6px; }
.stApp div[role="radiogroup"] > label {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 6px 16px;
    background: transparent;
    transition: background 0.15s, border-color 0.15s;
}
.stApp div[role="radiogroup"] > label:hover { background: var(--panel); }
.stApp div[role="radiogroup"] > label:has(input:checked) {
    background: var(--panel-hi);
    border-color: var(--amber);
}

/* ---------- buttons ---------- */
.stApp .stButton > button,
.stApp .stLinkButton > a,
.stApp div[data-testid="stFormSubmitButton"] > button {
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
    font-family: var(--sans);
    font-weight: 500;
    transition: border-color 0.15s, background 0.15s;
}
.stApp .stButton > button:hover,
.stApp .stLinkButton > a:hover,
.stApp div[data-testid="stFormSubmitButton"] > button:hover {
    border-color: var(--amber);
    background: var(--panel-hi);
    color: var(--text);
}
.stApp .stButton > button[kind="primary"],
.stApp div[data-testid="stFormSubmitButton"] > button[kind="primary"],
.stApp div[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] {
    background: var(--amber);
    border-color: var(--amber);
    color: var(--amber-ink);
    font-weight: 600;
}
.stApp .stButton > button[kind="primary"]:hover,
.stApp div[data-testid="stFormSubmitButton"] > button[kind="primary"]:hover,
.stApp div[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:hover {
    background: #f7c98f;
    border-color: #f7c98f;
    color: var(--amber-ink);
}
.stApp [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.4rem; }
.stApp [data-testid="stSidebar"] .stButton > button[kind="primary"],
.stApp [data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {
    background: var(--panel-hi);
    border-color: transparent;
    color: var(--text);
    font-weight: 600;
}
.stApp [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover,
.stApp [data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"]:hover {
    background: var(--panel-hi);
    border-color: transparent;
    color: var(--text);
}
.stApp [data-testid="stSidebar"] .stButton > button {
    justify-content: flex-start;
    text-align: left;
    background: transparent;
    border-color: transparent;
    min-height: 2.3rem;
    padding: 0.25rem 0.7rem;
}
.stApp [data-testid="stSidebar"] .stButton > button > div,
.stApp [data-testid="stSidebar"] .stButton > button [data-testid="stMarkdownContainer"] {
    width: 100%; justify-content: flex-start; text-align: left;
}
.stApp [data-testid="stSidebar"] .stButton > button:hover {
    background: var(--panel);
    border-color: var(--line);
}
.stApp [data-testid="stSidebar"] .stButton > button p {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.9rem; text-align: left;
}
.stApp [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] { gap: 0.3rem; align-items: center; }
.stApp [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] .stButton > button { justify-content: center; }
.stApp [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child .stButton > button {
    color: var(--muted); padding: 0.25rem 0;
}
.stApp [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child .stButton > button:hover {
    color: var(--bad); border-color: var(--bad);
}
.stApp button:focus-visible, .stApp a:focus-visible,
.stApp input:focus-visible, .stApp textarea:focus-visible {
    outline: 2px solid var(--amber);
    outline-offset: 2px;
}

/* ---------- inputs ---------- */
.stApp div[data-baseweb="input"],
.stApp div[data-baseweb="textarea"],
.stApp div[data-baseweb="select"] > div {
    background: var(--bg-side);
    border-radius: 10px;
    border: 1px solid var(--line);
}
.stApp div[data-baseweb="input"]:focus-within,
.stApp div[data-baseweb="textarea"]:focus-within { border-color: var(--amber); }
.stApp div[data-baseweb="input"] input,
.stApp div[data-baseweb="textarea"] textarea { color: var(--text); }
div[data-testid="stForm"] {
    border: 1px solid var(--line);
    border-radius: 12px;
    background: rgba(22, 50, 54, 0.45);
}
div[data-testid="stAlert"] { border-radius: 10px; }

/* ---------- chat ---------- */
div[data-testid="stChatMessage"] {
    background: transparent;
    border: 0;
    border-left: 2px solid var(--line);
    border-radius: 0;
    padding: 6px 0 6px 16px;
    margin-bottom: 14px;
}
div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]),
div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: var(--panel);
    border-left: 2px solid var(--amber);
    border-radius: 0 12px 12px 0;
    padding: 12px 16px;
}
div[data-testid="stChatMessage"] p { line-height: 1.6; font-size: 1rem; }
div[data-testid="stChatMessage"] { gap: 0.9rem; }
div[data-testid^="stChatMessageAvatar"], div[data-testid^="chatAvatarIcon"] {
    background: var(--panel-hi) !important;
    color: var(--text) !important;
    border-radius: 10px;
}
div[data-testid^="stChatMessageAvatar"] svg, div[data-testid^="chatAvatarIcon"] svg { fill: var(--text); color: var(--text); }
[data-testid="stBottom"], [data-testid="stBottom"] > div { background: var(--bg); }
[data-testid="stChatInput"] {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 14px;
}
[data-testid="stChatInput"]:focus-within { border-color: var(--amber); }
[data-testid="stChatInput"] textarea { color: var(--text); font-family: var(--sans); }
[data-testid="stChatInput"] button { background: var(--amber); color: var(--amber-ink); border-radius: 10px; }

/* ---------- dashboard tiles ---------- */
[class*="st-key-tile_"] {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 18px 18px 14px 18px;
    transition: border-color 0.15s;
}
[class*="st-key-tile_"]:hover { border-color: var(--amber); }
.nx-tile-icon {
    width: 40px; height: 40px; border-radius: 10px;
    background: rgba(242, 182, 109, 0.16);
    display: flex; align-items: center; justify-content: center;
    font-size: 1.25rem; margin-bottom: 10px;
}
.nx-tile-title { font-family: var(--serif); font-size: 1.2rem; font-weight: 600; }
.nx-tile-text { color: var(--muted); font-size: 0.92rem; margin: 3px 0 10px 0; min-height: 2.8em; line-height: 1.45; }

/* ---------- custom components ---------- */
.nx-hero { padding: 8px 0 26px 0; border-bottom: 1px solid var(--line); margin-bottom: 26px; }
.nx-hero-hello { font-family: var(--serif); color: var(--muted); font-size: 1.15rem; margin: 0 0 6px 0; }
.nx-hero h1 { font-size: 2.7rem; line-height: 1.12; margin: 0 0 12px 0; }
.nx-hero p { color: var(--muted); font-size: 1rem; margin: 0; max-width: 60ch; line-height: 1.6; }

.nx-strip {
    display: flex; flex-wrap: wrap;
    border: 1px solid var(--line); border-radius: 12px;
    background: var(--panel); margin-bottom: 30px; overflow: hidden;
}
.nx-cell { flex: 1 1 200px; padding: 16px 20px; border-left: 1px solid var(--line); }
.nx-cell:first-child { border-left: 0; }
.nx-cell-label { color: var(--muted); font-size: 0.85rem; display: flex; align-items: center; gap: 8px; }
.nx-cell-value { font-family: var(--serif); font-size: 1.6rem; font-weight: 600; margin-top: 4px; }
.nx-cell-note { color: var(--muted); font-size: 0.82rem; margin-top: 2px; }

.nx-section { font-family: var(--serif); font-size: 1.4rem; font-weight: 600; margin: 8px 0 4px 0; }
.nx-section-note { color: var(--muted); font-size: 0.92rem; margin-bottom: 8px; }

.nx-link-title { font-family: var(--serif); font-size: 1.15rem; font-weight: 600; }
.nx-link-text { color: var(--muted); font-size: 0.9rem; margin-top: 2px; }
.nx-rule { border-bottom: 1px solid var(--line); margin: 10px 0 14px 0; }

.nx-mem { border-left: 2px solid var(--amber); padding: 4px 0 4px 14px; margin: 10px 0; }
.nx-mem-tag { color: var(--amber); font-size: 0.82rem; font-weight: 600; }
.nx-mem-text { white-space: pre-wrap; line-height: 1.55; margin-top: 2px; overflow-wrap: anywhere; }

.nx-welcome { padding: 10px 0 14px 0; }
.nx-welcome h2 { font-size: 2.1rem; margin: 0 0 6px 0; }
.nx-welcome p { color: var(--muted); margin: 0; }

.nx-fact { display: flex; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--line); }
.nx-fact-k { color: var(--muted); flex: 0 0 170px; }
.nx-fact-v { flex: 1; overflow-wrap: anywhere; }

@media (max-width: 640px) {
    .nx-hero h1 { font-size: 2.2rem; }
    .nx-cell { border-left: 0; border-top: 1px solid var(--line); }
    .nx-cell:first-child { border-top: 0; }
    .nx-fact { flex-direction: column; gap: 2px; }
}
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

# =============================================================================
# HELPERS
# =============================================================================
def ollama_ready():
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        return response.ok
    except requests.RequestException:
        return False


def speak_text(text):
    """Speak text locally on Windows using the built-in System.Speech engine.
    Running speech in a short-lived PowerShell process avoids common pyttsx3/
    COM-thread conflicts caused by Streamlit reruns.
    """
    try:
        if os.name != "nt":
            st.warning("Voice output currently requires Windows.")
            return False
        # Base64 protects punctuation, quotes, and Unicode in the spoken text.
        text_b64 = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
        ps_script = (
            "$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + text_b64
            + "')); Add-Type -AssemblyName System.Speech; "
              "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$speaker.Volume = 100; $speaker.Rate = 0; "
              "$sentences = [regex]::Split($text, '(?<=[.!?])\\s+'); "
              "foreach ($sentence in $sentences) { "
              "if (-not [string]::IsNullOrWhiteSpace($sentence)) { "
              "$speaker.Speak($sentence.Trim()) } }; "
              "$speaker.Dispose()"
        )
        encoded_script = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script],
            capture_output=True,
            text=True,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "Windows speech engine returned an error.").strip()
            st.warning(f"Voice output unavailable: {details[:500]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        st.warning("Voice output timed out. Try a shorter reply.")
        return False
    except Exception as error:
        st.warning(f"Voice output unavailable: {type(error).__name__}: {error}")
        return False


def clean_for_speech(text):
    """Remove Markdown symbols and links so they are not read aloud."""
    cleaned = re.sub(r"[`*_#]", "", str(text))
    return re.sub(r"https?://\S+", "", cleaned)


def transcribe_audio(audio_bytes):
    recognizer = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
        recorded_audio = recognizer.record(source)
    return recognizer.recognize_google(recorded_audio)


def navigate_to(destination):
    st.session_state.navigation = destination


def submit_quick_prompt(prompt):
    st.session_state.pending_prompt = prompt


def sync_voice_preference():
    st.session_state.voice_enabled = st.session_state.get("voice_enabled_widget", True)


# ---------- small UI building blocks ----------
def esc(value):
    return html.escape(str(value))


def avatar_for(role):
    return "🧑‍💻" if role == "user" else "🧠"


def page_header(title, caption=None):
    st.title(title)
    if caption:
        st.caption(caption)


def tile_container(key):
    """A container the stylesheet can target; falls back gracefully on older Streamlit."""
    try:
        return st.container(key=key)
    except TypeError:
        return st.container()


def render_status_strip(cells):
    """One connected strip of status cells. Each cell: (label, value, note, state)."""
    parts = []
    for label, value, note, state in cells:
        dot = f'<span class="nx-dot {state}"></span>' if state in ("ok", "bad") else ""
        parts.append(
            '<div class="nx-cell">'
            f'<div class="nx-cell-label">{dot}{esc(label)}</div>'
            f'<div class="nx-cell-value">{esc(value)}</div>'
            f'<div class="nx-cell-note">{esc(note)}</div>'
            '</div>'
        )
    st.markdown('<div class="nx-strip">' + "".join(parts) + "</div>", unsafe_allow_html=True)


def render_memory_item(category, fact):
    st.markdown(
        '<div class="nx-mem">'
        f'<div class="nx-mem-tag">{esc(category)}</div>'
        f'<div class="nx-mem-text">{esc(fact)}</div>'
        '</div>',
        unsafe_allow_html=True
    )


def render_facts(rows):
    body = "".join(
        f'<div class="nx-fact"><div class="nx-fact-k">{esc(k)}</div>'
        f'<div class="nx-fact-v">{v}</div></div>'
        for k, v in rows
    )
    st.markdown(body, unsafe_allow_html=True)


# =============================================================================
# WINDOWS APPLICATION LAUNCHER
# Only applications listed here can be launched by chat commands.
# =============================================================================
APP_ALIASES = {
    "notepad": ["notepad.exe"],
    "firefox": ["firefox"],
    "mozilla firefox": ["firefox"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "file explorer": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "command prompt": ["cmd.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "task manager": ["taskmgr.exe"],
    "settings": ["ms-settings:"],
    "chrome": ["chrome"],
    "google chrome": ["chrome"],
    "edge": ["msedge"],
    "microsoft edge": ["msedge"],
    "vs code": ["code"],
    "visual studio code": ["code"],
    "spotify": ["spotify:"],
    "word": ["winword"],
    "excel": ["excel"],
    "powerpoint": ["powerpnt"]
}


def launch_windows_app(app_name):
    """Launch one recognized Windows application."""
    app_name = app_name.strip().lower()
    if app_name not in APP_ALIASES:
        return False, f"I don't recognize '{app_name}'."
    command = APP_ALIASES[app_name][0]
    try:
        if command.endswith(":"):
            os.startfile(command)
        else:
            app_path = shutil.which(command)
            if app_path:
                subprocess.Popen([app_path])
            else:
                # Windows resolves registered applications such as calc and notepad.
                subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", command],
                    shell=False
                )
        return True, f"Opening {app_name.title()}."
    except Exception as error:
        return False, f"Could not open {app_name}: {error}"


def search_in_firefox(query):
    """Open a Google search in Firefox without executing arbitrary shell commands."""
    firefox_candidates = [
        shutil.which("firefox"),
        os.path.join(
            os.environ.get("PROGRAMFILES", ""),
            "Mozilla Firefox", "firefox.exe"
        ),
        os.path.join(
            os.environ.get("PROGRAMFILES(X86)", ""),
            "Mozilla Firefox", "firefox.exe"
        ),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Mozilla Firefox", "firefox.exe"
        )
    ]
    firefox_exe = next(
        (candidate for candidate in firefox_candidates
         if candidate and os.path.isfile(candidate)),
        None
    )
    if not firefox_exe:
        return False, "I couldn't find Firefox. Please check that it is installed."
    search_url = "https://www.google.com/search?q=" + quote_plus(query)
    try:
        subprocess.Popen([firefox_exe, "-new-tab", search_url])
        return True, f"Opened Firefox search results for: {query}"
    except Exception as error:
        return False, f"Could not open Firefox: {error}"


def handle_firefox_search(prompt):
    """Recognize direct web-search commands intended for Firefox."""
    text = prompt.strip()
    patterns = [
        r"^(?:please\s+)?(?:search(?:\s+for)?|look\s+up|google)\s+(.+?)(?:\s+(?:in|on|using)\s+firefox)?[.!?]*$",
        r"^(?:please\s+)?find\s+(.+?)\s+(?:online|on\s+the\s+internet)[.!?]*$"
    ]
    query = None
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            query = match.group(1).strip()
            break
    if not query:
        return None
    success, message = search_in_firefox(query)
    return ("✅ " if success else "⚠️ ") + message


def spotify_search_and_play(query):
    """Open Spotify desktop search results. User starts the track in Spotify."""
    query = query.strip()
    if not query:
        return False, "Usage: /playmusic song, artist, or playlist name."
    try:
        # Spotify desktop URI opens search results in the installed app.
        os.startfile("spotify:search:" + quote(query, safe=""))
        return True, f"Opened Spotify search for {query}. Choose a result in Spotify to start playback."
    except Exception:
        try:
            # Web fallback still routes to Spotify, not a general search engine.
            import webbrowser
            webbrowser.open("https://open.spotify.com/search/" + quote(query, safe=""))
            return True, f"Opened Spotify search for {query} in your browser. If Spotify is installed, choose Open Spotify."
        except Exception as error:
            return False, f"Could not open Spotify search: {error}"


def spotify_media_key(action):
    """Send Windows media keys; Spotify must be running and may need focus/ownership."""
    if os.name != "nt":
        return False, "Spotify media-key controls are available on Windows only."
    key_codes = {
        "toggle": 0xB3,  # VK_MEDIA_PLAY_PAUSE
        "next": 0xB0,  # VK_MEDIA_NEXT_TRACK
        "previous": 0xB1,  # VK_MEDIA_PREV_TRACK
        "stop": 0xB2  # VK_MEDIA_STOP
    }
    if action not in key_codes:
        return False, "Unsupported Spotify control."
    try:
        user32 = ctypes.windll.user32
        vk = key_codes[action]
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
        labels = {"toggle": "Toggled play/pause", "next": "Skipped to the next track", "previous": "Went to the previous track", "stop": "Sent stop command"}
        return True, labels[action] + ". If another media app is active, it may receive the command instead."
    except Exception as error:
        return False, f"Could not send the media control: {error}"


def run_workflow(workflow_text):
    """Run a small, explicit sequence of approved NEXUS actions."""
    workflow_text = workflow_text.strip()
    if not workflow_text or workflow_text.lower() in ("help", "examples"):
        return (
            "### NEXUS Workflow Automation\n\n"
            "Chain approved actions with the word `then`.\n\n"
            "Examples:\n"
            "- `/workflow launch firefox then search Python automation`\n"
            "- `/workflow launch notepad then launch calculator`\n"
            "- `/workflow search weather forecast then launch spotify`\n\n"
            "Supported steps: `launch APP`, `search QUERY`, `firefox search QUERY`, `email`. "
            "NEXUS runs steps in order. Review each action; it will not execute arbitrary shell commands."
        )
    steps = re.split(r"\s+then\s+", workflow_text, flags=re.IGNORECASE)
    if len(steps) > 6:
        return "A workflow can contain at most 6 steps."
    results = []
    for index, raw_step in enumerate(steps, start=1):
        step = raw_step.strip()
        if not step:
            return "Workflow contains an empty step. Separate actions using `then`."
        match = re.match(r"^launch\s+(.+)$", step, re.IGNORECASE)
        if match:
            ok, message = launch_windows_app(match.group(1).strip())
        elif re.match(r"^firefox\s+search\s+.+$", step, re.IGNORECASE):
            query = re.sub(r"^firefox\s+search\s+", "", step, flags=re.IGNORECASE)
            ok, message = search_in_firefox(query.strip())
        elif re.match(r"^search\s+.+$", step, re.IGNORECASE):
            query = re.sub(r"^search\s+", "", step, flags=re.IGNORECASE)
            ok, message = search_in_firefox(query.strip())
        elif step.lower() == "email":
            st.session_state.pending_navigation = "Computer Tools"
            st.session_state.pending_computer_tools_section = "✉️ Email Assistant"
            ok, message = True, "Opened the Email Assistant section."
        else:
            return (f"I can't run workflow step {index}: `{step}`. "
                    "Use `launch APP`, `search QUERY`, `firefox search QUERY`, or `email`.")
        results.append(("✅" if ok else "⚠️") + f" Step {index}: {message}")
        if not ok:
            results.append("Workflow stopped because this step failed.")
            break
    return "### Workflow results\n\n" + "\n\n".join(results)


def coding_assistant(mode, request_text):
    """Use the local Ollama model for coding help; never execute submitted code."""
    mode_prompts = {
        "code": (
            "Act as a patient programming tutor. Help write the requested code. "
            "State the language, provide a complete runnable example when appropriate, "
            "explain the important parts, and mention assumptions. Do not claim to run code."
        ),
        "debug": (
            "Act as an expert debugging tutor. Inspect the supplied code and error carefully. "
            "Identify the likely cause, explain why it happens, show a corrected version or minimal patch, "
            "and give steps to verify it. If code or the exact error is missing, ask for it. "
            "Never claim to have executed or tested the code."
        ),
        "practice": (
            "Act as a friendly programming instructor. Create a practical coding exercise set "
            "for the requested language or topic. Include difficulty, clear tasks, sample input/output, "
            "and hints. Do not reveal full solutions unless requested. If no topic is given, ask the student to choose one."
        ),
    }
    if not request_text.strip():
        usage = {
            "code": "Usage: `/code build a Python calculator`",
            "debug": "Usage: `/debug paste your code and error message`",
            "practice": "Usage: `/practice Python loops beginner`",
        }
        return usage[mode]
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": mode_prompts[mode] + " Use Markdown. Be accurate and beginner-friendly."},
                    {"role": "user", "content": request_text},
                ],
                "stream": False,
                "options": {"num_ctx": 4096},
            },
            timeout=180,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        return "I can't reach Ollama. Start Ollama and make sure `qwen2.5:7b` is available, then try again."
    except requests.exceptions.Timeout:
        return "The local model took too long. Try a shorter coding request."
    except Exception as error:
        return f"Coding Assistant error: {type(error).__name__}: {error}"


def handle_slash_command(prompt):
    """Route simple slash commands to safe, existing NEXUS features."""
    text = prompt.strip()
    if not text.startswith("/"):
        return None
    command_line = text[1:].strip()
    if not command_line:
        return "Type `/help` to see NEXUS commands."
    parts = command_line.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command in ("help", "commands"):
        return (
            "### NEXUS Slash Commands\n\n"
            "- `/launch firefox` — launch an application\n"
            "- `/launch notepad` — launch Notepad (or another supported app)\n"
            "- `/firefox search Python tutorials` — search in Firefox\n"
            "- `/search artificial intelligence` — search the web in Firefox\n"
            "- `/email` — open the Email Assistant directly inside Computer Tools\n"
            "- `/playmusic song or artist` — search in Spotify\n"
            "- `/workflow launch firefox then search Python` — run actions in sequence\n"
            "- `/code build a Python calculator` — generate and explain code\n"
            "- `/debug paste code and error` — diagnose a programming problem\n"
            "- `/practice Python loops beginner` — generate coding exercises\n"
            "- `/playmusic pause` or `/playmusic resume` — toggle playback\n"
            "- `/playmusic next`, `/playmusic previous`, `/playmusic stop` — control playback\n"
            "- `/help` — show this command list\n\n"
            "You can still use normal chat requests without a slash."
        )
    if command == "launch":
        if not argument:
            return "Usage: `/launch appname` — for example, `/launch firefox`."
        success, message = launch_windows_app(argument)
        return ("✅ " if success else "⚠️ ") + message
    if command == "firefox":
        match = re.match(r"^search\s+(.+)$", argument, re.IGNORECASE)
        if not match:
            return "Usage: `/firefox search what you want to find`."
        success, message = search_in_firefox(match.group(1).strip())
        return ("✅ " if success else "⚠️ ") + message
    if command == "search":
        if not argument:
            return "Usage: `/search what you want to find`."
        success, message = search_in_firefox(argument)
        return ("✅ " if success else "⚠️ ") + message
    if command == "email":
        st.session_state.pending_navigation = "Computer Tools"
        st.session_state.pending_computer_tools_section = "✉️ Email Assistant"
        return (
            "✉️ Opening the Email Assistant in Computer Tools. "
            "Review your draft before sending it through Gmail."
        )
    if command == "workflow":
        return run_workflow(argument)
    if command in ("code", "debug", "practice"):
        return coding_assistant(command, argument)
    if command == "playmusic":
        if not argument:
            return "Usage: `/playmusic song, artist, or playlist` — or use pause, resume, next, previous, or stop."
        control = argument.lower().strip()
        if control in ("pause", "resume", "play", "toggle", "pause/resume"):
            success, message = spotify_media_key("toggle")
            if control == "resume":
                message = "Sent Spotify play/pause toggle to resume playback. " + message.split(". ", 1)[-1]
            elif control == "pause":
                message = "Sent Spotify play/pause toggle to pause playback. " + message.split(". ", 1)[-1]
        elif control in ("next", "skip"):
            success, message = spotify_media_key("next")
        elif control in ("previous", "prev", "back"):
            success, message = spotify_media_key("previous")
        elif control == "stop":
            success, message = spotify_media_key("stop")
        else:
            success, message = spotify_search_and_play(argument)
        return ("✅ " if success else "⚠️ ") + message
    return (f"I don't recognize `/{command}`. Type `/help` to see available commands.")


def handle_app_command(prompt):
    """Handle 'open Chrome' or 'open Chrome and Notepad' commands.

    If none of the requested names is a supported application (for example
    "open a discussion about..."), the message falls through to the AI model.
    """
    text = prompt.strip().lower()
    match = re.match(
        r"^(?:please\s+)?(?:open|launch|start)\s+(.+?)\s*[.!?]*$",
        text
    )
    if not match:
        return None
    requested = match.group(1)
    app_names = [
        name.strip(" .!?")
        for name in re.split(r"\s*(?:,|\band\b|\bplus\b)\s*", requested)
    ]
    app_names = [name for name in app_names if name]
    if not app_names:
        return "Please tell me which application to open."
    if not any(name in APP_ALIASES for name in app_names):
        return None
    output = ["### NEXUS App Launcher"]
    for app_name in app_names:
        success, message = launch_windows_app(app_name)
        output.append(f"{'✅' if success else '⚠️'} {message}")
    return "\n\n".join(output)


# =============================================================================
# WINDOWS WINDOW MULTITASKING
# =============================================================================
def get_open_windows():
    """Return visible Windows application windows and their handles."""
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    windows = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        title = title_buffer.value.strip()
        if title:
            windows.append({"hwnd": hwnd, "title": title})
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def find_window(search_term):
    term = search_term.strip().lower()
    windows = get_open_windows()
    for window in windows:
        if window["title"].lower() == term:
            return window
    for window in windows:
        if term in window["title"].lower():
            return window
    aliases = {
        "firefox": "mozilla firefox",
        "chrome": "google chrome",
        "edge": "microsoft edge",
        "vs code": "visual studio code"
    }
    alias = aliases.get(term)
    if alias:
        for window in windows:
            if alias in window["title"].lower():
                return window
    return None


def control_window(action, app_name):
    if os.name != "nt":
        return False, "Window controls are available on Windows only."
    window = find_window(app_name)
    if not window:
        return False, f"I couldn't find an open window matching '{app_name}'."
    user32 = ctypes.windll.user32
    hwnd = window["hwnd"]
    try:
        if action == "minimize":
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        elif action == "maximize":
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            user32.SetForegroundWindow(hwnd)
        else:  # switch / restore
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return True, f"{action.title()}d {window['title']}." if action == "minimize" else f"{action.title()}ed {window['title']}."
    except Exception as error:
        return False, f"Window operation failed: {error}"


def minimize_all_windows():
    if os.name != "nt":
        return False, "This feature is available on Windows only."
    windows = get_open_windows()
    for window in windows:
        ctypes.windll.user32.ShowWindow(window["hwnd"], 6)
    return True, f"Minimized {len(windows)} visible windows."


def arrange_two_windows(first_name, second_name):
    if os.name != "nt":
        return False, "Window arrangement is available on Windows only."
    first = find_window(first_name)
    second = find_window(second_name)
    if not first:
        return False, f"Couldn't find an open window matching '{first_name}'."
    if not second:
        return False, f"Couldn't find an open window matching '{second_name}'."
    if first["hwnd"] == second["hwnd"]:
        return False, "Please choose two different applications."
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1) - 48
    margin = 8
    half = (width - 3 * margin) // 2
    user32.ShowWindow(first["hwnd"], 9)
    user32.ShowWindow(second["hwnd"], 9)
    user32.MoveWindow(first["hwnd"], margin, margin, half, height - margin, True)
    user32.MoveWindow(second["hwnd"], 2 * margin + half, margin, half, height - margin, True)
    user32.SetForegroundWindow(first["hwnd"])
    return True, f"Arranged {first['title']} and {second['title']} side by side."


def handle_window_command(prompt):
    text = prompt.strip()
    if re.fullmatch(r"(?:please\s+)?(?:minimize|hide)\s+all\s+(?:windows|apps|applications)[.!?]*", text, re.I):
        ok, message = minimize_all_windows()
        return ("✅ " if ok else "⚠️ ") + message
    match = re.match(
        r"^(?:please\s+)?(?:arrange|tile)\s+(.+?)\s+and\s+(.+?)\s+side\s+by\s+side[.!?]*$",
        text, re.I
    )
    if match:
        ok, message = arrange_two_windows(match.group(1), match.group(2))
        return ("✅ " if ok else "⚠️ ") + message
    match = re.match(
        r"^(?:please\s+)?(switch\s+to|focus|activate|bring\s+to\s+front|minimize|maximize|restore)\s+(.+?)[.!?]*$",
        text, re.I
    )
    if match:
        action_text = match.group(1).lower()
        action = "switch" if action_text in (
            "switch to", "focus", "activate", "bring to front"
        ) else action_text
        ok, message = control_window(action, match.group(2))
        return ("✅ " if ok else "⚠️ ") + message
    return None


# =============================================================================
# CHAT ENGINE
# =============================================================================
def reply_and_rerun(text, spoken, speak_replies):
    """Save an assistant reply, show it right away, speak it, then refresh the page."""
    add_message_to_chat("assistant", text)
    with st.chat_message("assistant", avatar=avatar_for("assistant")):
        st.markdown(text)
    if speak_replies and spoken:
        with st.spinner("NEXUS is speaking..."):
            speak_text(spoken)
    st.rerun()


def extract_reminder_candidate(prompt):
    """Ask local Qwen to extract a reminder; never save until the user confirms."""
    now = datetime.now()
    system_prompt = (
        "Extract a single reminder/task from the user's message. "
        "Return ONLY valid JSON with keys: is_reminder (boolean), title (string), "
        "date (YYYY-MM-DD string or null), time (HH:MM 24-hour string or null), "
        "notes (string). Interpret relative dates using this current local date and time: "
        f"{now.strftime('%Y-%m-%d %H:%M')}. If no reminder/task intent exists, set is_reminder false. "
        "Do not invent a date or time. If the user mentions a reminder but omits date or time, "
        "return null for the missing field. No Markdown fences."
    )
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_ctx": 2048}
            },
            timeout=90
        )
        response.raise_for_status()
        data = response.json()["message"]["content"]
        candidate = __import__("json").loads(data)
        if not candidate.get("is_reminder"):
            return None
        title = str(candidate.get("title") or "").strip()
        date_text = candidate.get("date")
        time_text = candidate.get("time")
        if not title or not date_text or not time_text:
            return {"incomplete": True, "title": title or prompt, "date": date_text, "time": time_text}
        parsed_date = datetime.strptime(str(date_text), "%Y-%m-%d").date()
        parsed_time = datetime.strptime(str(time_text), "%H:%M").time()
        if datetime.combine(parsed_date, parsed_time) <= now:
            return {"incomplete": True, "title": title, "date": date_text, "time": time_text,
                    "error": "That date and time are in the past. Please clarify the intended schedule."}
        return {
            "incomplete": False, "title": title, "date": parsed_date.isoformat(),
            "time": parsed_time.strftime("%H:%M"),
            "notes": str(candidate.get("notes") or "")
        }
    except Exception:
        return None


def process_prompt(prompt, speak_replies):
    """Route one user prompt: commands first, then memory saving, then the AI model."""
    add_message_to_chat("user", prompt)
    with st.chat_message("user", avatar=avatar_for("user")):
        st.markdown(prompt)

    # Slash commands are routed before natural-language command handling.
    slash_result = handle_slash_command(prompt)
    if slash_result:
        reply_and_rerun(slash_result, clean_for_speech(slash_result)[:500], speak_replies)

    # Windows window-management commands.
    window_result = handle_window_command(prompt)
    if window_result:
        reply_and_rerun(window_result, clean_for_speech(window_result)[:500], speak_replies)

    # Direct Firefox searches.
    search_result = handle_firefox_search(prompt)
    if search_result:
        reply_and_rerun(search_result, "I opened your search in Firefox.", speak_replies)

    # App-launch commands.
    app_result = handle_app_command(prompt)
    if app_result:
        reply_and_rerun(app_result, "Application launch request processed.", speak_replies)

    lower_prompt = prompt.strip().lower()
    memory_prefixes = [
        "remember that ",
        "remember: ",
        "save this: ",
        "save this information: "
    ]
    matched_prefix = next(
        (prefix for prefix in memory_prefixes if lower_prompt.startswith(prefix)),
        None
    )
    if matched_prefix:
        fact = prompt.strip()[len(matched_prefix):].strip()
        if not fact:
            st.warning("Please tell me what to remember.")
            return
        try:
            add_memory("Other", fact)
        except Exception as error:
            st.error(f"Memory save failed: {error}")
            return
        confirmation = (
            "I've saved this information to your Personal Wiki:\n\n"
            f"**{fact}**\n\n"
            "I'll use it when relevant in future conversations."
        )
        reply_and_rerun(confirmation, "I've saved that information to your Personal Wiki.", speak_replies)

    # Detect likely reminder requests and ask the user to confirm extracted details.
    reminder_intent = re.search(
        r"\b(remind me|reminder|schedule|schedule a|meeting|appointment|deadline|class|exam|submit|"
        r"tomorrow|today|tonight|next week|\d{1,2}(:\d{2})?\s*(am|pm))\b",
        prompt, re.IGNORECASE
    )
    if reminder_intent:
        with st.spinner("Checking whether this is a reminder and extracting its date/time..."):
            candidate = extract_reminder_candidate(prompt)
        if candidate and not candidate.get("incomplete"):
            st.session_state.pending_task_candidate = candidate
            reply_and_rerun(
                "I think this is a reminder. I've prepared the details for your review below. "
                "I will save it only after you confirm.",
                "I prepared a reminder for your review.",
                speak_replies
            )
        elif candidate and candidate.get("incomplete"):
            reply_and_rerun(
                "I can help save this as a reminder, but I couldn't identify a complete future date and time. "
                "Please include both, for example: 'Remind me to meet Rahul tomorrow at 11 AM.'",
                "Please provide the date and time for your reminder.",
                speak_replies
            )

    # Everything else goes to the local model with relevant memories as context.
    related = search_memories(prompt, limit=8)
    memory_context = "\n".join([
        f"- [{item['category']}] {item['fact']}" for item in related
    ])
    if not memory_context:
        memory_context = "No directly matching saved memories."
    system = (
        "You are NEXUS, a helpful personal AI assistant. "
        "Be friendly, intelligent and concise. "
        "Be honest about what you can and cannot do. "
        "Never claim to have performed an action unless "
        "the application confirms it. "
        "Use saved personal memories when relevant. "
        "Do not invent personal facts. "
        "Reply in the language the user uses.\n\n"
        f"PERSONAL WIKI:\n{memory_context}"
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(st.session_state.messages[-12:])
    try:
        with st.spinner("NEXUS is thinking locally..."):
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_ctx": 4096}
                },
                timeout=180
            )
            response.raise_for_status()
            answer = response.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to Ollama.")
        return
    except requests.exceptions.Timeout:
        st.error("The model took too long. Try a shorter question.")
        return
    except Exception as error:
        st.error(f"AI request failed: {type(error).__name__}: {error}")
        return
    reply_and_rerun(answer, clean_for_speech(answer), speak_replies)


# =============================================================================
# SIDEBAR
# =============================================================================
def render_sidebar():
    with st.sidebar:
        st.markdown(
            '<div class="nx-brand-wrap"><div class="nx-logo">N</div>'
            '<div><div class="nx-brand">NEXUS</div>'
            '<div class="nx-brand-sub">Personal AI workspace</div></div></div>',
            unsafe_allow_html=True
        )
        st.divider()
        for name in NAV_PAGES:
            st.button(
                f"{NAV_ICONS.get(name, '')}  {name}",
                key=f"nav_{name}",
                use_container_width=True,
                type="primary" if st.session_state.navigation == name else "secondary",
                on_click=navigate_to,
                args=(name,)
            )
        page = st.session_state.navigation

        if page == "AI Chat":
            st.divider()
            st.markdown('<div class="nx-side-label">Chat history</div>', unsafe_allow_html=True)
            if st.button("＋ New chat", key="history_new_chat", use_container_width=True):
                begin_new_chat()
                st.rerun()
            saved_chats = get_conversations()
            if not saved_chats:
                st.caption("Your saved conversations will appear here.")
            for saved_chat in saved_chats:
                hcol1, hcol2 = st.columns([5, 1])
                with hcol1:
                    if st.button(
                        saved_chat["title"],
                        key=f"open_chat_{saved_chat['id']}",
                        use_container_width=True
                    ):
                        open_saved_chat(saved_chat["id"])
                        st.rerun()
                with hcol2:
                    if st.button(
                        "✕",
                        key=f"delete_chat_{saved_chat['id']}",
                        help="Delete this saved conversation"
                    ):
                        delete_saved_chat(saved_chat["id"])
                        st.rerun()

        st.divider()
        st.markdown('<div class="nx-side-label">Local intelligence</div>', unsafe_allow_html=True)
        if ollama_ready():
            engine_line = '<span class="nx-dot ok"></span>AI engine online'
        else:
            engine_line = '<span class="nx-dot bad"></span>AI engine offline'
        st.markdown(
            '<div class="nx-side-card">'
            f'<div class="nx-engine">{engine_line}</div>'
            f'<div class="nx-side-meta">Model: {esc(MODEL)}<br>Memory: local SQLite</div>'
            '</div>',
            unsafe_allow_html=True
        )
    return page


# =============================================================================
# PAGES
# =============================================================================
def greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning."
    if hour < 18:
        return "Good afternoon."
    return "Good evening."


def render_dashboard():
    memories = list_memories()
    files = list_workspace_files()
    ai_online = ollama_ready()

    st.markdown(
        '<div class="nx-hero">'
        f'<div class="nx-hero-hello">{esc(greeting())}</div>'
        '<h1>What are we working on?</h1>'
        f'<p>Everything here runs on this computer. The local model {esc(MODEL)} is '
        f'{"online and ready to talk" if ai_online else "offline, so start Ollama to chat"}.</p>'
        '</div>',
        unsafe_allow_html=True
    )

    render_status_strip([
        ("Local AI engine", "Online" if ai_online else "Offline", "Qwen 2.5 7B via Ollama",
         "ok" if ai_online else "bad"),
        ("Personal memory", str(len(memories)), "Saved wiki records", ""),
        ("Voice engine", "Ready", "Microphone and speech output", ""),
        ("Generated files", str(len(files)), "In the NEXUS workspace", ""),
    ])

    st.markdown('<div class="nx-section">Your workspace</div>', unsafe_allow_html=True)
    st.markdown('<div class="nx-section-note">Choose where to continue.</div>', unsafe_allow_html=True)

    destinations = [
        ("tile_chat", "dash_chat", "AI Chat", "💬", "AI conversation",
         "Talk to your local assistant using text or your microphone.", "Open chat"),
        ("tile_wiki", "dash_wiki", "Personal Wiki", "📚", "Personal wiki",
         "Store, review and manage your personal knowledge and memories.", "Open wiki"),
        ("tile_tools", "dash_tools", "Computer Tools", "🧰", "Computer tools",
         "Launch apps, arrange windows, draft emails and create files.", "Open tools"),
        ("tile_status", "dash_status", "System Status", "📊", "System status",
         "Check the AI engine, model and local workspace.", "View status"),
        ("tile_plans", "dash_plans", "My Plans & Reminders", "📅", "Plans and reminders",
         "Save meetings, deadlines and upcoming tasks in local SQLite.", "Open plans"),
    ]
    for row_start in range(0, len(destinations), 2):
        columns_row = st.columns(2)
        for column, (tile_key, key, target, icon, title, text, button_label) in zip(
                columns_row, destinations[row_start:row_start + 2]):
            with column:
                with tile_container(tile_key):
                    st.markdown(
                        f'<div class="nx-tile-icon">{icon}</div>'
                        f'<div class="nx-tile-title">{esc(title)}</div>'
                        f'<div class="nx-tile-text">{esc(text)}</div>',
                        unsafe_allow_html=True
                    )
                    st.button(button_label, key=key, use_container_width=True,
                              on_click=navigate_to, args=(target,))

    st.markdown('<div class="nx-section">Recent personal memories</div>', unsafe_allow_html=True)
    if memories:
        for item in memories[-3:][::-1]:
            render_memory_item(item["category"], item["fact"])
    else:
        st.info("Your Personal Wiki is empty. Add your first memory to get started.")
    st.caption(f"Workspace initialized • {datetime.now().strftime('%d %b %Y, %H:%M')}")


def render_chat():
    page_header("Talk to NEXUS", "Your local assistant with voice input and long-term memory.")

    voice_col, mic_col, new_col = st.columns([3, 2, 2])
    with voice_col:
        speak_replies = st.checkbox(
            "🔊 Enable NEXUS voice",
            value=st.session_state.voice_enabled,
            key="voice_enabled_widget",
            on_change=sync_voice_preference,
            help="NEXUS will speak chat replies and action confirmations."
        )
    with mic_col:
        audio = mic_recorder(
            start_prompt="🎙️ Speak",
            stop_prompt="⏹️ Stop",
            key="nexus_microphone",
            format="wav",
            use_container_width=True
        )
    with new_col:
        if st.button("＋ New chat", key="new_chat_main", use_container_width=True):
            begin_new_chat()
            st.rerun()

    if not ollama_ready():
        st.error("Ollama is offline. Open Ollama from the Windows Start menu.")

    if not st.session_state.messages:
        st.markdown(
            """
            <div class="nx-welcome">
                <h2>What would you like to work on today?</h2>
                <p>Ideas, questions, commands and personal knowledge, all processed locally.
                Type /help in the message box to see every command.</p>
            </div>
            """,
            unsafe_allow_html=True
        )
        quick_prompts = [
            ("💡 Explain a concept", "Explain a difficult concept in simple terms, with an example."),
            ("📅 Plan my day", "Help me create a practical plan for my day."),
            ("🧠 Use my memories", "What relevant things do you remember about me?"),
            ("✨ Brainstorm ideas", "Help me brainstorm some creative ideas.")
        ]
        quick_cols = st.columns(2)
        for index, (label, prompt_text) in enumerate(quick_prompts):
            with quick_cols[index % 2]:
                st.button(
                    label,
                    key=f"quick_{label}",
                    use_container_width=True,
                    on_click=submit_quick_prompt,
                    args=(prompt_text,)
                )

    for message in st.session_state.messages:
        with st.chat_message(message["role"], avatar=avatar_for(message["role"])):
            st.markdown(message["content"])

    # User-confirmed natural-language reminder preview.
    candidate = st.session_state.get("pending_task_candidate")
    if candidate:
        st.divider()
        st.subheader("Review reminder before saving")
        st.write(f"**Task:** {candidate['title']}")
        st.write(f"**Date:** {candidate['date']}")
        st.write(f"**Time:** {candidate['time']}")
        if candidate.get("notes"):
            st.caption(candidate["notes"])
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("Confirm and save reminder", key="confirm_nl_reminder", type="primary"):
                add_task(candidate["title"], candidate["date"], candidate["time"], candidate.get("notes", ""))
                st.session_state.pending_task_candidate = None
                st.success("Reminder saved to My Plans & Reminders.")
                st.rerun()
        with cancel_col:
            if st.button("Cancel", key="cancel_nl_reminder"):
                st.session_state.pending_task_candidate = None
                st.rerun()

    # The message box is pinned to the bottom of the page by Streamlit.
    typed = st.chat_input("Message NEXUS, or try /launch firefox, /email, /search AI news")
    if typed and typed.strip():
        st.session_state.pending_prompt = typed.strip()

    if audio and audio.get("id") != st.session_state.last_audio_id:
        st.session_state.last_audio_id = audio.get("id")
        transcript = None
        try:
            with st.spinner("Understanding your voice..."):
                transcript = transcribe_audio(audio["bytes"])
        except sr.UnknownValueError:
            st.warning("I couldn't understand you. Please try again.")
        except sr.RequestError as error:
            st.error(f"Speech recognition service error: {error}")
        except Exception as error:
            st.error(f"Microphone error: {error}")
        if transcript:
            st.session_state.pending_prompt = transcript
            st.rerun()

    prompt = st.session_state.pending_prompt
    if prompt:
        st.session_state.pending_prompt = None
        process_prompt(prompt, speak_replies)


def render_wiki():
    page_header("Personal wiki", "Your personal information stays in your local SQLite database.")

    with st.form("add_memory", clear_on_submit=True):
        category = st.selectbox(
            "Category",
            ["About me", "Education", "Work", "Goals", "Preferences", "Projects", "Other"]
        )
        fact = st.text_area(
            "What should NEXUS remember?",
            placeholder="Tell NEXUS something important about you..."
        )
        submitted = st.form_submit_button("Save to my Wiki", type="primary")
        if submitted:
            if fact.strip():
                add_memory(category, fact.strip())
                st.success("Memory saved successfully.")
                st.rerun()
            else:
                st.warning("Please enter information first.")

    st.divider()
    memories = list_memories()
    st.subheader(f"Saved memories ({len(memories)})")
    if not memories:
        st.info("No memories saved yet.")
    for item in memories:
        left, right = st.columns([6, 1])
        with left:
            render_memory_item(item["category"], item["fact"])
        with right:
            if st.button("Delete", key=f"delete_{item['id']}", use_container_width=True):
                remove_memory(item["id"])
                st.rerun()


def render_apps_and_windows():
    st.subheader("Launch a Windows application")
    launcher_options = [
        "Notepad", "Calculator", "Paint", "File Explorer",
        "Firefox", "Chrome", "Edge", "VS Code", "Spotify",
        "Command Prompt", "PowerShell", "Task Manager", "Settings"
    ]
    selected_app = st.selectbox("Choose an application", launcher_options)
    if st.button("Launch application", key="tools_launch_app", type="primary"):
        success, message = launch_windows_app(selected_app)
        if success:
            st.success(message)
        else:
            st.error(message)
    st.caption("You can also launch applications by chatting: "
               "'Open Firefox and Notepad'.")

    st.divider()
    st.subheader("Open windows")
    if st.button("Refresh open windows", key="refresh_open_windows"):
        st.rerun()
    open_windows = get_open_windows()
    if open_windows:
        for item in open_windows[:25]:
            st.write(f"🪟 {item['title']}")
    else:
        st.info("No visible titled windows found.")
    st.caption(
        "Chat commands: 'Switch to Firefox', 'Minimize Notepad', "
        "'Maximize VS Code', 'Minimize all windows', "
        "'Arrange Firefox and Notepad side by side'."
    )


def render_email_assistant():
    st.subheader("✉️ NEXUS Email Assistant")
    st.caption(
        "Create a professional email with your local Qwen model, review it, "
        "then open a pre-filled Gmail compose window. NEXUS will not send it automatically."
    )
    st.info(
        "Quick Gmail mode: NEXUS prepares the draft and opens Gmail with the "
        "recipient, subject and message filled in. You review and press Send in Gmail."
    )

    with st.form("nexus_email_draft_form"):
        recipient = st.text_input(
            "Recipient email address",
            value=st.session_state.email_recipient,
            placeholder="name@example.com"
        )
        purpose = st.text_area(
            "What should the email say?",
            placeholder="Example: Ask my professor for an extension on the project deadline.",
            height=130
        )
        tone = st.selectbox(
            "Writing tone",
            ["Professional", "Friendly", "Formal", "Short and direct", "Polite"]
        )
        generate_email = st.form_submit_button(
            "✨ Generate email draft", use_container_width=True, type="primary"
        )

    if generate_email:
        if not recipient.strip() or not re.fullmatch(EMAIL_PATTERN, recipient.strip()):
            st.error("Please enter a valid recipient email address.")
        elif not purpose.strip():
            st.error("Please describe what the email should communicate.")
        else:
            email_prompt = (
                "Write an email based on the user's instructions. "
                "Do not invent facts, dates, attachments, promises, or personal details. "
                "Use placeholders such as [date] if needed. "
                "Return exactly this format, with no Markdown fences:\n"
                "Subject: a concise subject line\n"
                "Body:\n"
                "the complete email body\n\n"
                f"Tone: {tone}\n"
                f"Recipient: {recipient.strip()}\n"
                f"User instructions: {purpose.strip()}"
            )
            try:
                with st.spinner("NEXUS is drafting your email locally..."):
                    response = requests.post(
                        OLLAMA_URL,
                        json={
                            "model": MODEL,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "You are NEXUS Email Assistant. Write clear, useful "
                                        "emails. Never fabricate details. Follow the requested "
                                        "output format exactly."
                                    )
                                },
                                {"role": "user", "content": email_prompt}
                            ],
                            "stream": False,
                            "options": {"num_ctx": 4096}
                        },
                        timeout=180
                    )
                    response.raise_for_status()
                    generated = response.json()["message"]["content"].strip()
                subject_match = re.search(
                    r"(?im)^Subject:\s*(.+)$", generated
                )
                body_match = re.search(
                    r"(?is)^.*?Body:\s*(.*)$", generated
                )
                if subject_match:
                    st.session_state.email_subject = subject_match.group(1).strip()
                    st.session_state.email_body = (
                        body_match.group(1).strip() if body_match else generated
                    )
                else:
                    st.session_state.email_subject = "Email from NEXUS"
                    st.session_state.email_body = generated
                st.session_state.email_recipient = recipient.strip()
                st.success("Your email draft is ready. Review and edit it below.")
            except requests.exceptions.ConnectionError:
                st.error("Cannot connect to Ollama. Please make sure Ollama is running.")
            except requests.exceptions.Timeout:
                st.error("The model took too long. Please try a shorter instruction.")
            except Exception as error:
                st.error(f"Email generation failed: {type(error).__name__}: {error}")

    if st.session_state.email_body:
        st.divider()
        st.subheader("Review and edit your email")
        st.text_input(
            "To",
            key="email_recipient",
            help="You can change the recipient before opening Gmail."
        )
        st.text_input("Subject", key="email_subject")
        st.text_area(
            "Email body",
            key="email_body",
            height=260
        )
        recipient_value = st.session_state.email_recipient.strip()
        subject_value = st.session_state.email_subject
        body_value = st.session_state.email_body
        if recipient_value and re.fullmatch(EMAIL_PATTERN, recipient_value):
            gmail_params = urlencode({
                "view": "cm",
                "fs": "1",
                "to": recipient_value,
                "su": subject_value,
                "body": body_value
            })
            gmail_compose_url = "https://mail.google.com/mail/?" + gmail_params
            st.link_button(
                "Open pre-filled draft in Gmail ↗",
                gmail_compose_url,
                use_container_width=True
            )
            st.caption(
                "Gmail opens in your browser. Check the recipient and content, "
                "then press Send yourself."
            )
        else:
            st.warning("Enter a valid recipient address to create the Gmail draft link.")


def render_files_and_websites():
    st.subheader("Create a text file")
    with st.form("create_file"):
        filename = st.text_input("File name", "my_goals.txt")
        content = st.text_area("File content", "My goal is to become an AI engineer.")
        create = st.form_submit_button("Create file", type="primary")
        if create:
            try:
                created_path = create_note(filename, content)
                st.success(f"Successfully created: {created_path}")
            except Exception as error:
                st.error(str(error))

    st.divider()
    st.subheader("Ask NEXUS a question and save the answer as a TXT file")
    st.caption("Write a question or topic. Your local Qwen model will generate an answer and NEXUS will save it as a .txt file in your workspace.")
    with st.form("question_to_txt"):
        question = st.text_area(
            "Your question",
            placeholder="Example: Explain Python functions with simple examples.",
            height=120
        )
        answer_filename = st.text_input("Output file name", "nexus_answer.txt")
        generate_txt = st.form_submit_button("Generate answer and create TXT", type="primary")
        if generate_txt:
            if not question.strip():
                st.warning("Please enter a question first.")
            elif not ollama_ready():
                st.error("Ollama is not running. Start Ollama and try again.")
            else:
                try:
                    with st.spinner("NEXUS is generating your answer with the local AI model..."):
                        response = requests.post(
                            OLLAMA_URL,
                            json={
                                "model": MODEL,
                                "messages": [
                                    {"role": "system", "content": "Answer the user's question clearly, accurately, and helpfully. Use plain text formatting suitable for a TXT file. If the answer is uncertain, say so."},
                                    {"role": "user", "content": question.strip()}
                                ],
                                "stream": False,
                                "options": {"num_ctx": 4096}
                            },
                            timeout=180
                        )
                        response.raise_for_status()
                        generated_answer = response.json()["message"]["content"].strip()
                    file_content = (
                        "NEXUS - AI GENERATED ANSWER\\n"
                        "========================================\\n\\n"
                        f"Question: {question.strip()}\\n\\n"
                        "Answer:\\n"
                        f"{generated_answer}\\n"
                    )
                    created_path = create_note(answer_filename, file_content)
                    st.session_state["last_generated_txt_answer"] = generated_answer
                    st.session_state["last_generated_txt_path"] = str(created_path)
                    st.success(f"Answer generated and saved: {created_path}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to Ollama. Make sure Ollama is running.")
                except requests.exceptions.Timeout:
                    st.error("The AI model took too long. Try a shorter question.")
                except Exception as error:
                    st.error(f"Could not generate the TXT file: {type(error).__name__}: {error}")

    if st.session_state.get("last_generated_txt_answer"):
        st.markdown("**Latest generated answer**")
        st.write(st.session_state["last_generated_txt_answer"])
        st.caption(f"Saved file: {st.session_state.get('last_generated_txt_path', '')}")

    st.divider()
    st.subheader("Open an approved website")
    choice = st.selectbox("Choose website", ["Google", "GitHub", "YouTube"])
    urls = {
        "Google": "https://www.google.com",
        "GitHub": "https://github.com",
        "YouTube": "https://www.youtube.com"
    }
    if st.button("Open website"):
        st.success(open_approved_website(urls[choice]))

    st.divider()
    st.subheader("Files created by NEXUS")
    files = list_workspace_files()
    if files:
        for file in files:
            st.write(f"📄 {file.name}")
    else:
        st.info("No generated files yet.")


def render_computer_tools():
    page_header("Computer tools", "Your control center for applications, windows, email and files.")
    tools_section = st.radio(
        "Computer Tools section",
        TOOLS_SECTIONS,
        key="computer_tools_section",
        horizontal=True,
        label_visibility="collapsed"
    )
    st.write("")
    if tools_section == "🖥️ Apps & Windows":
        render_apps_and_windows()
    elif tools_section == "✉️ Email Assistant":
        render_email_assistant()
    elif tools_section == "📁 Files & Websites":
        render_files_and_websites()


def render_system_status():
    page_header("System status", "Live checks for the local AI engine, memory and workspace.")
    ai_online = ollama_ready()
    memories = list_memories()
    files = list_workspace_files()

    render_status_strip([
        ("Ollama AI engine", "Online" if ai_online else "Offline", MODEL,
         "ok" if ai_online else "bad"),
        ("Personal wiki", "Ready", f"{len(memories)} saved memories", "ok"),
    ])

    render_facts([
        ("AI model", f"<code>{esc(MODEL)}</code>"),
        ("Workspace", f"<code>{esc(WORKSPACE)}</code>"),
        ("Saved memories", esc(len(memories))),
        ("Generated files", esc(len(files))),
        ("AI privacy", "Prompts are processed by your local Ollama service."),
        ("Memory privacy", "Personal Wiki is stored in your local SQLite database."),
        ("Voice output", "Offline Windows speech engine."),
        ("Voice input", "Google speech recognition service (internet required)."),
    ])
    st.write("")
    if st.button("Check installed Ollama models"):
        try:
            data = requests.get("http://localhost:11434/api/tags", timeout=5).json()
            names = [model["name"] for model in data.get("models", [])]
            st.write(names if names else "No models installed.")
        except Exception:
            st.error("Ollama is not reachable.")




# =============================================================================
# ATTENDANCE CHECKER (manual verification MVP)
# =============================================================================
ATTENDANCE_DB = "nexus_attendance.db"


def init_attendance_db():
    with sqlite3.connect(ATTENDANCE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_date TEXT NOT NULL,
                subject TEXT NOT NULL,
                class_time TEXT DEFAULT '',
                faculty TEXT DEFAULT '',
                portal_status TEXT NOT NULL,
                student_attended INTEGER NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)


def save_attendance_check(class_date, subject, class_time, faculty,
                          portal_status, student_attended, notes=""):
    with sqlite3.connect(ATTENDANCE_DB) as conn:
        conn.execute("""
            INSERT INTO attendance_checks
            (class_date, subject, class_time, faculty, portal_status,
             student_attended, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            class_date, subject.strip(), class_time.strip(), faculty.strip(),
            portal_status, int(student_attended), notes.strip(),
            datetime.now().isoformat(timespec="seconds")
        ))


def get_attendance_checks():
    with sqlite3.connect(ATTENDANCE_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM attendance_checks ORDER BY class_date DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def delete_attendance_check(check_id):
    with sqlite3.connect(ATTENDANCE_DB) as conn:
        conn.execute("DELETE FROM attendance_checks WHERE id = ?", (check_id,))


init_attendance_db()

# Save the user's explicitly confirmed first example once.
with sqlite3.connect(ATTENDANCE_DB) as _attendance_conn:
    _existing_example = _attendance_conn.execute(
        "SELECT id FROM attendance_checks WHERE class_date=? AND subject=? AND class_time=?",
        ("2026-09-23", "QALR (T)", "2:20 PM – 3:05 PM")
    ).fetchone()
    if not _existing_example:
        _attendance_conn.execute("""
            INSERT INTO attendance_checks
            (class_date, subject, class_time, faculty, portal_status,
             student_attended, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "2026-09-23", "QALR (T)", "2:20 PM – 3:05 PM", "VS",
            "Absent", 1,
            "User confirmed they attended, but the college portal showed Absent.",
            datetime.now().isoformat(timespec="seconds")
        ))



def render_attendance_checker():
    page_header(
        "Attendance checker",
        "Compare what you attended with what the CGC portal recorded. Your college login is never stored here."
    )

    records = get_attendance_checks()
    mismatches = [
        r for r in records
        if r["student_attended"] == 1 and r["portal_status"] == "Absent"
    ]
    c1, c2, c3 = st.columns(3)
    c1.metric("Checks saved", len(records))
    c2.metric("Possible errors", len(mismatches))
    c3.metric("Confirmed present", sum(
        1 for r in records if r["portal_status"] == "Present"
    ))

    st.info(
        "For this first version, you enter the portal status and confirm whether you attended. "
        "Automatic portal reading is not enabled yet."
    )

    st.subheader("Record a class attendance check")
    with st.form("nexus_attendance_check", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            class_date = st.date_input("Lecture date")
            subject = st.text_input("Subject", placeholder="QALR (T)")
            class_time = st.text_input("Class time", placeholder="2:20 PM – 3:05 PM")
        with col2:
            faculty = st.text_input("Faculty initials (optional)", placeholder="VS")
            portal_status = st.selectbox("Status shown on college portal", ["Present", "Absent", "Not marked"])
            student_attended = st.radio(
                "Did you attend this class?",
                ["Yes, I attended", "No, I did not attend"],
                horizontal=False
            )
        notes = st.text_area("Notes (optional)", placeholder="e.g. I was in class; may need to contact faculty.")
        save = st.form_submit_button("Save attendance check", type="primary")
        if save:
            if not subject.strip():
                st.error("Please enter the subject.")
            else:
                save_attendance_check(
                    class_date.isoformat(), subject, class_time, faculty,
                    portal_status, student_attended == "Yes, I attended", notes
                )
                st.success("Attendance check saved.")
                st.rerun()

    st.divider()
    st.subheader("Possible attendance errors")
    if not mismatches:
        st.success("No possible attendance errors recorded yet.")
    for record in mismatches:
        with st.container(border=True):
            st.markdown(f"**{esc(record['subject'])}** — {record['class_date']}")
            st.markdown(
                f"Your confirmation: **Attended** · Portal status: "
                f"**:red[Absent]**"
            )
            details = " · ".join(x for x in [
                record["class_time"], f"Faculty: {record['faculty']}" if record["faculty"] else ""
            ] if x)
            if details:
                st.caption(details)
            st.warning("Possible mismatch. Please verify with your faculty or attendance office.")
            if record["notes"]:
                st.write(record["notes"])

    st.divider()
    st.subheader("Attendance check history")
    if not records:
        st.caption("No attendance checks saved yet.")
    for record in records:
        with st.container(border=True):
            a, b = st.columns([6, 1])
            with a:
                student_text = "Attended" if record["student_attended"] else "Did not attend"
                st.markdown(f"**{esc(record['subject'])}** · {record['class_date']}")
                st.caption(
                    f"You: {student_text} | Portal: {record['portal_status']} | "
                    f"{record['class_time'] or 'Time not entered'}"
                )
            with b:
                if st.button("Delete", key=f"attendance_delete_{record['id']}"):
                    delete_attendance_check(record["id"])
                    st.rerun()


def render_plans():
    page_header("My plans & reminders",
                "Save tasks and receive Windows notifications at their scheduled date and time.")
    tasks = get_tasks()
    pending = [task for task in tasks if task["status"] == "Pending"]
    completed = [task for task in tasks if task["status"] == "Completed"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Total plans", len(tasks))
    c2.metric("Pending", len(pending))
    c3.metric("Completed", len(completed))

    st.subheader("Add a plan")
    with st.form("add_nexus_task", clear_on_submit=True):
        title = st.text_input("What do you need to do?",
                              placeholder="Meeting with Rahul")
        date_value = st.date_input("Date")
        time_value = st.time_input("Time (optional)", value=None)
        notes = st.text_area("Notes (optional)")
        submitted = st.form_submit_button("Save plan", type="primary")
        if submitted:
            if not title.strip():
                st.warning("Please enter a task or meeting name.")
            else:
                add_task(title, date_value.isoformat(),
                         time_value.strftime("%H:%M") if time_value else "", notes)
                st.success("Plan saved in your local SQLite database.")
                st.rerun()

    st.divider()
    st.subheader("Your saved plans")
    if not tasks:
        st.info("No plans saved yet. Add your first task above.")
    for task in tasks:
        with st.container(border=True):
            left, middle, right = st.columns([5, 2, 1])
            with left:
                st.markdown(f"**{esc(task['title'])}**")
                st.caption(f"{task['task_date']}  {task['task_time'] or 'Time not set'}")
                if task["notes"]:
                    st.write(task["notes"])
            with middle:
                st.caption(task["status"])
                if task["status"] == "Pending":
                    if st.button("Mark complete", key=f"task_done_{task['id']}"):
                        update_task_status(task["id"], "Completed")
                        st.rerun()
                else:
                    if st.button("Reopen", key=f"task_reopen_{task['id']}"):
                        update_task_status(task["id"], "Pending")
                        st.rerun()
            with right:
                if st.button("Delete", key=f"task_delete_{task['id']}"):
                    delete_task(task["id"])
                    st.rerun()
    st.caption("NEXUS checks due plans every 20 seconds and sends a Windows notification. Keep NEXUS running for reminders. Tasks without a time do not trigger a notification. Google Calendar sync is not included yet.")


# =============================================================================
# ROUTER
# =============================================================================
PAGE_RENDERERS = {
    "Dashboard": render_dashboard,
    "AI Chat": render_chat,
    "Personal Wiki": render_wiki,
    "Computer Tools": render_computer_tools,
    "System Status": render_system_status,
    "My Plans & Reminders": render_plans,
    "Attendance Checker": render_attendance_checker,
}

current_page = render_sidebar()
PAGE_RENDERERS[current_page]()