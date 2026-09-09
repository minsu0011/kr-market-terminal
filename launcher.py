from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ROOT = Path(sys.executable).resolve().parent if FROZEN else BUNDLE_ROOT
try:
    PORT = int(os.environ.get("TERMINAL_PORT", "8767"))
except ValueError:
    PORT = 8767
URL = f"http://127.0.0.1:{PORT}"
ERROR_LOG = ROOT / "launcher_error.log"


def write_log(message: str) -> None:
    with ERROR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def port_is_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.35):
            return True
    except OSError:
        return False


def venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def requirements_digest() -> str:
    path = ROOT / "requirements.txt"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_checked(command: list[str], label: str) -> None:
    print(label, flush=True)
    write_log(f"$ {' '.join(command)}")
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def ensure_environment() -> Path:
    py = venv_python()

    if not py.exists():
        print("[1/3] Creating isolated Python environment...", flush=True)
        run_checked(
            [sys.executable, "-m", "venv", str(ROOT / ".venv")],
            "Python virtual environment creation",
        )

    marker = ROOT / "data" / ".requirements.sha256"
    expected = requirements_digest()
    installed = marker.read_text(encoding="ascii").strip() if marker.exists() else ""

    if installed != expected:
        print("[2/3] Installing required packages. This is needed only on first run...", flush=True)
        run_checked(
            [str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            "pip bootstrap",
        )
        run_checked(
            [str(py), "-m", "pip", "install", "--prefer-binary", "-r", "requirements.txt"],
            "dependency installation",
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(expected, encoding="ascii")
    else:
        print("[2/3] Required packages are already installed.", flush=True)

    env_file = ROOT / ".env"
    env_example = ROOT / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copyfile(env_example, env_file)

    return py


def open_browser() -> None:
    try:
        if os.name == "nt":
            # This terminal is delivered for Chrome.  Launch Chrome explicitly
            # so a stale tab from another default browser/port cannot be shown.
            candidates = [
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            ]
            chrome = next((path for path in candidates if path.is_file()), None)
            if chrome:
                subprocess.Popen([str(chrome), "--new-tab", URL])
            else:
                os.startfile(URL)  # type: ignore[attr-defined]
        else:
            webbrowser.open_new_tab(URL)
    except Exception:
        try:
            webbrowser.open_new_tab(URL)
        except Exception:
            pass


def open_browser_when_ready() -> None:
    """Open the page only after the local server has started accepting requests."""
    for _ in range(60):
        if port_is_open():
            open_browser()
            return
        time.sleep(0.5)


def main() -> int:
    os.chdir(ROOT)
    ERROR_LOG.write_text("", encoding="utf-8")

    if port_is_open():
        print(f"Server is already running: {URL}", flush=True)
        open_browser()
        return 0

    py = None if FROZEN else ensure_environment()
    print(f"[3/3] Starting server: {URL}", flush=True)
    browser_thread = threading.Thread(target=open_browser_when_ready, daemon=True)
    browser_thread.start()

    if FROZEN:
        import uvicorn
        from app import app
        uvicorn.run(app, host="127.0.0.1", port=PORT, reload=False, log_level="info")
        return 0

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.call([str(py), "app.py"], cwd=ROOT, env=env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nServer stopped.", flush=True)
        raise SystemExit(0)
    # Do not catch SystemExit here: ``raise SystemExit(main())`` is the
    # normal success path and must keep its zero exit code.
    except Exception as exc:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        write_log(detail)
        print("", flush=True)
        print("Launcher failed.", flush=True)
        print(f"Error log: {ERROR_LOG}", flush=True)
        print(str(exc), flush=True)
        raise SystemExit(1)
