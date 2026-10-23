"""Desktop entry point: start the local server once, then open the browser."""
from pathlib import Path
import json
import csv
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"


def ready():
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=2) as response:
            return json.load(response).get("application") == "chess-annotator-desktop"
    except Exception:
        return False


def main():
    os.chdir(ROOT)
    if not ready():
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        credentials = sorted(ROOT.glob("*apiKey*.csv"))
        if len(credentials) > 1:
            raise RuntimeError("Multiple API CSV files found; keep only the intended one in the project folder.")
        if credentials:
            with credentials[0].open(encoding="utf-8-sig", newline="") as source:
                values = dict(row[:2] for row in csv.reader(source) if len(row) >= 2)
            if not values.get("apiKey") or not values.get("openAiCompatible", "").startswith("https://"):
                raise RuntimeError("API CSV must contain apiKey and an HTTPS openAiCompatible URL.")
            os.environ.setdefault("DEEPSEEK_API_KEY", values["apiKey"])
            os.environ.setdefault("DEEPSEEK_BASE_URL", values["openAiCompatible"].rstrip("/"))
            os.environ.setdefault("COMMENTATOR_BACKEND", "deepseek")
            os.environ.setdefault("COMMENTATOR_MODEL", "deepseek-v4.1-flash-1017")
            os.environ.setdefault("FACTCHECK_MODEL", "deepseek-v4.1-flash-1017")
            os.environ.setdefault("LLM_CONCURRENCY", "2")
        os.environ.setdefault("COMMENTATOR_BACKEND", "disabled")
        os.environ.setdefault("FACTCHECK_DEFAULT", "false")
        engine = ROOT / "stockfish/stockfish/stockfish-windows-x86-64-universal.exe"
        os.environ.setdefault("STOCKFISH_PATH", str(engine))
        if not engine.is_file() and not Path(os.environ["STOCKFISH_PATH"]).is_file():
            raise RuntimeError("Stockfish 19 executable is missing.")
        logs = ROOT / "runtime"
        logs.mkdir(exist_ok=True)
        with (logs / "server.log").open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "webapp.server:app", "--host", "127.0.0.1", "--port", "8765"],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        (logs / "server.pid").write_text(str(proc.pid))
        for _ in range(60):
            if ready():
                break
            if proc.poll() is not None:
                raise RuntimeError("Server failed to start. See runtime/server.log.")
            time.sleep(0.5)
        else:
            raise RuntimeError("Server startup timed out. See runtime/server.log.")
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(exc), "Chess Annotator", 0x10)
        else:
            raise
