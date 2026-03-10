"""Fixtures за Playwright E2E тестове — работи с реални AI ключове."""
# -*- coding: utf-8 -*-

import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from dotenv import dotenv_values

SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"
APP_DIR = Path(__file__).resolve().parent.parent.parent
APP_PORT = 8502
APP_URL = f"http://localhost:{APP_PORT}"
HEALTH_URL = f"{APP_URL}/_stcore/health"


@pytest.fixture(scope="function")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1920, "height": 1080},
    }


@pytest.fixture(scope="session")
def streamlit_server():
    """Стартира реалното приложение с истински .env ключове."""
    env_file = APP_DIR / ".env"
    if not env_file.exists():
        raise RuntimeError(
            "Липсва .env файл с реални API ключове. "
            "E2E тестовете изискват истински ANTHROPIC_API_KEY и DEEPSEEK_API_KEY."
        )

    import sys
    streamlit_exe = Path(sys.executable).parent / "streamlit.exe"
    if not streamlit_exe.exists():
        streamlit_exe = Path(sys.executable).parent / "streamlit"

    log_file = APP_DIR / "tests" / "e2e" / "streamlit_test.log"
    _log_fh = open(log_file, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            str(streamlit_exe), "run", "app.py",
            f"--server.port={APP_PORT}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            "--server.fileWatcherType=none",
        ],
        cwd=str(APP_DIR),
        stdout=_log_fh,
        stderr=_log_fh,
        env={**os.environ, **dotenv_values(APP_DIR / ".env"), "PYTHONIOENCODING": "utf-8", "APP_PASSWORD": ""},
    )

    for _ in range(120):
        try:
            resp = urllib.request.urlopen(HEALTH_URL, timeout=2)
            if resp.status == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        proc.kill()
        stdout = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RuntimeError(f"Streamlit не стартира.\nstdout: {stdout}\nstderr: {stderr}")

    yield APP_URL

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="function")
def app_page(page, streamlit_server):
    """Зарежда приложението и изчаква пълното му зареждане."""
    # Wait for server to be ready (it may be busy after a previous heavy operation)
    for _ in range(90):
        try:
            resp = urllib.request.urlopen(f"{streamlit_server}/_stcore/health", timeout=3)
            if resp.status == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    page.goto(streamlit_server, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_selector('[data-testid="stApp"]', timeout=30000)
    page.get_by_text("AI Статус").wait_for(state="visible", timeout=60000)
    page.wait_for_timeout(5000)
    return page


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Прави screenshot при неуспешен тест."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        page = item.funcargs.get("app_page") or item.funcargs.get("page")
        if page:
            SCREENSHOTS_DIR.mkdir(exist_ok=True)
            name = item.nodeid.replace("::", "_").replace("/", "_").replace("\\", "_")
            path = SCREENSHOTS_DIR / f"{name}.png"
            try:
                page.screenshot(path=str(path), full_page=True)
            except Exception:
                pass
