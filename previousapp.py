import streamlit as st
import requests
import pyttsx3
import io
import speech_recognition as sr
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
    "navigation": "Dashboard"
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

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
    border-radius: 14px;
    padding: 10px;
    background: #111c30;
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
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.setProperty("volume", 1.0)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as error:
        st.warning(f"Voice output unavailable: {error}")


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
    speak_replies = st.checkbox("🔊 Enable NEXUS voice", value=False)

    if not ollama_ready():
        st.error("Ollama is offline. Open Ollama from the Windows Start menu.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Message bar: text field, microphone, and send button on one row.
    message_col, mic_col, send_col = st.columns([8, 1, 1])
    with message_col:
        st.text_input(
            "Message NEXUS",
            placeholder="Message NEXUS...",
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

    # A typed message is queued by the Send button callback.
    # A spoken message is queued after transcription.
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
        st.session_state.messages.append({"role": "user", "content": prompt})
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
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": confirmation
                    })
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

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer
                })
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

    if st.button("Clear conversation"):
        st.session_state.messages = []
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
    st.caption("Controlled computer operations with visible results.")
    st.subheader("Create a text file")

    with st.form("create_file"):
        filename = st.text_input("File name", "my_goals.txt")
        content = st.text_area("File content", "My goal is to become an AI engineer.")
        create = st.form_submit_button("Create file")
        if create:
            try:
                path = create_note(filename, content)
                st.success(f"Successfully created: {path}")
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
