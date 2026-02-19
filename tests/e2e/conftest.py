"""Fixtures for Playwright E2E tests of the ВиК График Генератор."""

import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"

# Paths
APP_DIR = Path(__file__).resolve().parent.parent.parent  # schedule-generator/
APP_PORT = 8502  # Avoid conflict with production on 8501
APP_URL = f"http://localhost:{APP_PORT}"
HEALTH_URL = f"{APP_URL}/_stcore/health"
ENV_FILE = APP_DIR / ".env"

# Dummy .env content (non-placeholder values that pass _check_configuration)
_TEST_ENV = (
    "ANTHROPIC_API_KEY=sk-ant-e2e-test-dummy-key-12345\n"
    "DEEPSEEK_API_KEY=sk-e2e-test-dummy-deepseek-67890\n"
    "ADMIN_CODE=e2e-test-admin-code-abcde\n"
)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Override default browser context to use a wide viewport."""
    return {
        **browser_context_args,
        "viewport": {"width": 1920, "height": 1080},
    }


@pytest.fixture(scope="session")
def streamlit_server():
    """Start a headless Streamlit server for the entire test session.

    - Uses existing .env if present (preserves real API keys)
    - Creates a dummy .env with non-placeholder keys if none exists
    - Starts Streamlit on port 8502 in headless mode
    - Waits up to 30 seconds for the server to become healthy
    - Kills the server and cleans up on teardown
    """
    # --- Setup: ensure .env exists with valid keys ---
    env_existed = ENV_FILE.exists()
    created_dummy = False
    if not env_existed:
        ENV_FILE.write_text(_TEST_ENV, encoding="utf-8")
        created_dummy = True

    # --- Start Streamlit subprocess ---
    proc = subprocess.Popen(
        [
            "streamlit", "run", "app.py",
            f"--server.port={APP_PORT}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            "--server.fileWatcherType=none",
            "--global.developmentMode=false",
        ],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    # --- Wait for health endpoint ---
    for _ in range(60):
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
        raise RuntimeError(
            f"Streamlit did not start within 30s.\nstdout: {stdout}\nstderr: {stderr}"
        )

    yield APP_URL

    # --- Teardown ---
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Clean up dummy .env if we created it
    if created_dummy and ENV_FILE.exists():
        ENV_FILE.unlink()


@pytest.fixture(scope="function")
def app_page(page, streamlit_server):
    """Navigate to the app, wait for it to render, and return the page.

    Each test gets a fresh page navigation to avoid state leakage.
    """
    page.goto(streamlit_server, wait_until="networkidle", timeout=30000)
    # Wait for the Streamlit app container to be present
    page.wait_for_selector('[data-testid="stApp"]', timeout=15000)
    # Wait for the sidebar to fully render (past the AI health check spinner).
    # The "Разходи" section appears right after the AI Status section completes.
    # This is the most reliable indicator that the health check is done.
    cost_section = page.get_by_text("Разходи")
    cost_section.first.wait_for(state="visible", timeout=60000)
    # Give Streamlit extra time to finish rendering all components
    page.wait_for_timeout(2000)
    # Scroll to bottom and back to trigger lazy rendering of all elements
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1000)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    return page


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture screenshot on test failure for debugging."""
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
