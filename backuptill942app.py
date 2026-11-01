import streamlit as st
import requests
import base64
import io
import speech_recognition as sr
import sqlite3
import uuid
import os
import re
import subprocess
import shutil
import ctypes
from urllib.parse import urlencode
from urllib.parse import quote_plus
from datetime import datetime
from streamlit_mic_recorder import mic_recorder

from memory import init_db, add_memory, list_memories, search_memories, remove_memory
from tools import create_note, list_workspace_files, open_approved_website, WORKSPACE

# CONFIGURATION
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b"

st.set_page_config(
    page_title="NEXUS | Personal AI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)
init_db()

# SESSION STATE
defaults = {
    "messages": [],
    "pending_prompt": None,
    "last_audio_id": None,
    "navigation": "Dashboard",
    "pending_navigation": None,
    "pending_computer_tools_section": None,
    "computer_tools_section": "🖥️ Apps & Windows",
    "active_chat_id": None,
    "email_subject": "",
    "email_body": "",
    "email_recipient": "",
    "voice_enabled": True
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

# LOCAL CHAT HISTORY DATABASE
CHAT_DB = "nexus_chat_history.db"


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


# DESIGN
st.markdown("""
<style>
.stApp {
    background: radial-gradient(circle at 85% 5%, rgba(34, 94, 155, 0.15), transparent 35%), #0b1120;
    color: #edf4ff;
}
[data-testid="stSidebar"] {
    background: #101b30;
    border-right: 1px solid #253650;
}
.nexus-brand {
    font-size: 2.5rem;
    font-weight: 900;
    letter-spacing: 0.22em;
    color: #82c5ff;
    padding-top: 12px;
}
.nexus-subtitle {
    color: #91a7c4;
    font-size: 0.85rem;
    letter-spacing: 0.08em;
}
.hero {
    background: linear-gradient(120deg, #152a47, #132038, #172b42);
    border: 1px solid #304b70;
    border-radius: 20px;
    padding: 30px;
    margin-bottom: 24px;
}
.hero h1 { color: #eaf4ff; font-size: 2.5rem; margin-bottom: 8px; }
.hero p { color: #a9c2df; font-size: 1rem; }
.metric-card {
    background: #14223a;
    border: 1px solid #2b4261;
    border-radius: 16px;
    padding: 20px;
    min-height: 145px;
}
.metric-label { color: #9db2ce; font-size: 0.9rem; }
.metric-value {
    color: #8bc5ff;
    font-size: 1.65rem;
    font-weight: 800;
    margin-top: 10px;
}
.metric-description { color: #91a7c4; font-size: 0.8rem; margin-top: 5px; }
.section-heading {
    color: #dcecff;
    font-weight: 750;
    font-size: 1.35rem;
    margin-top: 20px;
}
div[data-testid="stChatMessage"] {
    border: 1px solid #2b405e;
    border-radius: 16px;
    padding: 14px 16px;
    margin-bottom: 12px;
    background: #111c30;
}
div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background: #172b45;
    border-color: #3c638e;
}
.chat-welcome {
    background: linear-gradient(135deg, #152a47, #101b30);
    border: 1px solid #304b70;
    border-radius: 20px;
    padding: 28px;
    margin: 18px 0 22px 0;
}
.chat-welcome h2 {
    color: #eaf4ff;
    font-size: 2rem;
    margin: 0 0 8px 0;
}
.chat-welcome p {
    color: #a9c2df;
    margin: 0;
}
.stButton > button { border-radius: 10px; border: 1px solid #3a5b82; }
div[data-testid="stForm"] { border-color: #2b405e; }
div[data-testid="stTextInput"] input { border-radius: 12px; }
</style>
""", unsafe_allow_html=True)


# HELPERS
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
              "$speaker.Speak($text); $speaker.Dispose()"
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


def transcribe_audio(audio_bytes):
    recognizer = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
        recorded_audio = recognizer.record(source)
    return recognizer.recognize_google(recorded_audio)


def navigate_to(destination):
    st.session_state.navigation = destination


def submit_typed_message():
    typed = st.session_state.get("nexus_message_input", "").strip()
    if typed:
        st.session_state.pending_prompt = typed
        st.session_state.nexus_message_input = ""


def submit_quick_prompt(prompt):
    st.session_state.pending_prompt = prompt


def render_metric(label, value, description):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-description">{description}</div>
        </div>
        """,
        unsafe_allow_html=True
    )


# WINDOWS APPLICATION LAUNCHER
# Only applications listed here can be launched by chat commands.
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

    return (f"I don't recognize `/{command}`. Type `/help` to see available commands.")


def handle_app_command(prompt):
    """Handle 'open Chrome' or 'open Chrome and Notepad' commands."""
    text = prompt.strip().lower()

    match = re.match(
        r"^(?:please\s+)?(?:open|launch|start)\s+(.+?)\s*[.!?]*$",
        text
    )

    if not match:
        return None

    requested = match.group(1)
    app_names = re.split(r"\s*(?:,|\band\b|\bplus\b)\s*", requested)

    results = []
    for app_name in app_names:
        app_name = app_name.strip(" .!?")
        if app_name:
            results.append(launch_windows_app(app_name))

    if not results:
        return "Please tell me which application to open."

    output = ["### NEXUS App Launcher"]
    for success, message in results:
        output.append(f"{'✅' if success else '⚠️'} {message}")

    return "\n\n".join(output)


# WINDOWS WINDOW MULTITASKING
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


# SIDEBAR
with st.sidebar:
    st.markdown('<div class="nexus-brand">NEXUS</div>', unsafe_allow_html=True)
    st.markdown('<div class="nexus-subtitle">PERSONAL AI WORKSPACE</div>', unsafe_allow_html=True)
    st.divider()
    st.caption("MAIN WORKSPACE")
    page = st.radio(
        "Navigation",
        ["Dashboard", "AI Chat", "Personal Wiki", "Computer Tools", "System Status"],
        key="navigation",
        label_visibility="collapsed"
    )

    if page == "AI Chat":
        st.divider()
        st.caption("CHAT HISTORY")
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
                    "🗑️",
                    key=f"delete_chat_{saved_chat['id']}",
                    help="Delete this saved conversation"
                ):
                    delete_saved_chat(saved_chat["id"])
                    st.rerun()

    st.divider()
    st.caption("LOCAL INTELLIGENCE")
    if ollama_ready():
        st.success("AI Engine Online")
    else:
        st.error("AI Engine Offline")
    st.caption(f"Model: {MODEL}")
    st.caption("Local SQLite Memory")


# DASHBOARD
if page == "Dashboard":
    st.markdown("""
    <div class="hero">
        <h1>Welcome to NEXUS.</h1>
        <p>Your personal intelligence workspace. Think, remember, create and interact.</p>
    </div>
    """, unsafe_allow_html=True)

    memories = list_memories()
    files = list_workspace_files()
    ai_online = ollama_ready()
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        render_metric("LOCAL AI ENGINE", "ONLINE" if ai_online else "OFFLINE", "Qwen 2.5 7B via Ollama")
    with col2:
        render_metric("PERSONAL MEMORY", str(len(memories)), "Saved Wiki records")
    with col3:
        render_metric("VOICE ENGINE", "READY", "Microphone and speech output")
    with col4:
        render_metric("GENERATED FILES", str(len(files)), "Files in NEXUS workspace")

    st.markdown('<div class="section-heading">Your Workspace</div>', unsafe_allow_html=True)
    st.write("Choose an area to continue working with NEXUS.")
    c1, c2 = st.columns(2)

    with c1:
        with st.container(border=True):
            st.subheader("🧠 AI Conversation")
            st.write("Talk to your local AI assistant using text or your microphone.")
            st.button("Open AI Chat →", key="dash_chat", use_container_width=True,
                      on_click=navigate_to, args=("AI Chat",))
        with st.container(border=True):
            st.subheader("📁 Computer Tools")
            st.write("Create files, open approved websites and inspect generated files.")
            st.button("Open Computer Tools →", key="dash_tools", use_container_width=True,
                      on_click=navigate_to, args=("Computer Tools",))

    with c2:
        with st.container(border=True):
            st.subheader("📚 Personal Wiki")
            st.write("Store, review and manage your personal knowledge and memories.")
            st.button("Open Personal Wiki →", key="dash_wiki", use_container_width=True,
                      on_click=navigate_to, args=("Personal Wiki",))
        with st.container(border=True):
            st.subheader("⚙️ System Status")
            st.write("Check the AI engine, model and local workspace.")
            st.button("View System Status →", key="dash_status", use_container_width=True,
                      on_click=navigate_to, args=("System Status",))

    st.markdown('<div class="section-heading">Recent Personal Memories</div>', unsafe_allow_html=True)
    if memories:
        for item in memories[-3:][::-1]:
            with st.container(border=True):
                st.caption(item["category"])
                st.write(item["fact"])
    else:
        st.info("Your Personal Wiki is empty. Add your first memory to get started.")
    st.caption(f"Workspace initialized • {datetime.now().strftime('%d %b %Y, %H:%M')}")


# AI CHAT
elif page == "AI Chat":
    st.title("Talk to NEXUS")
    st.caption("Your personal AI assistant with voice and long-term memory.")
    speak_replies = st.checkbox("🔊 Enable NEXUS voice", key="voice_enabled", help="NEXUS will speak chat replies and action confirmations.")

    if not ollama_ready():
        st.error("Ollama is offline. Open Ollama from the Windows Start menu.")

    if not st.session_state.messages:
        st.markdown(
            """
            <div class="chat-welcome">
                <h2>What would you like to work on today?</h2>
                <p>Your local AI workspace for ideas, questions and personal knowledge.</p>
            </div>
            """,
            unsafe_allow_html=True
        )
        st.caption("QUICK START")

        quick_cols = st.columns(4)
        quick_prompts = [
            ("💡 Explain a concept", "Explain a difficult concept in simple terms, with an example."),
            ("📅 Plan my day", "Help me create a practical plan for my day."),
            ("🧠 Use my memories", "What relevant things do you remember about me?"),
            ("✨ Brainstorm ideas", "Help me brainstorm some creative ideas.")
        ]

        for quick_col, (label, prompt_text) in zip(quick_cols, quick_prompts):
            with quick_col:
                st.button(
                    label,
                    key=f"quick_{label}",
                    use_container_width=True,
                    on_click=submit_quick_prompt,
                    args=(prompt_text,)
                )

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Text, microphone and send button in one row.
    message_col, mic_col, send_col = st.columns([8, 1, 1])
    with message_col:
        st.text_input(
            "Message NEXUS",
            placeholder="Try /launch firefox, /email, /search AI news, or /help...",
            key="nexus_message_input",
            label_visibility="collapsed"
        )
    with mic_col:
        audio = mic_recorder(
            start_prompt="🎙️",
            stop_prompt="⏹️",
            key="nexus_microphone",
            format="wav"
        )
    with send_col:
        st.button(
            "➤",
            key="nexus_send",
            help="Send message",
            use_container_width=True,
            on_click=submit_typed_message
        )

    if audio and audio.get("id") != st.session_state.last_audio_id:
        st.session_state.last_audio_id = audio.get("id")
        try:
            with st.spinner("Understanding your voice..."):
                transcript = transcribe_audio(audio["bytes"])
            st.success(f"You said: {transcript}")
            st.session_state.pending_prompt = transcript
            st.rerun()
        except sr.UnknownValueError:
            st.warning("I couldn't understand you. Please try again.")
        except sr.RequestError as error:
            st.error(f"Speech recognition service error: {error}")
        except Exception as error:
            st.error(f"Microphone error: {error}")

    prompt = st.session_state.pending_prompt
    if prompt:
        st.session_state.pending_prompt = None

    if prompt:
        add_message_to_chat("user", prompt)

        # Slash commands are routed before natural-language command handling.
        slash_result = handle_slash_command(prompt)
        if slash_result:
            add_message_to_chat("assistant", slash_result)
            # Speak command confirmations too (for example /email and /launch).
            if speak_replies:
                spoken_confirmation = re.sub(r"[`*_#]", "", slash_result)
                spoken_confirmation = re.sub(r"https?://\S+", "", spoken_confirmation)
                speak_text(spoken_confirmation[:500])
            st.rerun()

        # Handle Windows window-management commands first.
        window_result = handle_window_command(prompt)
        if window_result:
            add_message_to_chat("assistant", window_result)
            if speak_replies:
                speak_text(re.sub(r"[`*_#]", "", window_result)[:500])
            st.rerun()

        # Handle direct Firefox searches before sending other prompts to the AI.
        search_result = handle_firefox_search(prompt)
        if search_result:
            add_message_to_chat("assistant", search_result)
            if speak_replies:
                speak_text("I opened your search in Firefox.")
            st.rerun()

        # Handle app-launch commands before sending other prompts to the AI.
        app_result = handle_app_command(prompt)
        if app_result:
            add_message_to_chat("assistant", app_result)
            if speak_replies:
                speak_text("Application launch request processed.")
            st.rerun()

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
            if fact:
                try:
                    add_memory("Other", fact)
                    confirmation = (
                        "I've saved this information to your Personal Wiki:\n\n"
                        f"**{fact}**\n\n"
                        "I'll use it when relevant in future conversations."
                    )
                    add_message_to_chat("assistant", confirmation)
                    if speak_replies:
                        speak_text("I've saved that information to your Personal Wiki.")
                    st.rerun()
                except Exception as error:
                    st.error(f"Memory save failed: {error}")
            else:
                st.warning("Please tell me what to remember.")
        else:
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

                add_message_to_chat("assistant", answer)
                if speak_replies:
                    with st.spinner("NEXUS is speaking..."):
                        speak_text(answer)
                st.rerun()

            except requests.exceptions.ConnectionError:
                st.error("Cannot connect to Ollama.")
            except requests.exceptions.Timeout:
                st.error("The model took too long. Try a shorter question.")
            except Exception as error:
                st.error(f"AI request failed: {type(error).__name__}: {error}")

    if st.button("＋ New chat", key="new_chat_main"):
        begin_new_chat()
        st.rerun()





# PERSONAL WIKI
elif page == "Personal Wiki":
    st.title("Personal Wiki")
    st.caption("Your personal information stays in your local SQLite database.")

    with st.form("add_memory", clear_on_submit=True):
        category = st.selectbox(
            "Category",
            ["About me", "Education", "Work", "Goals", "Preferences", "Projects", "Other"]
        )
        fact = st.text_area(
            "What should NEXUS remember?",
            placeholder="Tell NEXUS something important about you..."
        )
        submitted = st.form_submit_button("Save to my Wiki")
        if submitted:
            if fact.strip():
                add_memory(category, fact.strip())
                st.success("Memory saved successfully.")
                st.rerun()
            else:
                st.warning("Please enter information first.")

    st.divider()
    memories = list_memories()
    st.subheader(f"Saved Memories ({len(memories)})")
    if not memories:
        st.info("No memories saved yet.")

    for item in memories:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.markdown(f"**{item['category']}**")
            left.write(item["fact"])
            if right.button("Delete", key=f"delete_{item['id']}"):
                remove_memory(item["id"])
                st.rerun()


# COMPUTER TOOLS
elif page == "Computer Tools":
    st.title("Computer Tools")
    st.caption("Your control center for applications, windows, email and files.")

    tools_section = st.radio(
        "Computer Tools section",
        ["🖥️ Apps & Windows", "✉️ Email Assistant", "📁 Files & Websites"],
        key="computer_tools_section",
        horizontal=True,
        label_visibility="collapsed"
    )

    if tools_section == "🖥️ Apps & Windows":
        st.subheader("Launch a Windows application")
        launcher_options = [
            "Notepad", "Calculator", "Paint", "File Explorer",
            "Firefox", "Chrome", "Edge", "VS Code", "Spotify",
            "Command Prompt", "PowerShell", "Task Manager", "Settings"
        ]
        selected_app = st.selectbox("Choose an application", launcher_options)
        if st.button("Launch application", key="tools_launch_app"):
            success, message = launch_windows_app(selected_app)
            if success:
                st.success(message)
            else:
                st.error(message)

        st.caption("You can also launch applications by chatting: "
                   "'Open Firefox and Notepad'.")

        st.divider()
        st.subheader("Open Windows")
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


    elif tools_section == "✉️ Email Assistant":
        st.title("✉️ NEXUS Email Assistant")
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
            generate_email = st.form_submit_button("✨ Generate email draft", use_container_width=True)

        if generate_email:
            if not recipient.strip() or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient.strip()):
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

            if recipient_value and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient_value):
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


    elif tools_section == "📁 Files & Websites":
        st.subheader("Create a text file")
        with st.form("create_file"):
            filename = st.text_input("File name", "my_goals.txt")
            content = st.text_area("File content", "My goal is to become an AI engineer.")
            create = st.form_submit_button("Create file")
            if create:
                try:
                    created_path = create_note(filename, content)
                    st.success(f"Successfully created: {created_path}")
                except Exception as error:
                    st.error(str(error))

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

# SYSTEM STATUS
elif page == "System Status":
    st.title("System Status")
    ai_online = ollama_ready()
    memories = list_memories()
    files = list_workspace_files()
    col1, col2 = st.columns(2)
    col1.metric("Ollama AI Engine", "Online" if ai_online else "Offline")
    col2.metric("Personal Wiki", "Ready")

    st.divider()
    st.write(f"**AI Model:** `{MODEL}`")
    st.write(f"**Workspace:** `{WORKSPACE}`")
    st.write(f"**Saved memories:** {len(memories)}")
    st.write(f"**Generated files:** {len(files)}")
    st.write("**AI privacy:** Prompts are processed by your local Ollama service.")
    st.write("**Memory privacy:** Personal Wiki is stored in your local SQLite database.")
    st.write("**Voice output:** Offline Windows speech engine.")
    st.write("**Voice input:** Google speech recognition service (internet required).")

    if st.button("Check installed Ollama models"):
        try:
            data = requests.get("http://localhost:11434/api/tags", timeout=5).json()
            names = [model["name"] for model in data.get("models", [])]
            st.write(names if names else "No models installed.")
        except Exception:
            st.error("Ollama is not reachable.")
