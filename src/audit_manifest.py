"""Кой код и коя конфигурация са произвели този артефакт.

НЕЗАВИСИМ ОДИТ 18.08.2026, P0.1: „В 40/40 сурови записа липсват git_commit,
config_hash, tech_chains_hash, resource_capacity_hash, model, provider,
max_tokens, timestamp.  Затова не може машинно да се каже кой прогон е преди
12.5, кой след 12.6 и кои ресурсни капацитети е ползвал."

И по-остро: „Документите казват, че `template_applicability_ok` е твърд флаг,
но в 0/40 прогона го има.  Документите казват, че показателят за
строителството е поправен, но в 32/32 той още е равен на срока.  Следователно
15/40 не е успеваемостта на текущата версия."

Прав е и по двете, и коренът е един: пакетът смесваше документи от една версия,
прогони от друга и XML от трета, а нищо в артефактите не позволяваше това да се
забележи машинно.  Всеки път се хващаше на ръка, от одитора.

Затова тук се прави ЕДИН отпечатък на състоянието — код плюс конфигурация плюс
модел — и всеки артефакт го носи.  Различни ID-та значат различни версии, а
сглобяването на пакет от смесени версии може да се спре, вместо да се обяснява
след това.

Отпечатъкът НЕ включва работната директория, времето (то е отделно поле) и
съдържанието на тръжните документи — те са вход, не версия.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: Файловете, чиято промяна МЕНИ резултата.  Всеки влиза в отпечатъка отделно,
#: за да се вижда кой точно се е разминал, а не само че нещо се е разминало.
_CONFIG_FILES = (
    "config/tech_chains.json",
    "config/resource_capacity.json",
    "config/productivities.json",
)

#: Модулите, които решават какъв ще е графикът.  Отпечатъкът им е по
#: СЪДЪРЖАНИЕ: git commit-ът не стига, защото работното дърво може да е мръсно.
_CODE_FILES = (
    "src/ai_processor.py",
    "src/work_package.py",
    "src/schedule_builder.py",
    "src/schedule_diagnostics.py",
    "src/duration_calculator.py",
    "src/export_xml.py",
)


def _хеш(път: Path) -> str:
    try:
        return hashlib.sha256(път.read_bytes()).hexdigest()[:16]
    except OSError:
        return "липсва"


def _git(*аргументи: str) -> str:
    try:
        return subprocess.run(("git", *аргументи), cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build_manifest(**допълнително: Any) -> dict:
    """Отпечатък на версията, която тече в този момент.

    `manifest_id` е хеш на всичко останало: два артефакта с еднакво ID са
    произведени от един и същ код и една и съща конфигурация.
    """
    конфигурации = {име: _хеш(ROOT / име) for име in _CONFIG_FILES}
    код = {име: _хеш(ROOT / име) for име in _CODE_FILES}

    мръсно = bool(_git("status", "--porcelain", "--", "src", "config", "tools"))
    данни: dict[str, Any] = {
        "git_commit": _git("rev-parse", "--short", "HEAD") or "неизвестен",
        "git_dirty": мръсно,
        "config_hashes": конфигурации,
        "code_hashes": код,
        "model": os.getenv("DEEPSEEK_MODEL", ""),
        "provider_base_url": os.getenv("DEEPSEEK_BASE_URL", ""),
        "controller": os.getenv("WORKER_MODEL", "") or "claude-opus-4-8",
        "max_tokens": int(os.getenv("WORKER_MAX_TOKENS", "48000")),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    данни.update(допълнително)

    # Времето и мръсното дърво НЕ влизат в ID-то: първото се мени при всяко
    # пускане, второто е предупреждение, а не различна версия.
    за_ид = {k: v for k, v in данни.items() if k not in ("timestamp", "git_dirty")}
    данни["manifest_id"] = hashlib.sha256(
        json.dumps(за_ид, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    return данни


def write_manifest(папка: Path, **допълнително: Any) -> dict:
    """Запиши `audit_manifest.json` в папката и го върни."""
    данни = build_manifest(**допълнително)
    папка.mkdir(parents=True, exist_ok=True)
    (папка / "audit_manifest.json").write_text(
        json.dumps(данни, ensure_ascii=False, indent=1), encoding="utf-8")
    return данни


def manifest_id(**допълнително: Any) -> str:
    return build_manifest(**допълнително)["manifest_id"]


def assert_same_version(артефакти: dict[str, str]) -> list[str]:
    """Кои артефакти са от друга версия — гейтът при сглобяване на пакет.

    Args:
        артефакти: {име на артефакт: manifest_id, който носи}.

    Returns:
        Списък с разминаванията; празен, когато всички са от една версия.
    """
    ид_та = {ид for ид in артефакти.values() if ид}
    if len(ид_та) <= 1:
        return []
    водещ = max(ид_та, key=lambda ид: sum(1 for v in артефакти.values() if v == ид))
    return [f"{име}: manifest_id={ид or 'ЛИПСВА'} (мнозинството е {водещ})"
            for име, ид in sorted(артефакти.items()) if ид != водещ]
