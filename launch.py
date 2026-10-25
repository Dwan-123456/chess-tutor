"""Desktop entry point: restart the local server, then open the browser."""
from pathlib import Path
from contextlib import contextmanager
import json
import csv
import os
import re
import socket
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


def desktop_processes():
    """Find this checkout's server processes, never trusting a stale PID file."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "@(Get-CimInstance Win32_Process -ErrorAction Stop | "
         "Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') } | "
         "Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    processes = json.loads(result.stdout or "[]")
    if isinstance(processes, dict):
        processes = [processes]
    executable = re.escape(str(ROOT / ".venv" / "Scripts")) + r"\\pythonw?\.exe"
    command = re.compile(
        r'^"?' + executable + r'"?\s+-m\s+'
        r'(webapp\.desktop_server|uvicorn\s+webapp\.server:app)(?=\s|$)', re.I)
    matches = []
    for process in processes:
        line = process.get("CommandLine") or ""
        match = command.match(line)
        if match and (match[1].lower() == "webapp.desktop_server" or
                      re.search(r"(?:^|\s)--port(?:\s+|=)8765(?=\s|$)", line)):
            matches.append(process)
    return matches


def stop_existing_server():
    if sys.platform != "win32":
        raise RuntimeError("The desktop restart launcher currently requires Windows.")
    processes = desktop_processes()
    pids = {process["ProcessId"] for process in processes}
    # Windows venv Python has a child interpreter; stop its tree only once.
    for process in processes:
        if process["ParentProcessId"] not in pids:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(process["ProcessId"]), "/T", "/F"],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode and desktop_processes():
                raise RuntimeError("Unable to stop the previous Chess Annotator service.")
    deadline = time.monotonic() + 10
    while True:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            occupied = probe.connect_ex(("127.0.0.1", 8765)) == 0
        if not occupied:
            break
        if not processes or time.monotonic() >= deadline:
            raise RuntimeError("Port 8765 is still in use. No unrelated program was stopped.")
        time.sleep(0.2)
    (ROOT / "runtime" / "server.pid").unlink(missing_ok=True)


@contextmanager
def launch_lock():
    """Serialize overlapping shortcut launches through server startup."""
    import msvcrt
    logs = ROOT / "runtime"
    logs.mkdir(exist_ok=True)
    with (logs / "launch.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        deadline = time.monotonic() + 60
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another launcher is still starting the service.")
                time.sleep(0.2)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def restart_server():
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
            [sys.executable, "-m", "webapp.desktop_server"],
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


def main():
    os.chdir(ROOT)
    with launch_lock():
        stop_existing_server()
        restart_server()
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
