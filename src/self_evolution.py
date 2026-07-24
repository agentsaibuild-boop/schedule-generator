"""Self-evolution system — AI-driven application modification with 3-level change management.

Levels:
  - GREEN:  Knowledge files (.md) — no admin code, no confirmation
  - YELLOW: Config files (.json) — no admin code, requires confirmation
  - RED:    Code files (.py, requirements.txt) — requires admin code + confirmation

Uses the Anthropic controller model (see ai_router.MODEL_CONTROLLER) for code
analysis and generation. Git backup is created before every RED-level change.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from src.ai_router import MODEL_CONTROLLER

if TYPE_CHECKING:
    from src.ai_router import AIRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag — ИЗКЛЮЧЕНО по подразбиране
# ---------------------------------------------------------------------------
# Одит 2026-07-23: това приложение приема недоверени тръжни документи, праща
# съдържанието им към AI И позволява на AI да пише в собствения си код — в
# същия процес, със същите ключове и същия достъп до файловата система.
# Това е верига за отдалечено изпълнение на код, а не функция.
#
# Бариерите вътре (детерминистична класификация, ограничени пътища, валидиран
# pip, git бекъп) намаляват вероятността, но не и класа риск.  Освен това
# GREEN нивото не иска нито admin код, нито потвърждение, а четенето на
# файлове се случва ПРЕДИ каквато и да е проверка.
#
# Правилното решение е изнасяне в отделен процес: предложение → изолиран
# sandbox → статичен анализ и тестове → човешки review → PR → merge.
# Докато това не е направено, функцията стои ИЗКЛЮЧЕНА.
#
# Включва се съзнателно с ENABLE_SELF_EVOLUTION=1 в .env — и НЕ бива да се
# включва на машина, която обработва реални тръжни документи.
def is_enabled() -> bool:
    """Дали self-evolution е разрешен (по подразбиране: НЕ)."""
    return os.getenv("ENABLE_SELF_EVOLUTION", "").strip().lower() in {
        "1", "true", "yes", "да",
    }


DISABLED_MESSAGE = (
    "🔒 **Самопромяната на приложението е изключена.**\n\n"
    "Функцията позволява на AI да пише в кода на приложението — в същия "
    "процес, който обработва тръжни документи и държи API ключовете. "
    "Изключена е след одит на 2026-07-23.\n\n"
    "Ако наистина ти трябва: `ENABLE_SELF_EVOLUTION=1` в `.env`, на машина "
    "БЕЗ реални тръжни документи и без production ключове."
)

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

# Тежест на нивата — за сравнение „не по-опасно от обявеното".
_LEVEL_RANK: dict[str, int] = {"green": 0, "yellow": 1, "red": 2}

# Име на пакет по PEP 508 + евентуален спецификатор на версия.  Всичко
# извън този шаблон се отхвърля — иначе моделът може да подаде
# „package --index-url http://…" или произволен git+ssh адрес.
_SAFE_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"           # име
    r"(\[[A-Za-z0-9,._-]+\])?"               # extras
    r"\s*((==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9][A-Za-z0-9.*+!_-]*)?$"
)


def classify_path(rel_path: str) -> str:
    """Определи нивото на файл ДЕТЕРМИНИСТИЧНО, по пътя и разширението.

    ЗАЩО (P6): досега нивото идваше единствено от преценката на модела в
    `analyze_request`.  Моделът, който пише промяната, сам си оценяваше и
    опасността ѝ — и тази оценка беше единственото, което решаваше дали ще
    се иска admin код.  План, обявен за „green", можеше да съдържа промяна
    в `src/ai_router.py` и тя минаваше без никаква бариера.

    Тук нивото се извежда от самия файл и служи за КРЪСТОСАНА ПРОВЕРКА
    срещу обявеното от модела.

    Args:
        rel_path: Път спрямо корена на приложението.

    Returns:
        'green' | 'yellow' | 'red'.  При съмнение — 'red'.
    """
    path = PurePosixPath(str(rel_path).replace("\\", "/"))
    parts = path.parts
    suffix = path.suffix.lower()

    if suffix == ".md" and parts and parts[0] == "knowledge":
        return "green"
    if suffix == ".json" and parts and parts[0] == "config":
        return "yellow"
    return "red"


def max_level(rel_paths: list[str]) -> str:
    """Най-високото ниво сред подадените файлове ('green', ако няма файлове)."""
    level = "green"
    for rel_path in rel_paths:
        candidate = classify_path(rel_path)
        if _LEVEL_RANK[candidate] > _LEVEL_RANK[level]:
            level = candidate
    return level


def is_safe_requirement(spec: str) -> bool:
    """Дали редът е безобиден requirement (име + евентуална версия)."""
    return bool(_SAFE_REQUIREMENT_RE.match(spec.strip()))

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
        if not is_enabled():
            logger.warning("Self-evolution е изключен — %s отказан.", "analyze_request")
            return {"level": "red", "description": DISABLED_MESSAGE,
                    "affected_files": [], "risks": ["функцията е изключена"],
                    "error": "disabled"}

        file_tree = self._get_file_tree()
        prompt = ANALYZE_REQUEST_PROMPT.format(
            user_request=user_request,
            file_tree=file_tree,
        )

        try:
            client = self.router.get_anthropic_client()
            response = client.messages.create(
                model=MODEL_CONTROLLER,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            raw = response.content[0].text if response.content else "{}"
            self.router.log_usage(
                MODEL_CONTROLLER,
                response.usage.input_tokens,
                response.usage.output_tokens,
                "evolution_analyze",
            )
            return self.router.parse_json_response(raw)
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
        if not is_enabled():
            logger.warning("Self-evolution е изключен — %s отказан.", "generate_changes")
            return {"changes": [], "new_requirements": [], "test_instructions": "",
                    "error": "disabled"}

        # Read current contents of affected files
        file_contents_parts: list[str] = []
        for af in plan.get("affected_files", []):
            # Одит 2026-07-23: тук пътят се ползваше СУРОВ, докато
            # `resolve_safe_path` пазеше само записа.  Абсолютен път в плана
            # (който идва от модел) прочиташе произволен файл — включително
            # `.env` — и съдържанието му отиваше в промпта към Anthropic.
            # Тоест пробойна за изнасяне на ключове, не само за запис.
            try:
                fpath = self.resolve_safe_path(af.get("path", ""))
            except ValueError as exc:
                logger.error("Отказано четене при self-evolution: %s", exc)
                file_contents_parts.append(
                    f"--- {af.get('path')} --- (отказан път: {exc})\n"
                )
                continue

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
            client = self.router.get_anthropic_client()
            response = client.messages.create(
                model=MODEL_CONTROLLER,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.content[0].text if response.content else "{}"
            self.router.log_usage(
                MODEL_CONTROLLER,
                response.usage.input_tokens,
                response.usage.output_tokens,
                "evolution_generate",
            )
            return self.router.parse_json_response(raw)
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
        # Постоянно време: обикновеното `==` спира при първата различна буква
        # и позволява кодът да се отгатне символ по символ по времето за
        # отговор.  Никога не логвай нито въведения, нито очаквания код.
        return secrets.compare_digest(str(input_code), str(self.admin_code))

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
        if not is_enabled():
            return {"success": False, "error": "Самопромяната е изключена."}
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

    def resolve_safe_path(self, rel_path: str) -> Path:
        """Преобразувай път спрямо корена и се убеди, че НЕ излиза от него.

        ЗАЩО (P6): `Path(app_root) / rel_path` изглежда безобидно, но ако
        `rel_path` е АБСОЛЮТЕН, Python изхвърля основата — `Path("/app") /
        "/etc/passwd"` дава `/etc/passwd`.  Комбинирано с `..` това е
        произволен запис по файловата система с текста, който моделът е
        генерирал.

        Raises:
            ValueError: ако пътят е празен, абсолютен или излиза от корена.
        """
        raw = str(rel_path or "").strip()
        if not raw:
            raise ValueError("празен път")

        candidate = Path(raw.replace("\\", "/"))
        if candidate.is_absolute() or (len(raw) > 1 and raw[1] == ":"):
            raise ValueError(f"абсолютен път не е разрешен: {raw}")

        root = Path(self.app_root).resolve()
        target = (root / candidate).resolve()

        if target != root and root not in target.parents:
            raise ValueError(f"пътят излиза извън приложението: {raw}")

        return target

    def check_changes_against_level(
        self, changes: dict[str, Any], declared_level: str
    ) -> list[str]:
        """Сверѝ кои файлове се пипат срещу обявеното ниво.

        Моделът обявява нивото; тук проверяваме дали то отговаря на
        РЕАЛНИТЕ файлове.  Несъответствие = отказ, не предупреждение.

        Args:
            changes: Резултатът от `generate_changes()`.
            declared_level: Нивото от плана ('green'/'yellow'/'red').

        Returns:
            Списък с нарушения (празен = всичко е наред).
        """
        declared_rank = _LEVEL_RANK.get(declared_level, _LEVEL_RANK["red"])
        violations: list[str] = []

        for change in changes.get("changes", []):
            rel_path = change.get("file_path", "")
            actual = classify_path(rel_path)
            if _LEVEL_RANK[actual] > declared_rank:
                violations.append(
                    f"{rel_path} е ниво '{actual}', а планът е обявен като "
                    f"'{declared_level}'"
                )

        if changes.get("new_requirements") and declared_rank < _LEVEL_RANK["red"]:
            violations.append(
                "промяна в зависимостите (requirements) е ниво 'red', "
                f"а планът е обявен като '{declared_level}'"
            )

        return violations

    def apply_changes(
        self, changes: dict[str, Any], declared_level: str = "red"
    ) -> dict[str, Any]:
        """Apply generated changes to the filesystem.

        Args:
            changes: The changes dict from generate_changes().
            declared_level: Нивото, обявено от плана.  Всеки файл се сверява
                срещу него — план „green", който пипа `.py`, се отказва.

        Returns:
            Dict with applied count, failed count, errors, details.
        """
        if not is_enabled():
            logger.error("Self-evolution е изключен — apply_changes ОТКАЗАН.")
            return {"applied": 0, "failed": 1, "blocked": True, "details": [],
                    "errors": ["Самопромяната е изключена (ENABLE_SELF_EVOLUTION)."]}
        results: list[dict[str, Any]] = []
        applied = 0
        failed = 0
        errors: list[str] = []

        # Бариера 1: обявеното ниво трябва да покрива реалните файлове.
        violations = self.check_changes_against_level(changes, declared_level)
        if violations:
            logger.error(
                "Self-evolution отказан — нивото не отговаря на файловете: %s",
                "; ".join(violations),
            )
            return {
                "applied": 0,
                "failed": len(violations),
                "errors": [
                    "Промяната е отказана: обявеното ниво не отговаря на "
                    "засегнатите файлове."
                ] + violations,
                "details": [],
                "blocked": True,
            }

        for change in changes.get("changes", []):
            action = change.get("action", "modify")
            rel_path = change.get("file_path", "")

            # Бариера 2: пътят трябва да остане вътре в приложението.
            try:
                abs_path = self.resolve_safe_path(rel_path)
            except ValueError as exc:
                logger.error("Отказан път при self-evolution: %s", exc)
                errors.append(f"Отказан път {rel_path!r}: {exc}")
                results.append({
                    "file": rel_path, "action": action,
                    "status": "blocked", "error": str(exc),
                })
                failed += 1
                continue

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
            # Бариера 3: имената на пакетите идват от модел — приемат се само
            # чисти PEP 508 спецификации.  Без това „пакет --index-url http://…"
            # или „git+ssh://…" стигат директно до pip.
            unsafe = [r for r in new_reqs if not is_safe_requirement(str(r))]
            if unsafe:
                logger.error("Отказани requirements: %s", unsafe)
                errors.append(
                    "Отказани пакети (недопустим формат): " + ", ".join(map(str, unsafe))
                )
                # Брои се като провал, за да го види извикващият: той решава
                # дали да върне промяната по `failed`, не по `errors`.
                failed += len(unsafe)
                new_reqs = []

        if new_reqs:
            req_path = Path(self.app_root) / "requirements.txt"
            try:
                existing = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
                for req in new_reqs:
                    pkg_name = req.split(">=")[0].split("==")[0].split(">")[0].split("<")[0].strip()
                    if pkg_name not in existing:
                        existing += f"\n{req}"
                req_path.write_text(existing.strip() + "\n", encoding="utf-8")

                # Install new requirements.  `--` спира разчитането на флагове,
                # за да не може име на пакет да се представи за опция на pip.
                pip_result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--"] + list(new_reqs),
                    cwd=self.app_root,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if pip_result.returncode != 0:
                    errors.append(f"pip install грешка: {pip_result.stderr[:200]}")
            except Exception as exc:
                errors.append(f"Грешка при инсталиране на пакети: {exc}")

        # Auto-update documentation after successful changes
        if applied > 0:
            try:
                from src.docs_updater import DocsUpdater
                docs_updater = DocsUpdater(self.app_root)
                docs_result = docs_updater.run_all_updates()
                if docs_result["total"] > 0:
                    logger.info("Auto-updated %d doc sections after self-evolution", docs_result["total"])
            except Exception as exc:
                logger.warning("Docs auto-update failed (non-critical): %s", exc)

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
