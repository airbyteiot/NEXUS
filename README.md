# NEXUS Hackathon MVP

A local-first Windows demo: AI chat powered by Ollama, a local SQLite Personal Wiki, and safe, explicit computer tools.

## What works
- Local AI chat through Ollama's HTTP API
- Personal Wiki: save, list, retrieve relevant facts, delete
- Create text files in `workspace/generated_files`
- Open a small allowlist of websites
- System status / model check

## Requirements
- Windows 10 22H2+ or Windows 11
- Python 3.12
- Ollama for Windows
- Internet for initial installers/model download; the app can run locally afterward

## Install
1. Install Python 3.12: https://www.python.org/downloads/windows/
   - Enable "Add python.exe to PATH".
2. Install Ollama: https://ollama.com/download/windows
3. Open a NEW PowerShell window and check:
   ```powershell
   py -3.12 --version
   ollama --version
   ```
4. Extract this folder somewhere writable, e.g. `C:\NEXUS-Hackathon`.
5. In PowerShell, enter the folder and run:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\install_windows.ps1
   ```
6. Start NEXUS:
   ```powershell
   .\start_nexus.bat
   ```

If the model was not downloaded during setup:
```powershell
ollama pull qwen2.5:7b
```

## Model options
- Default: `qwen2.5:7b` (good first try)
- Lower-memory fallback: `qwen2.5:3b`
- Better quality if hardware allows: `qwen2.5:7b`

To change the model, edit `MODEL` in `app.py`.

## Demo flow
1. Open Personal Wiki and save: "I am a student learning Python. My goal is to become an AI engineer."
2. Open AI Chat and ask: "What is my career goal?"
3. Open Computer Tools and create `my_goals.txt`.
4. Open an approved website.
5. Restart the app and show that Wiki memories remain saved.

## Privacy
- SQLite Wiki database is local in `data/nexus.db`.
- Generated files are local in `workspace/generated_files`.
- Chat prompts are sent to the local Ollama service at `localhost:11434`.
- No OpenAI API key is required.
- This MVP does not have unrestricted desktop control. Computer actions are explicit buttons and an allowlist.

## Troubleshooting
- `py` not recognized: reinstall Python and enable PATH, then reopen PowerShell.
- `ollama` not recognized: install Ollama and reopen PowerShell.
- Connection refused at port 11434: start Ollama from Start menu or run `ollama serve`.
- Model not found: run `ollama pull qwen2.5:7b`.
- Model too slow / out of memory: use `ollama pull qwen2.5:3b`, then change `MODEL` in `app.py`.
- Port 8501 in use: run `streamlit run app.py --server.port 8502`.
- PowerShell blocks script: run `Set-ExecutionPolicy -Scope Process Bypass`.
- Firewall prompt: allow local access if Windows asks; the app binds to Streamlit's local development server.

## Team split (3-4 people)
- Person 1: setup, Ollama, backend integration
- Person 2: Streamlit UI and demo polish
- Person 3: Wiki, memory and test data
- Person 4: presentation, QA, demo script and backup recording


## Hardware profile used for this demo
Recommended starting target: 16 GB system RAM, NVIDIA GPU with 8 GB VRAM, and a modern 6-core CPU. Use the 7B model with a 4096-token context as configured in `app.py`. If the laptop becomes slow or runs out of memory, switch `MODEL` to `qwen2.5:3b`.
