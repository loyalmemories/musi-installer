"""Download the user's copy of Musi with an in-memory Apple session."""
import json
import os
import subprocess
import uuid
from pathlib import Path


def download_musi(email, password, request_code, notify):
    tool = Path(__file__).with_name("bin") / "musi-bridge.exe"
    if not tool.is_file():
        raise RuntimeError("The download component is missing. Extract the entire ZIP, including the bin folder.")
    folder = Path.home() / "Downloads" / "Musi"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"Musi-{uuid.uuid4().hex[:12]}.ipa"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    secrets = [email, password]
    result = None
    failure = None
    def scrub(text):
        for value in secrets:
            if value:
                text = text.replace(value, "[redacted]")
        return text
    # Credentials travel through stdin, never through argv, env or files.
    with subprocess.Popen([str(tool)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                          creationflags=flags) as process:
        try:
            process.stdin.write(json.dumps({"email": email, "password": password,
                                           "output": str(destination)}) + "\n")
            process.stdin.flush()
            for line in process.stdout:
                event = json.loads(line)
                kind = event.get("type")
                if kind == "2fa":
                    code = request_code()
                    if code is None:
                        raise RuntimeError("Verification cancelled.")
                    secrets.append(code)
                    process.stdin.write(json.dumps({"code": code}) + "\n")
                    process.stdin.flush()
                elif kind == "error":
                    failure = scrub(event.get("message", "Apple returned an error."))
                elif kind == "downloaded":
                    result = Path(event["path"])
                elif kind in ("stage", "progress"):
                    event["message"] = scrub(event.get("message", ""))
                    notify(event)
            status = process.wait()
            if status != 0 or result is None:
                raise RuntimeError(failure or "The download component stopped before completing.")
            if result.resolve() != destination.resolve() or not result.is_file():
                raise RuntimeError("The download did not produce the expected app package.")
            return result
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


# Compatibility with earlier local scripts.
scarica = download_musi
