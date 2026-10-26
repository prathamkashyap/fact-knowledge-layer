#!/usr/bin/env python3
"""
End-to-end execution smoke check for a fresh clone.

Runs the documented workflow against temporary paths outside the repository:

1. Load the bundled starter PDFs into a temporary SQLite database.
2. Start the real FastAPI app with ``FACT_LAYER_DB_PATH`` pointed at that DB.
3. Exercise the live HTTP surface: ``/health``, ``/``, ``/ui``,
   ``/documents``, ``/facts``, ``/facts/{id}``, ``/relationships``, ``/stats``.
4. Terminate the server and remove the temporary database.

No external LLM credentials are required or forwarded; the default
offline/heuristic runtime is exercised. Exits non-zero and prints diagnostics if
any required stage fails. Nothing under ``docs/evaluation`` is read or written.

Usage:
    python scripts/smoke_check.py
    python scripts/smoke_check.py --keep          # keep the temp DB for debugging
    python scripts/smoke_check.py --timeout 180   # allow a slower machine
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _clean_env() -> dict[str, str]:
    """Copy the environment without provider credentials to prove offline use."""
    env = dict(os.environ)
    for key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        env.pop(key, None)
    return env


def _http_get(base: str, path: str, timeout: float = 10.0):
    with urllib.request.urlopen(base + path, timeout=timeout) as response:
        return response.status, response.headers.get("content-type", ""), response.read()


def _wait_for_health(base: str, process: subprocess.Popen, timeout: float) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with code {process.returncode}")
        try:
            status, _, body = _http_get(base, "/health", timeout=2.0)
            if status == 200:
                return json.loads(body)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"server did not become healthy within {timeout}s: {last_error}")


def _tail(path: Path, lines: int = 30) -> str:
    try:
        content = path.read_text(errors="replace").splitlines()
    except OSError:
        return "(no server log)"
    return "\n".join(content[-lines:])


def run_smoke(timeout: float, keep: bool) -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="fkl-smoke-"))
    db_path = temp_root / "demo.db"
    server_log = temp_root / "server.log"
    uploads_dir = REPO_ROOT / "data" / "uploads"
    uploads_preexisted = uploads_dir.exists()
    server: subprocess.Popen | None = None
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if condition:
            _log("PASS", message)
        else:
            failures.append(message)
            _log("FAIL", message)

    try:
        _log("stage", f"temp root: {temp_root}")

        # 1. Demo load into a temporary database.
        started = time.time()
        load = subprocess.run(
            [sys.executable, "scripts/load_starter_demo.py", "--db", str(db_path)],
            cwd=REPO_ROOT,
            env=_clean_env(),
            capture_output=True,
            text=True,
        )
        if load.returncode != 0:
            print(load.stdout, file=sys.stderr)
            print(load.stderr, file=sys.stderr)
        require(load.returncode == 0, f"demo loader exited 0 (got {load.returncode})")
        require(db_path.is_file(), f"demo database created at {db_path}")
        if load.returncode != 0 or not db_path.is_file():
            raise RuntimeError("demo load failed")
        _log("PASS", f"demo load finished in {time.time() - started:.1f}s")

        # 2. Start the real application.
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        env = _clean_env()
        env["FACT_LAYER_DB_PATH"] = str(db_path)
        with server_log.open("w") as log_handle:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        health = _wait_for_health(base, server, timeout)
        require(health.get("status") == "ok", "GET /health status is ok")
        stats = health.get("stats", {})
        require(stats.get("total_documents", 0) >= 1, "health reports documents >= 1")
        require(stats.get("total_facts", 0) >= 1, "health reports facts >= 1")
        require(stats.get("total_relationships", 0) >= 1, "health reports relationships >= 1")
        require(
            health.get("reasoning_mode") == "heuristic",
            "offline heuristic reasoning mode is active",
        )

        # 3. UI endpoints.
        status, content_type, body = _http_get(base, "/")
        require(status == 200 and "text/html" in content_type, "GET / returns HTML")
        require(b"Fact Explorer" in body, "GET / renders the Fact Explorer UI")
        status, content_type, body = _http_get(base, "/ui")
        require(status == 200 and "text/html" in content_type, "GET /ui returns HTML")

        # 4. Read-only API endpoints.
        status, _, body = _http_get(base, "/documents")
        documents = json.loads(body)
        require(status == 200, "GET /documents returns 200")
        require(
            len(documents) == stats.get("total_documents"),
            f"GET /documents count ({len(documents)}) matches health",
        )
        require(
            all("filename" in doc and "total_pages" in doc for doc in documents),
            "each document exposes filename and total_pages",
        )

        status, _, body = _http_get(base, "/facts?limit=5")
        facts = json.loads(body)
        require(status == 200 and len(facts) > 0, "GET /facts returns facts")
        require(
            all(
                fact.get("evidence", {}).get("text")
                and fact["evidence"].get("page_number", 0) > 0
                and fact["evidence"].get("document_name")
                for fact in facts
            ),
            "every fact carries grounded evidence (text, page, document)",
        )

        if facts:
            fact_id = facts[0]["id"]
            status, _, body = _http_get(base, f"/facts/{fact_id}")
            detail = json.loads(body)
            require(status == 200, f"GET /facts/{fact_id} returns 200")
            require("evidence" in detail and "relationships" in detail,
                    "fact detail exposes evidence and linked relationships")

        status, _, body = _http_get(base, "/relationships?limit=5")
        relationships = json.loads(body)
        require(status == 200 and len(relationships) > 0, "GET /relationships returns rows")
        require(
            all(r.get("relationship") and r.get("dimensions") and r.get("reason")
                for r in relationships),
            "each relationship exposes a label, dimensions, and reason",
        )

        status, _, body = _http_get(base, "/stats")
        require(status == 200 and json.loads(body).get("total_documents", 0) >= 1,
                "GET /stats returns aggregate counts")

    except Exception as exc:  # noqa: BLE001 - report every stage failure
        failures.append(str(exc))
        _log("FAIL", str(exc))
        _log("server log (tail)", "\n" + _tail(server_log))
    finally:
        if server is not None and server.poll() is None:
            try:
                os.killpg(os.getpgid(server.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(server.pid), signal.SIGKILL)
                server.wait(timeout=5)
            _log("stage", "server terminated")

        if keep:
            _log("stage", f"--keep set; temporary files retained at {temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)
            _log("stage", "temporary database removed")

        # The app creates data/uploads on import; remove it only if this run
        # created an empty one and it did not exist beforehand.
        if not uploads_preexisted and uploads_dir.is_dir() and not any(uploads_dir.iterdir()):
            uploads_dir.rmdir()
            data_dir = uploads_dir.parent
            if data_dir.is_dir() and not any(data_dir.iterdir()):
                data_dir.rmdir()

    if failures:
        print(f"\nSMOKE CHECK FAILED ({len(failures)} failure(s)):", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("\nSMOKE CHECK PASSED: load, startup, UI, and read-only API all work.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for the server to become healthy (default: 120).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temporary database and server log for debugging.",
    )
    args = parser.parse_args([] if argv is None else argv)
    return run_smoke(timeout=args.timeout, keep=args.keep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
