# -*- coding: utf-8 -*-
"""Кой модел чете тръжните документи най-добре — единственото място, където
изборът на модел още мени графика.

ЗАЩО ОТДЕЛЕН БЕНЧМАРК.  `bench_workers.py` мери разделянето на участъци.  Този
път вече е второстепенен: при даден списък с клонове (от векторния четец или
от таблица) `generate_packages` минава детерминистично и НЕ вика модел —
измерено 26.08.2026: цялата генерация за Тръстеник е $0.00 и 0 повиквания.

Онова, което се плаща ВИНАГИ и мени всичко след себе си, е анализът на
документите.  От него излизат типът на поръчката, обявените срокове, обхватът
и имената на местата.  Сгрешен срок тук значи сгрешено темпо, сгрешен брой
екипи и сгрешен график — правилото „обявеният срок надделява" стъпва точно
върху този прочит.  Затова моделите се сравняват ТУК.

КАК СЕ ОЦЕНЯВА.  Не на око, а срещу неща, които в документите или ги има, или
ги няма:

  тип           разпозната ли е поръчката (инженеринг / мрежа / довеждащ ...)
  срокове       намерени ли са ОБЯВЕНИТЕ дни (за Илиянци 120 и 660)
  обхват        споменати ли са мрежите, които наистина се строят
  места         колко имена е върнал И КОЛКО ОТ ТЯХ ГИ ИМА в текста.
                Второто е проверка за измисляне: име, което не се среща
                буквално в документите, е халюцинация и се брои като такава.
  формат        мина ли JSON-ът без спасителния парсер

Употреба:
    python tools/bench_analysis.py --project "~/Desktop/2026" \
        --truth docs/бенчмарк/истина-илиянци.json
    python tools/bench_analysis.py --project ... --models gpt-5-mini,...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

OUT_DIR = ROOT / "docs" / "бенчмарк"

#: Кандидатите.  Работникът днес е първият; останалите са стъпала нагоре по
#: цена.  Vision-моделът (gemini) е тук, защото същият прочит му се пада, ако
#: някой ден анализът тръгне през него.
CANDIDATES: tuple[str, ...] = (
    "deepseek/deepseek-v4-flash",
    "openai/gpt-5-mini",
    "openai/gpt-5.6-luna",
    "google/gemini-3.6-flash",
    "x-ai/grok-4.3",
    "anthropic/claude-sonnet-5",
)


def _tariffs(models: list[str]) -> dict:
    from tools.bench_workers import openrouter_prices

    цени = openrouter_prices()
    if any(m not in цени for m in models):
        цени = openrouter_prices(refresh=True)
    return цени


def _текст(project: Path) -> str:
    """Целият конвертиран текст — за проверката дали местата ги има наистина."""
    парчета = []
    for f in sorted((project / "converted").glob("*.json")):
        if f.name == "_manifest.json":
            continue
        парчета.append(f.read_text(encoding="utf-8"))
    return "\n".join(парчета)


def _разбери(content: str) -> tuple[dict | None, bool]:
    """(разбран JSON, мина ли без спасяване)."""
    from src.ai_router import AIRouter

    try:
        return json.loads(content), True
    except Exception:                                   # noqa: BLE001
        pass
    try:
        return AIRouter.parse_json_response(content), False
    except Exception:                                   # noqa: BLE001
        return None, False


def _да(флаг: bool) -> str:
    return " да" if флаг else "  -"


#: Представки и кавички, които моделите слагат или махат по своя воля.  Първата
#: проба (31.08.2026) обяви 25 от 28 места за измислени — а те бяха в текста,
#: само че там пише `ул.Христо Ботев`, а моделът връща `ул. „Христо Ботев"`.
#: Проверка за халюцинация, която брои форматирането, не мери халюцинация.
_ЗА_ИЗХВЪРЛЯНЕ = ("ул.", "ул", "бул.", "бул", "кв.", "кв", "гр.", "с.",
                  "местност", "м-ст", "жк", "ж.к.")


def _плоско(текст: str) -> str:
    """Само буквите и цифрите, с малки букви — сравнението да е за СЪЩНОСТТА."""
    return "".join(з for з in текст.lower() if з.isalnum())


def _ключ_на_място(име: str) -> str:
    т = име.lower().strip()
    for представка in _ЗА_ИЗХВЪРЛЯНЕ:
        if т.startswith(представка):
            т = т[len(представка):].strip()
            break
    return _плоско(т)


def _оцени(разбран: dict | None, чист_json: bool, текст: str,
           истина: dict) -> dict:
    ако_нищо = {"тип_ок": False, "срокове_ок": False, "обхват_ок": False,
                "места": 0, "места_в_текста": 0, "измислени": 0,
                "чист_json": False, "точки": 0, "видян_тип": ""}
    if разбран is None:
        return ако_нищо

    цялото = json.dumps(разбран, ensure_ascii=False).lower()

    тип = str(разбран.get("project_type") or "").lower()
    тип_ок = истина["project_type"].lower() in тип

    срокове = json.dumps(разбран.get("deadlines"), ensure_ascii=False)
    срокове_ок = all(str(д) in срокове for д in истина["deadline_days"])

    обхват_ок = all(дума.lower() in цялото for дума in истина["scope_words"])

    места = [str(m).strip() for m in (разбран.get("locations") or [])
             if str(m).strip()]
    плосък = истина["_плосък_текст"]
    намерени = [m for m in места if _ключ_на_място(m) and
                _ключ_на_място(m) in плосък]
    в_текста = len(намерени)
    без_измисляне = bool(места) and в_текста / len(места) >= 0.9

    точки = (2 * тип_ок + 3 * срокове_ок + 2 * обхват_ок
             + 1 * чист_json + 2 * без_измисляне)
    return {"тип_ок": тип_ок, "срокове_ок": срокове_ок, "обхват_ок": обхват_ок,
            "места": len(места), "места_в_текста": в_текста,
            "измислени": len(места) - в_текста, "чист_json": чист_json,
            "точки": точки, "видян_тип": тип[:40],
            # СПИСЪЦИТЕ, не само броят: без тях „измислено" не може да се
            # провери от друг човек, а точно това искаме да е проверимо.
            "върнати_места": места[:120],
            "непотвърдени": [m for m in места if m not in намерени][:60]}


def _прогон(model: str, tariff: dict, project: Path, текст: str,
            истина: dict) -> dict:
    import src.ai_router as router_mod
    from src.ai_processor import AIProcessor
    from src.ai_router import AIRouter
    from src.file_manager import FileManager

    router = AIRouter()
    ai = AIProcessor(router=router)
    files = FileManager(base_path=str(project))

    старият = router_mod.MODEL_WORKER
    router_mod.MODEL_WORKER = model
    router_mod.PRICING.setdefault(model, {"input": tariff["input"],
                                          "output": tariff["output"]})
    # БЕЗ ТИХ ЗАМЕСТИТЕЛ — иначе провал на кандидата се записва като негов
    # резултат, а всъщност е отговор на контрольора (урокът от 12.08.2026).
    router.deepseek_available = True
    router.anthropic_available = False
    router._update_fallback_state()

    започна = time.monotonic()
    грешка = ""
    try:
        отговор = ai.analyze_documents(files.get_converted_files(),
                                       files.get_all_text())
        съдържание = отговор.get("analysis") or ""
    except Exception as exc:                            # noqa: BLE001
        съдържание, грешка = "", str(exc)[:160]
    секунди = time.monotonic() - започна
    router_mod.MODEL_WORKER = старият

    разбран, чист = _разбери(съдържание) if съдържание else (None, False)
    оценка = _оцени(разбран, чист, текст, истина)

    вх = sum(int(c.get("tokens_in") or 0) for c in router.usage_log)
    изх = sum(int(c.get("tokens_out") or 0) for c in router.usage_log)
    цена = вх * tariff["input"] + изх * tariff["output"]
    return {"model": model, "секунди": round(секунди, 1), "tokens_in": вх,
            "tokens_out": изх, "цена": round(цена, 5), "грешка": грешка,
            **оценка}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--truth", required=True,
                        help="какво пише в документите")
    parser.add_argument("--models", default=",".join(CANDIDATES))
    parser.add_argument("--out", default=str(OUT_DIR / "анализ.json"))
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    project = Path(os.path.expanduser(args.project))
    истина = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    текст = _текст(project)
    истина["_плосък_текст"] = _плоско(текст)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    цени = _tariffs(models)

    print(f"документи: {len(текст):,} знака · истина: {истина['name']}")
    записи = []
    for m in models:
        t = цени.get(m)
        if not t:
            print(f"{m:32s} НЯМА ТАРИФА — пропуснат")
            continue
        r = _прогон(m, t, project, текст, истина)
        записи.append(r)
        опашка = ("ГРЕШКА " + r["грешка"]) if r["грешка"] else ""
        print(f"{m:32s} точки {r['точки']:>2}/10  {r['секунди']:>6.0f}s  "
              f"${r['цена']:.4f}  места {r['места_в_текста']}/{r['места']}"
              f"  {опашка}", flush=True)

    записи.sort(key=lambda r: (-r["точки"], r["цена"]))
    print(f"\n{'модел':32s}{'точки':>6}{'тип':>5}{'срок':>6}{'обхват':>8}"
          f"{'json':>6}{'места':>8}{'измисл':>8}{'$':>9}{'сек':>7}")
    for r in записи:
        print(f"{r['model']:32s}{r['точки']:>4}/10{_да(r['тип_ок']):>5}"
              f"{_да(r['срокове_ок']):>6}{_да(r['обхват_ок']):>8}"
              f"{_да(r['чист_json']):>6}{r['места']:>8}{r['измислени']:>8}"
              f"{r['цена']:>9.4f}{r['секунди']:>7.0f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    за_запис = {k: v for k, v in истина.items() if not k.startswith("_плосък")}
    Path(args.out).write_text(
        json.dumps({"истина": за_запис, "прогони": записи},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
