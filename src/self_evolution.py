"""Self-evolution system — AI-driven application modification with 3-level change management.

Levels:
  - GREEN:  Knowledge files (.md) — no admin code, no confirmation
  - YELLOW: Config files (.json) — no admin code, requires confirmation
  - RED:    Code files (.py, requirements.txt) — requires admin code + confirmation

Uses Anthropic Claude (claude-sonnet-4-6) for code analysis and generation.
Git backup is created before every RED-level change.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.ai_router import AIRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Change level definitions
# ---------------------------------------------------------------------------

CHANGE_LEVELS: dict[str, dict[str, Any]] = {
    "green": {
        "name": "Знания",
        "emoji": "🟢",
        "requires_admin": False,
        "requires_confirm": False,
    },
    "yellow": {
        "name": "Конфигурация",
        "emoji": "🟡",
        "requires_admin": False,
        "requires_confirm": True,
    },
    "red": {
        "name": "Код",
        "emoji": "🔴",
        "requires_admin": True,
        "requires_confirm": True,
    },
}

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

ANALYZE_REQUEST_PROMPT = """\
Ти си архитект на Streamlit приложение за строителни графици.
Потребителят иска промяна: '{user_request}'

Текуща структура на приложението:
{file_tree}

Определи:
1. Ниво на промяна:
   - 'green' = само .md файлове в knowledge/ (уроци, методики, skills)
   - 'yellow' = само .json файлове в config/ (productivities, app_config)
   - 'red' = .py файлове или requirements.txt

2. План за промяна — кои файлове ще бъдат засегнати и как

3. Описание на човешки език — какво точно ще се промени

4. Рискове — какво може да се счупи

Отговори САМО в JSON:
{{
  "level": "green"/"yellow"/"red",
  "description": "Човешко описание на промяната",
  "affected_files": [
    {{"path": "relative/path", "action": "create"/"modify"/"delete", "description": "какво се променя"}}
  ],
  "risks": ["риск 1", "риск 2"],
  "estimated_complexity": "low"/"medium"/"high",
  "user_impact": "Как ще засегне потребителите"
}}"""

GENERATE_CHANGES_PROMPT = """\
Генерирай конкретните промени за следния план:
{plan}

Текущо съдържание на файловете, които ще променяш:
{file_contents}

ПРАВИЛА:
- Python код и коментари: на АНГЛИЙСКИ
- Текстове видими от потребителя: на БЪЛГАРСКИ
- Запази type hints и docstrings
- Не чупи съществуваща функционалност
- Ако създаваш нов файл — включи ЦЕЛИЯ файл
- Ако модифицираш файл — покажи ТОЧНО кои секции се променят

Отговори в JSON:
{{
  "changes": [
    {{
      "file_path": "relative/path",
      "action": "create"/"modify",
      "content": "ПЪЛНО съдържание на файла (ако create)",
      "modifications": [
        {{
          "description": "какво се променя",
          "old_code": "точен стар код за замяна",
          "new_code": "нов код"
        }}
      ]
    }}
  ],
  "new_requirements": ["package>=version"],
  "test_instructions": "Как да се тества промяната"
}}"""


class SelfEvolution:
    """Manages self-modification of the application through AI-generated changes."""

    CHANGE_LEVELS = CHANGE_LEVELS

    def __init__(self, app_root: str, router: AIRouter) -> None:
        """Initialize the self-evolution manager.

        Args:
            app_root: Absolute path to the application root directory.
            router: AIRouter instance for Anthropic API calls.
        """
        self.app_root = app_root
        self.router = router
        self.admin_code: str | None = os.getenv("ADMIN_CODE")
        self.change_history: list[dict[str, Any]] = []
        self.pending_changes: dict[str, Any] | None = None

        # Load persistent history
        self._load_history()

    # ------------------------------------------------------------------
    # File tree helper
    # ------------------------------------------------------------------

    def _get_file_tree(self) -> str:
        """Build a string listing all .py, .md, .json files in the app."""
        root = Path(self.app_root)
        extensions = {".py", ".md", ".json", ".txt"}
        lines: list[str] = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix in extensions and "__pycache__" not in str(p):
                rel = p.relative_to(root)
                lines.append(str(rel))
        return "\n".join(lines) if lines else "(empty)"

    # ------------------------------------------------------------------
    # Analyze request
    # ------------------------------------------------------------------

    def analyze_request(self, user_request: str) -> dict[str, Any]:
        """Ask Anthropic to analyze the user request and determine change level.

        Args:
            user_request: Natural-language description of the desired change.

        Returns:
            Parsed dict with level, description, affected_files, risks, etc.
        """
        file_tree = self._get_file_tree()
        prompt = ANALYZE_REQUEST_PROMPT.format(
            user_request=user_request,
            file_tree=file_tree,
        )

        try:
            client = self.router._get_anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            raw = response.content[0].text if response.content else "{}"
            self.router._log_usage(
                "claude-sonnet-4-6",
                response.usage.input_tokens,
                response.usage.output_tokens,
                "evolution_analyze",
            )
            return self.router._parse_json_response(raw)
        except Exception as exc:
            logger.exception("Failed to analyze evolution request")
            return {
                "level": "red",
                "description": f"Грешка при анализ: {exc}",
                "affected_files": [],
                "risks": [str(exc)],
                "estimated_complexity": "high",
                "user_impact": "Неизвестно",
                "error": True,
            }

    # ------------------------------------------------------------------
    # Generate changes
    # ------------------------------------------------------------------

    def generate_changes(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Ask Anthropic to generate concrete file changes for the plan.

        Args:
            plan: The analysis plan dict from analyze_request().

        Returns:
            Parsed dict with changes list, new_requirements, test_instructions.
        """
        # Read current contents of affected files
        file_contents_parts: list[str] = []
        for af in plan.get("affected_files", []):
            fpath = Path(self.app_root) / af["path"]
            if fpath.exists() and fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    file_contents_parts.append(
                        f"--- {af['path']} ---\n{content}\n"
                    )
                except Exception:
                    file_contents_parts.append(
                        f"--- {af['path']} --- (не може да се прочете)\n"
                    )
            else:
                file_contents_parts.append(
                    f"--- {af['path']} --- (не съществува — ще бъде създаден)\n"
                )

        file_contents = "\n".join(file_contents_parts) if file_contents_parts else "(няма файлове)"

        prompt = GENERATE_CHANGES_PROMPT.format(
            plan=json.dumps(plan, ensure_ascii=False, indent=2),
            file_contents=file_contents,
        )

        try:
            client = self.router._get_anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.content[0].text if response.content else "{}"
            self.router._log_usage(
                "claude-sonnet-4-6",
                response.usage.input_tokens,
                response.usage.output_tokens,
                "evolution_generate",
            )
            return self.router._parse_json_response(raw)
        except Exception as exc:
            logger.exception("Failed to generate evolution changes")
            return {
                "changes": [],
                "new_requirements": [],
                "test_instructions": "",
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview_changes(self, plan: dict[str, Any], changes: dict[str, Any]) -> str:
        """Format changes for human review.

        Args:
            plan: The analysis plan dict.
            changes: The generated changes dict.

        Returns:
            Human-readable preview string.
        """
        level_info = self.CHANGE_LEVELS.get(plan.get("level", "red"), self.CHANGE_LEVELS["red"])
        lines: list[str] = []
        lines.append(f"{level_info['emoji']} **{level_info['name']}** промяна\n")
        lines.append(f"📋 **Преглед на промените:**\n")

        for change in changes.get("changes", []):
            action = change.get("action", "modify")
            fpath = change.get("file_path", "?")
            if action == "create":
                content = change.get("content", "")
                line_count = len(content.splitlines()) if content else 0
                lines.append(f"  ➕ Нов файл: `{fpath}` ({line_count} реда)")
            elif action == "modify":
                mods = change.get("modifications", [])
                lines.append(f"  ✏️ Модифициране: `{fpath}`")
                for mod in mods:
                    lines.append(f"     — {mod.get('description', '?')}")
            elif action == "delete":
                lines.append(f"  🗑️ Изтриване: `{fpath}`")

        # New requirements
        new_reqs = changes.get("new_requirements", [])
        if new_reqs:
            lines.append(f"\n📦 Нови пакети: {', '.join(new_reqs)}")
        else:
            lines.append("\n📦 Нови пакети: (няма)")

        # Risks
        risks = plan.get("risks", [])
        if risks:
            lines.append("\n⚠️ **Рискове:**")
            for risk in risks:
                lines.append(f"  — {risk}")

        # User impact
        impact = plan.get("user_impact", "")
        if impact:
            lines.append(f"\n👥 {impact}")

        # Test instructions
        test_inst = changes.get("test_instructions", "")
        if test_inst:
            lines.append(f"\n🧪 **Тест:** {test_inst}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Admin code verification
    # ------------------------------------------------------------------

    def verify_admin_code(self, input_code: str) -> bool:
        """Check if the provided code matches the ADMIN_CODE from .env.

        Args:
            input_code: The code entered by the user.

        Returns:
            True if the codes match (exact, case-sensitive).
        """
        if not self.admin_code:
            logger.warning("ADMIN_CODE is not set in .env — all admin checks will fail")
            return False
        return input_code == self.admin_code

    # ------------------------------------------------------------------
    # Git backup
    # ------------------------------------------------------------------

    def create_backup(self, description: str = "") -> dict[str, Any]:
        """Create a Git backup commit before applying changes.

        Args:
            description: Short description for the backup commit message.

        Returns:
            Dict with success, commit_hash, timestamp.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"backup: преди self-evolution промяна — {description}" if description else "backup: преди self-evolution промяна"

        try:
            # Stage all current changes
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if add_result.returncode != 0:
                logger.warning("git add failed: %s", add_result.stderr)

            # Commit
            commit_result = subprocess.run(
                ["git", "commit", "-m", message, "--allow-empty"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else "unknown"

            return {
                "success": True,
                "commit_hash": commit_hash,
                "timestamp": timestamp,
                "message": message,
            }
        except FileNotFoundError:
            logger.warning("Git is not installed or not in PATH")
            return {"success": False, "commit_hash": "", "timestamp": timestamp, "error": "Git не е наличен"}
        except subprocess.TimeoutExpired:
            logger.warning("Git backup timed out")
            return {"success": False, "commit_hash": "", "timestamp": timestamp, "error": "Git timeout"}
        except Exception as exc:
            logger.exception("Git backup failed")
            return {"success": False, "commit_hash": "", "timestamp": timestamp, "error": str(exc)}

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------

    def apply_changes(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply generated changes to the filesystem.

        Args:
            changes: The changes dict from generate_changes().

        Returns:
            Dict with applied count, failed count, errors, details.
        """
        results: list[dict[str, Any]] = []
        applied = 0
        failed = 0
        errors: list[str] = []

        for change in changes.get("changes", []):
            action = change.get("action", "modify")
            rel_path = change.get("file_path", "")
            abs_path = Path(self.app_root) / rel_path

            try:
                if action == "create":
                    # Create directories if needed
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                    content = change.get("content", "")
                    abs_path.write_text(content, encoding="utf-8")
                    results.append({"file": rel_path, "action": "created", "status": "ok"})
                    applied += 1

                elif action == "modify":
                    if not abs_path.exists():
                        errors.append(f"Файлът {rel_path} не съществува за модификация")
                        results.append({"file": rel_path, "action": "modify", "status": "error", "error": "not found"})
                        failed += 1
                        continue

                    current = abs_path.read_text(encoding="utf-8")

                    for mod in change.get("modifications", []):
                        old_code = mod.get("old_code", "")
                        new_code = mod.get("new_code", "")
                        if old_code and old_code in current:
                            current = current.replace(old_code, new_code, 1)
                        elif old_code:
                            errors.append(
                                f"Не може да се намери код за замяна в {rel_path}: "
                                f"{old_code[:80]}..."
                            )
                            failed += 1
                            continue

                    abs_path.write_text(current, encoding="utf-8")
                    results.append({"file": rel_path, "action": "modified", "status": "ok"})
                    applied += 1

                elif action == "delete":
                    if abs_path.exists():
                        abs_path.unlink()
                        results.append({"file": rel_path, "action": "deleted", "status": "ok"})
                        applied += 1
                    else:
                        errors.append(f"Файлът {rel_path} не съществува за изтриване")
                        failed += 1

            except Exception as exc:
                logger.exception("Failed to apply change to %s", rel_path)
                errors.append(f"{rel_path}: {exc}")
                results.append({"file": rel_path, "action": action, "status": "error", "error": str(exc)})
                failed += 1

        # Handle new requirements
        new_reqs = changes.get("new_requirements", [])
        if new_reqs:
            req_path = Path(self.app_root) / "requirements.txt"
            try:
                existing = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
                for req in new_reqs:
                    pkg_name = req.split(">=")[0].split("==")[0].split(">")[0].split("<")[0].strip()
                    if pkg_name not in existing:
                        existing += f"\n{req}"
                req_path.write_text(existing.strip() + "\n", encoding="utf-8")

                # Install new requirements
                pip_result = subprocess.run(
                    [sys.executable, "-m", "pip", "install"] + new_reqs,
                    cwd=self.app_root,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if pip_result.returncode != 0:
                    errors.append(f"pip install грешка: {pip_result.stderr[:200]}")
            except Exception as exc:
                errors.append(f"Грешка при инсталиране на пакети: {exc}")

        return {
            "applied": applied,
            "failed": failed,
            "errors": errors,
            "details": results,
        }

    # ------------------------------------------------------------------
    # Test changes
    # ------------------------------------------------------------------

    def test_changes(self) -> dict[str, Any]:
        """Run basic tests to verify the application still works.

        Returns:
            Dict with passed bool, tests_run, tests_passed, errors.
        """
        tests_run = 0
        tests_passed = 0
        errors: list[str] = []

        # Test 1: Syntax check all .py files
        root = Path(self.app_root)
        py_files = list(root.rglob("*.py"))
        py_files = [f for f in py_files if "__pycache__" not in str(f)]

        for py_file in py_files:
            tests_run += 1
            result = subprocess.run(
                [sys.executable, "-c", f"import py_compile; py_compile.compile(r'{py_file}', doraise=True)"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                tests_passed += 1
            else:
                errors.append(f"Синтактична грешка в {py_file.relative_to(root)}: {result.stderr[:200]}")

        # Test 2: Try importing app module
        tests_run += 1
        import_result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from src import self_evolution"],
            cwd=self.app_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if import_result.returncode == 0:
            tests_passed += 1
        else:
            errors.append(f"Import грешка: {import_result.stderr[:200]}")

        # Test 3: Check JSON configs are valid
        config_dir = root / "config"
        if config_dir.exists():
            for json_file in config_dir.glob("*.json"):
                tests_run += 1
                try:
                    json.loads(json_file.read_text(encoding="utf-8"))
                    tests_passed += 1
                except json.JSONDecodeError as exc:
                    errors.append(f"Невалиден JSON: {json_file.name}: {exc}")

        return {
            "passed": tests_passed == tests_run,
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(self, commit_hash: str) -> dict[str, Any]:
        """Rollback the application to a previous Git commit.

        Args:
            commit_hash: The commit hash to restore.

        Returns:
            Dict with success bool and restored_to hash.
        """
        try:
            result = subprocess.run(
                ["git", "reset", "--hard", commit_hash],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                # Log the rollback
                self._save_rollback_to_log(commit_hash)
                return {"success": True, "restored_to": commit_hash}
            else:
                return {"success": False, "restored_to": "", "error": result.stderr}
        except Exception as exc:
            logger.exception("Rollback failed")
            return {"success": False, "restored_to": "", "error": str(exc)}

    # ------------------------------------------------------------------
    # Change history
    # ------------------------------------------------------------------

    def get_change_history(self) -> list[dict[str, Any]]:
        """Return the list of all self-evolution changes."""
        return self.change_history

    def log_change(
        self,
        request: str,
        plan: dict[str, Any],
        backup_hash: str,
        status: str,
    ) -> None:
        """Record a change in the persistent evolution log.

        Args:
            request: The original user request.
            plan: The analysis plan dict.
            backup_hash: Git commit hash of the backup.
            status: 'applied' or 'rolled_back'.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "request": request,
            "level": plan.get("level", "unknown"),
            "description": plan.get("description", ""),
            "affected_files": [af.get("path", "") for af in plan.get("affected_files", [])],
            "backup_commit": backup_hash,
            "status": status,
            "applied_by": "потребител",
        }
        self.change_history.append(entry)
        self._save_history()

    def commit_changes(self, description: str) -> dict[str, Any]:
        """Create a Git commit after successfully applying changes.

        Args:
            description: Short description for the commit message.

        Returns:
            Dict with success, commit_hash.
        """
        message = f"feat: {description}"
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else "unknown"
            return {"success": result.returncode == 0, "commit_hash": commit_hash}
        except Exception as exc:
            logger.exception("Commit after evolution failed")
            return {"success": False, "commit_hash": "", "error": str(exc)}

    # ------------------------------------------------------------------
    # Persistent log (knowledge/evolution_log.json)
    # ------------------------------------------------------------------

    def _get_log_path(self) -> Path:
        """Return the path to the evolution log file."""
        return Path(self.app_root) / "knowledge" / "evolution_log.json"

    def _load_history(self) -> None:
        """Load change history from the persistent JSON file."""
        log_path = self._get_log_path()
        if log_path.exists():
            try:
                data = json.loads(log_path.read_text(encoding="utf-8"))
                self.change_history = data.get("changes", [])
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("Failed to load evolution log: %s", exc)
                self.change_history = []

    def _save_history(self) -> None:
        """Persist change history to the JSON file."""
        log_path = self._get_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Compute stats
        stats = {
            "total_changes": len(self.change_history),
            "green_changes": sum(1 for c in self.change_history if c.get("level") == "green"),
            "yellow_changes": sum(1 for c in self.change_history if c.get("level") == "yellow"),
            "red_changes": sum(1 for c in self.change_history if c.get("level") == "red"),
            "rollbacks": sum(1 for c in self.change_history if c.get("status") == "rolled_back"),
        }

        data = {
            "version": "1.0",
            "changes": self.change_history,
            "stats": stats,
        }

        try:
            log_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.exception("Failed to save evolution log")

    def _save_rollback_to_log(self, commit_hash: str) -> None:
        """Record a rollback event in the log."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "request": "Rollback",
            "level": "rollback",
            "description": f"Възстановяване към commit {commit_hash[:8]}",
            "affected_files": [],
            "backup_commit": commit_hash,
            "status": "rolled_back",
            "applied_by": "потребител",
        }
        self.change_history.append(entry)
        self._save_history()
