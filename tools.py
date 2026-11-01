from pathlib import Path
from urllib.parse import urlparse
import webbrowser

ROOT = Path(__file__).parent
WORKSPACE = (ROOT / "workspace" / "generated_files").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

ALLOWED_HOSTS = {"www.google.com", "google.com", "github.com", "www.youtube.com", "youtube.com"}

def create_note(filename, content):
    # Prevent path traversal: only a simple filename is accepted.
    safe_name = Path(filename).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("Please enter a valid file name.")
    if not safe_name.lower().endswith(".txt"):
        safe_name += ".txt"
    target = (WORKSPACE / safe_name).resolve()
    if target.parent != WORKSPACE:
        raise ValueError("Invalid file path.")
    target.write_text(content, encoding="utf-8")
    return target

def open_approved_website(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("This website is not on the demo allowlist.")
    webbrowser.open(url)
    return f"Opened {url}"

def list_workspace_files():
    return sorted([p for p in WORKSPACE.iterdir() if p.is_file()])
