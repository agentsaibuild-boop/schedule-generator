"""A/B сравнение на worker модели върху golden сценарии (Етап 4).

ЗАЩО така: след P2 продължителностите се смятат ДЕТЕРМИНИСТИЧНО от
`duration_calculator`.  Затова worker-ът вече НЕ се оценява по аритметика,
а по това, което само той може да сгреши:

  1. ПАРАМЕТРИ — `length_m`, `dn`, `material`, `method`.  Ако материалът
     липсва, калкулаторът пропуска задачата и продължителността пада обратно
     към предположение на модела.  Това е най-важната метрика.
  2. СТРУКТУРА — задължителни зависимости (Тласкател → КПС), място на
     дезинфекцията, разбивка по операции.
  3. ДИСЦИПЛИНА — без фантомни фази, без измислени топоними.

Оценяването е ДЕТЕРМИНИСТИЧНО (без LLM съдия), за да е повторяемо.

Употреба:
    python tools/worker_ab.py                  # всички кандидати
    python tools/worker_ab.py --dry-run        # само цена, без заявки
    python tools/worker_ab.py --models a,b     # избрани модели
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Конзолата на Windows е cp1252 по подразбиране — изходът е на кирилица.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import src.ai_router as router_mod  # noqa: E402
from src.ai_processor import AIProcessor  # noqa: E402
from src.ai_router import AIRouter  # noqa: E402
from src.duration_calculator import calculate_task_duration, is_pipe_task  # noqa: E402
from src.knowledge_manager import KnowledgeManager  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

CANDIDATES = [
    "deepseek/deepseek-chat",              # текущият worker
    "anthropic/claude-sonnet-5",
    "google/gemini-3.1-pro-preview",
]

# Таван на изходните токени за A/B-то.  Виж бележката в `run_one` защо
# продукционните 4096 не стигат за reasoning модели.
AB_MAX_TOKENS = 16000

# USD за токен — от OpenRouter, проверени 2026-07-22.  Нужни са, защото
# `_calculate_cost` търси модела в PRICING.
MODEL_PRICES: dict[str, dict[str, float]] = {
    "deepseek/deepseek-chat": {"input": 0.20 / 1e6, "output": 0.80 / 1e6},
    "anthropic/claude-sonnet-5": {"input": 2.00 / 1e6, "output": 10.00 / 1e6},
    "google/gemini-3.1-pro-preview": {"input": 2.00 / 1e6, "output": 12.00 / 1e6},
}

# ---------------------------------------------------------------------------
# Golden сценарии — от ACCURACY.md (реални проекти)
# ---------------------------------------------------------------------------

SCENARIOS: list[dict] = [
    {
        "name": "Горноград — довеждащ, DN300 CI, горски",
        "project_type": "довеждащ",
        "locations": ["Горноград"],
        "analysis": (
            "Обект: Довеждащ водопровод, гр. Горноград.\n"
            "Материал: чугунени тръби DN300 (сив чугун).\n"
            "Терен: горски, труднодостъпен.\n"
            "Обща дължина 673 м, разделена на участъци: 74 м, 128 м, 117 м, 354 м.\n"
            "Дейности по КСС: изкоп на траншея, доставка и полагане на тръби DN300,\n"
            "засипване и уплътняване, хидравлично изпитване, дезинфекция и промивка.\n"
            "Един екип, последователно изпълнение."
        ),
        "checks": {
            "expect_material": "CI",
            "expect_dn": 300,
            "forbid_phases": ["административна подготовка", "въвеждане вобд", "демобилизация"],
        },
    },
    {
        "name": "Среднево — единичен участък, DN500 PE, 720м",
        "project_type": "довеждащ",
        "locations": ["Среднево"],
        "analysis": (
            "Обект: Довеждащ водопровод, гр. Среднево.\n"
            "Материал: полиетиленови тръби PE 100 RC, DN500.\n"
            "Терен: черен път.\n"
            "Обща дължина 720 м, един участък.\n"
            "Дейности по КСС: изкоп, полагане DN500 PE, засипване,\n"
            "изпитване на якост, изпитване на спад на налягане, дезинфекция.\n"
            "Един екип."
        ),
        "checks": {
            "expect_material": "PE",
            "expect_dn": 500,
            "forbid_phases": ["административна подготовка", "въвеждане вобд"],
        },
    },
    {
        "name": "Опитно — инженеринг, канализация + КПС",
        "project_type": "инженеринг",
        "locations": ["Опитно"],
        "analysis": (
            "Обект: Канализационна мрежа и КПС, с. Опитно.\n"
            "Канализационни клонове PVC DN315: Кл.16 — 351 м, Кл.20 — 635 м,\n"
            "Кл.22 — 70 м, Кл.30 — 210 м.\n"
            "Тласкател PE DN160, дължина 480 м.\n"
            "КПС (Канална Помпена Станция) — 1 брой.\n"
            "Монтаж на ревизионни шахти РШ — 42 броя.\n"
            "Терен: черен път и асфалт.\n"
            "1-2 екипа, канализацията се строи от долу нагоре по гравитация."
        ),
        "checks": {
            "expect_dn": 315,
            "require_dependency": ("тласкател", "кпс"),
            "forbid_phases": ["административна подготовка", "въвеждане вобд", "демобилизация"],
        },
    },
]


# ---------------------------------------------------------------------------
# Оценяване
# ---------------------------------------------------------------------------

def score_schedule(tasks: list[dict], checks: dict, analysis: str) -> dict:
    """Оцени един генериран график срещу golden очакванията.

    Всяка метрика е 0..1.  Никъде не се ползва LLM — резултатът е повторяем.
    """
    result: dict = {}
    pipe_tasks = [t for t in tasks if is_pipe_task(t)]
    n_pipe = len(pipe_tasks) or 1

    # --- 1. Параметри (най-важното след P2) ---
    def _has(task: dict, *fields: str) -> bool:
        return any(
            task.get(f) not in (None, "", 0) for f in fields
        )

    result["param_dn"] = sum(_has(t, "dn", "diameter") for t in pipe_tasks) / n_pipe
    result["param_length"] = sum(
        _has(t, "length_m", "length") or str(t.get("unit", "")).lower() in ("м", "m")
        for t in pipe_tasks
    ) / n_pipe
    result["param_material"] = sum(_has(t, "material") for t in pipe_tasks) / n_pipe

    # Най-прекият показател: колко от тръбните задачи калкулаторът може да
    # сметне.  Останалите падат обратно към предположението на модела.
    computable = 0
    for task in pipe_tasks:
        try:
            if calculate_task_duration(task).days is not None:
                computable += 1
        except Exception:
            pass
    result["calculator_coverage"] = computable / n_pipe

    # --- 2. Материалът е правилният (урок #35) ---
    if "expect_material" in checks:
        want = checks["expect_material"]
        materials = {str(t.get("material", "")).upper() for t in pipe_tasks}
        materials.discard("")
        result["material_correct"] = 1.0 if materials == {want} else (
            0.5 if want in materials else 0.0
        )

    # --- 3. DN е правилният ---
    if "expect_dn" in checks:
        want_dn = checks["expect_dn"]
        dns = set()
        for t in pipe_tasks:
            for f in ("dn", "diameter"):
                v = t.get(f)
                if isinstance(v, (int, float)):
                    dns.add(int(v))
        result["dn_present"] = 1.0 if want_dn in dns else 0.0

    # --- 4. Задължителна зависимост ---
    if "require_dependency" in checks:
        pred_word, succ_word = checks["require_dependency"]
        # ID-тата идват от модел и не са гарантирано низове — попадал е dict,
        # който гърми при вкарване в set.  Оценяваме модели, значи входът е
        # ненадежден по определение.
        def _hashable_id(task: dict):
            tid = task.get("id")
            return tid if isinstance(tid, (str, int)) else None

        by_id = {
            _hashable_id(t): t for t in tasks if _hashable_id(t) is not None
        }
        preds = [t for t in tasks if pred_word in str(t.get("name", "")).lower()]
        succs = [t for t in tasks if succ_word in str(t.get("name", "")).lower()]
        ok = 0.0
        if preds and succs:
            pred_ids = {i for i in (_hashable_id(p) for p in preds) if i is not None}
            for s in succs:
                deps = {
                    d for d in (s.get("dependencies") or [])
                    if isinstance(d, (str, int))
                }
                if deps & pred_ids:
                    ok = 1.0
                    break
                # Косвено: наследникът започва след края на предшественика
                s_start = s.get("start_day", 0)
                if all(
                    s_start > by_id.get(pid, {}).get("end_day", 0)
                    for pid in pred_ids
                    if pid in by_id
                ):
                    ok = max(ok, 0.5)
        result["required_dependency"] = ok

    # --- 5. Фантомни фази (урок #41) ---
    forbidden = checks.get("forbid_phases", [])
    hits = [
        t.get("name", "")
        for t in tasks
        if any(f in str(t.get("name", "")).lower() for f in forbidden)
    ]
    result["no_phantom_phases"] = 0.0 if hits else 1.0
    result["_phantom_hits"] = hits

    # --- 6. Халюцинирани топоними ---
    warnings = AIProcessor._validate_task_locations(tasks, [], analysis)
    result["no_hallucinated_places"] = 1.0 if not warnings else max(
        0.0, 1.0 - len(warnings) / max(len(tasks), 1)
    )
    result["_hallucinations"] = len(warnings)

    # --- 7. Разбивка по операции (не 1 ред на участък) ---
    result["decomposition"] = min(len(tasks) / 12.0, 1.0)

    return result


WEIGHTS = {
    "calculator_coverage": 3.0,   # най-важно: без това P2 не работи
    "param_material": 2.0,
    "material_correct": 2.0,
    "param_dn": 1.0,
    "param_length": 1.0,
    "dn_present": 1.0,
    "required_dependency": 2.0,
    "no_phantom_phases": 1.5,
    "no_hallucinated_places": 1.0,
    "decomposition": 0.5,
}


def composite(scores: dict) -> float:
    """Претеглен общ резултат 0..100."""
    total = 0.0
    weight_sum = 0.0
    for key, weight in WEIGHTS.items():
        if key in scores:
            total += scores[key] * weight
            weight_sum += weight
    return 100.0 * total / weight_sum if weight_sum else 0.0


# ---------------------------------------------------------------------------
# Изпълнение
# ---------------------------------------------------------------------------

def run_one(model: str, scenario: dict) -> dict:
    """Генерирай график с даден модел и го оцени."""
    original = router_mod.MODEL_WORKER
    original_tokens = router_mod._MAX_TOKENS_CHAT
    router_mod.MODEL_WORKER = model
    # Продукционният таван е 4096 — достатъчен за DeepSeek V3 (не-reasoning),
    # но reasoning моделите изразходват част от бюджета за разсъждение ПРЕДИ
    # да напишат JSON-а и отговорът излиза отрязан (`finish_reason=length`).
    # Измерено 2026-07-22: Gemini 3.1 Pro — 4990 знака reasoning, out=4092/4096,
    # JSON прекъснат по средата.  Без това вдигане A/B-то мери тавана, а не
    # модела.
    router_mod._MAX_TOKENS_CHAT = AB_MAX_TOKENS
    # `_calculate_cost` прави PRICING[MODEL_WORKER] като резервен вариант —
    # без запис за подменения модел това е KeyError, който изглежда като
    # „моделът не работи".
    router_mod.PRICING.setdefault(model, MODEL_PRICES.get(model, {"input": 0.0, "output": 0.0}))
    try:
        router = AIRouter()
        router.anthropic_available = False   # само worker пътят
        proc = AIProcessor(router=router, knowledge_manager=KnowledgeManager(str(ROOT / "knowledge")))

        started = time.time()
        result = proc.generate_schedule(
            analysis={"analysis": scenario["analysis"]},
            project_type=scenario["project_type"],
            all_text=scenario["analysis"],
            extra_locations=scenario["locations"],
        )
        elapsed = time.time() - started

        schedule = result.get("schedule")
        if isinstance(schedule, str):
            schedule = AIRouter.parse_json_response(schedule)
        tasks = schedule.get("tasks", []) if isinstance(schedule, dict) else (
            schedule if isinstance(schedule, list) else []
        )

        scores = score_schedule(tasks, scenario["checks"], scenario["analysis"])
        record = {
            "model": model,
            "scenario": scenario["name"],
            "tasks": len(tasks),
            "seconds": elapsed,
            "cost": result.get("total_cost", 0.0),
            "scores": scores,
            "composite": composite(scores),
            "status": result.get("status"),
        }
        if not tasks:
            # Празен график е резултат, който трябва да се обясни, не да се
            # отчете като „лош модел".
            record["diagnostic"] = {
                "status": result.get("status"),
                "error": str(result.get("error", ""))[:200],
                "remaining_issues": result.get("remaining_issues", [])[:3],
                "parse_error": result.get("parse_error"),
            }
        return record
    finally:
        router_mod.MODEL_WORKER = original
        router_mod._MAX_TOKENS_CHAT = original_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(CANDIDATES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "tools" / "worker_ab_results.json"))
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.dry_run:
        print(f"Ще пусне {len(models)} модела × {len(SCENARIOS)} сценария "
              f"= {len(models) * len(SCENARIOS)} генерирания.")
        for m in models:
            print(f"  - {m}")
        return 0

    results: list[dict] = []
    for model in models:
        for scenario in SCENARIOS:
            print(f"→ {model} :: {scenario['name']}", flush=True)
            try:
                res = run_one(model, scenario)
            except Exception as exc:
                print(f"  ГРЕШКА: {type(exc).__name__}: {str(exc)[:160]}")
                res = {"model": model, "scenario": scenario["name"],
                       "error": str(exc)[:300], "composite": 0.0, "cost": 0.0}
            results.append(res)
            if "error" not in res:
                print(f"  {res['composite']:.1f}/100 | {res['tasks']} задачи | "
                      f"{res['seconds']:.0f}s | ${res['cost']:.4f}", flush=True)

    Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nЗаписано: {args.out}")
    print_summary(results)
    return 0


def print_summary(results: list[dict]) -> None:
    """Обобщение по модел."""
    by_model: dict[str, list[dict]] = {}
    for r in results:
        by_model.setdefault(r["model"], []).append(r)

    print("\n" + "=" * 78)
    print(f"{'модел':34} {'общо':>6} {'калк.покр.':>11} {'материал':>9} {'цена':>9}")
    print("-" * 78)
    rows = []
    for model, runs in by_model.items():
        ok = [r for r in runs if "error" not in r]
        if not ok:
            print(f"{model[:34]:34} {'ГРЕШКА':>6}")
            continue
        comp = sum(r["composite"] for r in ok) / len(ok)
        cov = sum(r["scores"].get("calculator_coverage", 0) for r in ok) / len(ok)
        mat = sum(r["scores"].get("param_material", 0) for r in ok) / len(ok)
        cost = sum(r["cost"] for r in runs)
        rows.append((comp, model, cov, mat, cost))
    for comp, model, cov, mat, cost in sorted(rows, reverse=True):
        print(f"{model[:34]:34} {comp:6.1f} {cov*100:10.0f}% {mat*100:8.0f}% {cost:9.4f}")
    print("=" * 78)


if __name__ == "__main__":
    raise SystemExit(main())
