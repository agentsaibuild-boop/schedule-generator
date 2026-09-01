"""Integration tests: РЕАЛНО изпълнение на PII скана в ВРЕМЕНЕН GIT REPO.

Одит v23: unit тестовете на `scan_text`/`scan_file` минаваха, докато СЪЩИНСКАТА
атака (staged-но-после-изтрит-от-worktree файл) заобикаляше hook-а — защото няма
integration тест с реален git index.  Тук има: staged blob-ове, NUL-safe имена,
pathname vs съдържание, операционни грешки — през `security_scan.py --staged`,
точно както hook-ът.

FAILURE означава: PII сканът може да бъде заобиколен през git index-а или се
проваля fail-open при операционна грешка.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCAN = REPO_ROOT / "tools" / "security_scan.py"
HOOK = REPO_ROOT / "hooks" / "pre-commit"
TERM = "Зорницаград"          # синтетичен клиентски маркер, НЕ реално име

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git не е наличен")


def _clean_env() -> dict:
    """Средата БЕЗ GIT_* — иначе тестът работи върху ЧУЖД индекс.

    Тестовете вдигат временни репа, но hook-ът ги пуска ПО ВРЕМЕ НА КОМИТ, а
    тогава git подава `GIT_INDEX_FILE`, `GIT_DIR` и др. надолу.  Наследени, те
    насочват `git diff --cached` от временното репо към ИСТИНСКОТО — blob-овете
    липсват, сканът връща rc 3 (fail-closed) и шест теста падат.  Резултатът е,
    че с инсталиран hook НИТО ЕДИН комит не минава, а самостоятелният прогон на
    pytest е зелен: дефектът се вижда само в момента, в който пречи.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=_clean_env())


def _scan(repo: Path, *args: str) -> int:
    """Пусни security_scan.py в repo-то (cwd), върни exit кода."""
    return subprocess.run(
        [sys.executable, str(SCAN), *args],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=_clean_env()).returncode


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "deny.txt").write_text(TERM + "\n", encoding="utf-8")
    return r


def _deny(repo: Path) -> str:
    return str(repo / "deny.txt")


# ===================================================================
# P0 — staged съдържанието НЕ бива да заобикаля скана
# ===================================================================

def test_staged_content_survives_worktree_deletion(repo: Path):
    """ТОЧНАТА атака от одит v22/v23: `git add` на тайна, после изтриване от
    worktree.  Staged blob-ът остава в индекса и отива в commit-а — сканът ТРЯБВА
    да го хване, макар файлът да не е на диска."""
    secret = repo / "secret.md"
    secret.write_text(f"Проект: {TERM}", encoding="utf-8")
    _git(repo, "add", "secret.md")
    secret.unlink()                       # изтрит от worktree, ОСТАВА в индекса
    assert not secret.exists()
    assert _scan(repo, "--staged", _deny(repo)) == 2      # хванат ОТ ИНДЕКСА


def test_staged_clean_is_zero(repo: Path):
    (repo / "ok.md").write_text("нищо чувствително", encoding="utf-8")
    _git(repo, "add", "ok.md")
    assert _scan(repo, "--staged", _deny(repo)) == 0


def test_staged_term_in_filename_is_caught(repo: Path):
    f = repo / f"{TERM}_report.md"
    f.write_text("чисто съдържание", encoding="utf-8")
    _git(repo, "add", str(f.name))
    assert _scan(repo, "--staged", _deny(repo)) == 2      # по ИМЕ


def test_staged_nul_safe_filename_with_spaces(repo: Path):
    f = repo / f"проект с интервали {TERM}.md"
    f.write_text("съдържание", encoding="utf-8")
    _git(repo, "add", str(f.name))
    assert _scan(repo, "--staged", _deny(repo)) == 2


# ===================================================================
# Операционна грешка = fail-closed (non-zero), НЕ „чисто"
# ===================================================================

def test_missing_file_is_operational_error_not_clean(repo: Path):
    rc = _scan(repo, _deny(repo), str(repo / "does_not_exist.md"))
    assert rc == 3                        # одит v23: НЕ 0


def test_git_error_outside_repo_is_operational_error(tmp_path: Path):
    """`--staged` извън git repo → git грешка → operational (3), не 0."""
    (tmp_path / "deny.txt").write_text(TERM + "\n", encoding="utf-8")
    rc = subprocess.run(
        [sys.executable, str(SCAN), "--staged", str(tmp_path / "deny.txt")],
        cwd=tmp_path, capture_output=True, text=True,
        encoding="utf-8", errors="replace").returncode
    assert rc == 3


# ===================================================================
# РЕАЛНИЯТ shell hook (hooks/pre-commit) — не само Python скенерът.
# PRECOMMIT_SKIP_TESTS=1 пуска само скана (без pytest рекурсия).
# ===================================================================

bash_required = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash не е наличен")


def _hook_repo(repo: Path) -> Path:
    """Подготви repo-то за реалния hook: копие на скенера + denylist в .git."""
    (repo / "tools").mkdir(exist_ok=True)
    shutil.copy(SCAN, repo / "tools" / "security_scan.py")
    (repo / ".git" / "hooks-secrets").write_text(TERM + "\n", encoding="utf-8")
    return repo


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    """Изпълни РЕАЛНИЯ hooks/pre-commit (само скана)."""
    env = _clean_env()
    env["PRECOMMIT_SKIP_TESTS"] = "1"
    # осигури, че `python`/`python3` се резолвва (hook-ът ги търси)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["bash", str(HOOK)], cwd=repo, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")


@bash_required
def test_real_hook_blocks_content_leak(repo: Path):
    _hook_repo(repo)
    (repo / "note.md").write_text(f"Проект: {TERM}", encoding="utf-8")
    _git(repo, "add", "note.md", "tools/security_scan.py")
    r = _run_hook(repo)
    assert r.returncode != 0                       # блокиран
    assert "SCAN FAILED" in r.stdout


@bash_required
def test_real_hook_blocks_staged_deletion_bypass(repo: Path):
    """ГЛАВНАТА атака през РЕАЛНИЯ hook: staged secret, изтрит от worktree."""
    _hook_repo(repo)
    secret = repo / "secret.md"
    secret.write_text(f"обект {TERM}", encoding="utf-8")
    _git(repo, "add", "secret.md", "tools/security_scan.py")
    secret.unlink()                                # изтрит от worktree, в индекса
    r = _run_hook(repo)
    assert r.returncode != 0                       # ПАК блокиран (от индекса)


@bash_required
def test_real_hook_blocks_synthetic_api_token(repo: Path):
    _hook_repo(repo)
    # split literal → source файлът няма contiguous key-match, runtime стойността matches
    token = "sk-ant-" + "api03-" + "A" * 20
    (repo / "cfg.env").write_text("TOKEN=" + token, encoding="utf-8")
    _git(repo, "add", "cfg.env", "tools/security_scan.py")
    r = _run_hook(repo)
    assert r.returncode != 0


@bash_required
def test_real_hook_blocks_term_in_filename(repo: Path):
    _hook_repo(repo)
    f = repo / f"{TERM}_plan.md"
    f.write_text("чисто", encoding="utf-8")
    _git(repo, "add", str(f.name), "tools/security_scan.py")
    r = _run_hook(repo)
    assert r.returncode != 0


@bash_required
def test_real_hook_passes_clean(repo: Path):
    _hook_repo(repo)
    (repo / "ok.md").write_text("нищо чувствително — Тестоград", encoding="utf-8")
    _git(repo, "add", "ok.md", "tools/security_scan.py")
    r = _run_hook(repo)
    assert r.returncode == 0                        # чисто → минава скана
    assert "Няма намерени" in r.stdout


@bash_required
def test_real_hook_clean_unicode_filename_passes(repo: Path):
    _hook_repo(repo)
    (repo / "проект-Тестоград.md").write_text("чисто", encoding="utf-8")
    _git(repo, "add", "проект-Тестоград.md", "tools/security_scan.py")
    assert _run_hook(repo).returncode == 0


@bash_required
def test_real_hook_blocks_token_after_worktree_deletion(repo: Path):
    """API token staged, после изтрит от worktree → пак блокиран (от индекса)."""
    _hook_repo(repo)
    token = "sk-ant-" + "api03-" + "A" * 20
    cfg = repo / "cfg.env"
    cfg.write_text("TOKEN=" + token, encoding="utf-8")
    _git(repo, "add", "cfg.env", "tools/security_scan.py")
    cfg.unlink()
    assert _run_hook(repo).returncode != 0


@bash_required
def test_real_hook_blocks_machine_path_in_filename(repo: Path):
    """Одит v24 P1: машинен-път ИМЕ на файл (относителен в repo) се блокира."""
    _hook_repo(repo)
    mp = "Users/" + "alice/" + "Desktop"           # split → source файлът е чист
    d = repo / mp
    d.mkdir(parents=True)
    (d / "plan.md").write_text("чисто съдържание", encoding="utf-8")
    _git(repo, "add", "--", mp + "/plan.md")
    _git(repo, "add", "tools/security_scan.py")
    assert _run_hook(repo).returncode != 0


@bash_required
def test_real_hook_fails_closed_on_git_show_error(repo: Path, tmp_path: Path):
    """Одит v24 committed regression: `git show :path` умишлено връща грешка (git
    shim) → реалният hook блокира (op_error), НЕ минава fail-open."""
    _hook_repo(repo)
    (repo / "note.md").write_text("нещо чисто", encoding="utf-8")
    _git(repo, "add", "note.md", "tools/security_scan.py")
    shim = tmp_path / "shim"
    shim.mkdir()
    real_path = os.environ.get("PATH", "")
    (shim / "git").write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do [ "$a" = "show" ] && exit 128; done\n'
        'PATH="$REAL_PATH" exec git "$@"\n', encoding="utf-8")
    (shim / "git").chmod(0o755)
    env = _clean_env()
    env["PRECOMMIT_SKIP_TESTS"] = "1"
    env["REAL_PATH"] = str(Path(sys.executable).parent) + os.pathsep + real_path
    env["PATH"] = str(shim) + os.pathsep + env["REAL_PATH"]
    r = subprocess.run(["bash", str(HOOK)], cwd=repo, env=env,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode != 0, f"fail-open! stdout={r.stdout}"


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows забранява tab/newline в имена на файлове")
def test_nul_safe_tab_and_newline_filenames(repo: Path):
    """POSIX: имена с TAB/NEWLINE + термин се обработват (NUL-safe изброяване)."""
    for name in (f"a\tb_{TERM}.md", f"c\nd_{TERM}.md"):
        (repo / name).write_text("x", encoding="utf-8")
        _git(repo, "add", "--", name)
    _git(repo, "add", "tools/security_scan.py")
    assert _scan(repo, "--staged", _deny(repo)) == 2


def test_ci_orchestration_two_channels(repo: Path):
    """Одит v24: симулация на ДВАТА CI канала — pathname за ВСИЧКИ файлове (вкл.
    бинарни по разширение) + content за текстови.  Token-shaped ИМЕ минаваше
    преди v24; сега канал 1 го лови."""
    _hook_repo(repo)
    token = "sk-ant-" + "api03-" + "A" * 20
    (repo / f"{token}.png").write_bytes(b"\x89PNG\x00binary")   # бинарен + token ИМЕ
    _git(repo, "add", "--", f"{token}.png")
    _git(repo, "add", "tools/security_scan.py")
    dl = _deny(repo)
    # канал 1: pathname за ВСИЧКИ (git ls-files) — трябва да хване token-ИМЕТО
    names = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                           text=True, encoding="utf-8",
                           env=_clean_env()).stdout.split()
    ch1 = subprocess.run([sys.executable, str(SCAN), "--names-only", dl, *names],
                         cwd=repo, capture_output=True, text=True,
                         encoding="utf-8", errors="replace",
                         env=_clean_env()).returncode
    assert ch1 == 2                                  # token-ИМЕ хванато по канал 1
