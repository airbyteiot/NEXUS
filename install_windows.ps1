$ErrorActionPreference = "Stop"
Write-Host "=== NEXUS setup ===" -ForegroundColor Cyan

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Python launcher not found. Install Python 3.12 from https://www.python.org/downloads/windows/" -ForegroundColor Yellow
    Write-Host "During setup, enable 'Add python.exe to PATH'. Then close and reopen PowerShell."
    exit 1
}

py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

New-Item -ItemType Directory -Force -Path ".\data", ".\workspace\generated_files" | Out-Null

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Ollama found. Pulling Qwen 2.5 3B model..." -ForegroundColor Green
    ollama pull qwen2.5:7b
} else {
    Write-Host "Ollama not found. Install it from https://ollama.com/download/windows then run: ollama pull qwen2.5:7b" -ForegroundColor Yellow
}

Write-Host "Setup complete. Run .\start_nexus.bat" -ForegroundColor Green
