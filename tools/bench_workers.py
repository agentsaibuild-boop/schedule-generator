"""Колко струва ЕДИН използваем график — бенчмарк на работниците.

ЗАЩО този файл съществува отделно от `worker_ab.py`: онзи мери КАЧЕСТВО върху
измислени golden сценарии.  Тук въпросът е друг и е паричен — при кой работник
излиза чист график и на каква цена, върху РЕАЛНИЯ търг.  Затова:

  * ползва същия пакетен път и същите показатели като `rerun_series.py`
    (`_prepare`, `_metrics`) — числата са сравними с телеметрията в
    `docs/прогони/`, а не нова, несравнима мярка;
  * подготовката (анализ + отсечки) е КЕШИРАНА — плаща се само генерацията,
    иначе бенчмаркът мери четенето на чертежа веднъж на модел;
  * цената се смята ОТ ТОКЕНИТЕ × ЖИВАТА тарифа на OpenRouter, а не от
    `ai_router.PRICING`.  Проверено 12.08.2026: `PRICING` държи deepseek на
    $0.28/$0.42 за 1M, а живата тарифа е $0.257/$1.029 — тоест всички записани
    досега цени за изхода са занижени 2.45×.  Бенчмарк, който наследи тази
    грешка, ще класира моделите по грешна ос.

Двата въпроса, на които отговаря таблицата:

    цена за прогон          — колко струва един опит
    цена за ЧИСТ график     — цена за прогон ÷ дял чисти прогони
                              (това е истинската цена; прогон, който не дава
                               използваем график, е платен и изхвърлен)

Употреба:
    python tools/bench_workers.py --project "~/Desktop/2026" --runs 3
    python tools/bench_workers.py --project ... --models qwen/qwen3.7-flash,deepseek/deepseek-chat
    python tools/bench_workers.py --dry        # прогноза от измерен профил, без заявки
    python tools/bench_workers.py --prices     # само живите тарифи на кандидатите
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# Конзолата на Windows е cp1252 по подразбиране — изходът е на кирилица.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bench")

OUT_DIR = ROOT / "docs" / "бенчмарк"
PRICES_CACHE = OUT_DIR / "цени.json"
PROFILE_PATH = OUT_DIR / "профил.json"
RESULTS_PATH = OUT_DIR / "резултати.json"

#: Стълбица от евтино към скъпо.  Безплатният е основа (0 лв., но и той има
#: цена — вижда се в дела чисти прогони), Sonnet е таванът за сравнение.
CANDIDATES: tuple[str, ...] = (
    "openai/gpt-oss-20b:free",
    "qwen/qwen3.7-flash",
    "openai/gpt-oss-120b",
    "z-ai/glm-4.7-flash",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-chat",
    "anthropic/claude-sonnet-5",
)

#: Пълната серия, с която се сравнява прогнозата (4 серии × 10 прогона).
SERIES_RUNS = 40


# ---------------------------------------------------------------------------
# Живи тарифи
# ---------------------------------------------------------------------------

def openrouter_prices(refresh: bool = False) -> dict[str, dict[str, float]]:
    """{model_id: {"input": $/токен, "output": $/токен, "out_cap": int}}.

    Кешира се, защото при 40+ прогона тарифата не бива да се тегли на всеки
    прогон, но и не бива да е твърдо записана в кода — точно това счупи
    отчитането досега.
    """
    if PRICES_CACHE.exists() and not refresh:
        return json.loads(PRICES_CACHE.read_text(encoding="utf-8"))

    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as resp:
        data = json.load(resp)["data"]

    prices: dict[str, dict[str, float]] = {}
    for model in data:
        pricing = model.get("pricing") or {}
        try:
            prices[model["id"]] = {
                "input": float(pricing.get("prompt", 0.0)),
                "output": float(pricing.get("completion", 0.0)),
                "out_cap": int((model.get("top_provider") or {}).get(
                    "max_completion_tokens") or 0),
                "ctx": int(model.get("context_length") or 0),
            }
        except (TypeError, ValueError):
            continue

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PRICES_CACHE.write_text(json.dumps(prices, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return prices


def price_of(prices: dict, model: str) -> dict[str, float]:
    """Тарифата на модела; при непознат модел — нула и видима бележка."""
    entry = prices.get(model)
    if entry is None:
        logger.warning("%s няма тарифа в OpenRouter — цената ще излезе 0", model)
        return {"input": 0.0, "output": 0.0, "out_cap": 0, "ctx": 0}
    return entry


# ---------------------------------------------------------------------------
# Един прогон
# ---------------------------------------------------------------------------

def _usage_since(router: Any, mark: int) -> list[dict]:
    return list(router.usage_log[mark:])


def run_once(prep: dict, model: str, tariff: dict, *, use_segments: bool,
             repair_rounds: int, run: int, max_tokens: int = 16000) -> dict:
    """Един пакетен прогон с подменен работник.  Цената — от токените."""
    from tools.rerun_series import _metrics

    import src.ai_router as router_mod

    ai = prep["ai"]
    router = ai.router

    original_model = router_mod.MODEL_WORKER
    original_cap = os.environ.get("WORKER_MAX_TOKENS")
    router_mod.MODEL_WORKER = model

    # БЕЗ ТИХ ЗАМЕСТИТЕЛ.  Първата проба (12.08.2026) даде 18 прогона, в които
    # НИТО ЕДИН не стигна до поискания работник: `chat()` при отказ вдига
    # `deepseek_available = False` за целия живот на инстанцията и пада към
    # Anthropic.  Рутерът е един за всичките модели, затова една засечка
    # превърна бенчмарка на шестте работника в бенчмарк на контрольора — шест
    # „различни" модела с еднакви 247/248 задачи и $3.92 сметка при Opus.
    #
    # Тук провалът на работника ТРЯБВА да се запише като провал НА НЕГО.
    # Затова: флагът се вдига наново преди всеки прогон, а резервният път се
    # изключва за времето на измерването.
    router.deepseek_available = True
    saved_anthropic = router.anthropic_available
    router.anthropic_available = False
    router._update_fallback_state()
    # Таванът на изхода не може да е над това, което доставчикът дава — иначе
    # заявката гърми с 400 и прогонът се записва като „модел, който не работи".
    cap = tariff.get("out_cap") or 0
    os.environ["WORKER_MAX_TOKENS"] = str(min(max_tokens, cap) if cap else max_tokens)
    os.environ["PACKAGE_REPAIR_ROUNDS"] = str(repair_rounds)
    # `_calculate_cost` пада към PRICING[MODEL_WORKER]; без запис това е
    # KeyError, който изглежда като провал на модела.
    router_mod.PRICING.setdefault(model, {"input": tariff["input"],
                                          "output": tariff["output"]})

    # Причината, ако прогонът не стигне до модела.  Рутерът гълта отказите на
    # доставчика и връща „и двата модела са недостъпни", така че без този
    # прихващач всяка засечка на OpenRouter изглежда в таблицата като лош
    # модел.  Проба 13.08.2026: три прогона паднаха за 5–17 секунди и записът
    # каза само „заместен от никого" — а моделът беше изправен.
    captured: list[str] = []

    class _LastRouterError(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                # САМО диагнозата, без суровия отговор на модела: в него влизат
                # имена на обекта и улиците, а резултатите отиват в git.
                msg = record.getMessage()
                captured.append(msg.split("\n", 1)[0].split("{", 1)[0].strip()[:200])

    sniffer = _LastRouterError()
    logging.getLogger("src.ai_router").addHandler(sniffer)

    mark = len(router.usage_log)
    started = time.monotonic()
    try:
        result = ai.generate_schedule_packaged(
            prep["analysis"], prep["boq_index"], num_teams=2,
            locations=prep["locations"],
            segments=prep["segments"] if use_segments else None)
    except Exception as exc:                            # noqa: BLE001
        result = None
        failure = exc
    finally:
        elapsed = time.monotonic() - started
        logging.getLogger("src.ai_router").removeHandler(sniffer)
        router_mod.MODEL_WORKER = original_model
        router.anthropic_available = saved_anthropic
        router._update_fallback_state()
        if original_cap is None:
            os.environ.pop("WORKER_MAX_TOKENS", None)
        else:
            os.environ["WORKER_MAX_TOKENS"] = original_cap

    # Токените и цената — разделени по модел, за да се вижда колко е работникът
    # и колко контрольорът.  Смесени в едно число, скъпият контрольор изглежда
    # като скъп работник.
    calls = _usage_since(router, mark)
    per_model: dict[str, dict[str, float]] = {}
    for call in calls:
        row = per_model.setdefault(call["model"], {"in": 0, "out": 0, "calls": 0})
        row["in"] += int(call.get("tokens_in") or 0)
        row["out"] += int(call.get("tokens_out") or 0)
        row["calls"] += 1

    worker = per_model.get(model, {"in": 0, "out": 0, "calls": 0})
    worker_cost = worker["in"] * tariff["input"] + worker["out"] * tariff["output"]
    other_cost = sum(c.get("cost_usd") or 0.0 for c in calls
                     if c.get("model") != model)
    # Прогон без НИТО ЕДНО извикване към поискания работник не е негов резултат.
    # Без тази проверка чужд модел мълчаливо влиза в таблицата под чуждо име.
    answered_by = sorted(per_model)
    substituted = worker["calls"] == 0

    if result is None or substituted:
        return {"model": model, "run": run, "status": "error",
                "error": (str(failure)[:300] if result is None
                          else (captured[-1] if captured else
                                f"заместен от {', '.join(answered_by) or 'никого'}")),
                "router_warnings": captured[-3:],
                "clean": False, "exportable": False, "substituted": substituted,
                "answered_by": answered_by,
                "tokens_in": worker["in"], "tokens_out": worker["out"],
                "calls": worker["calls"], "cost_worker": round(worker_cost, 6),
                "cost_other": round(other_cost, 6), "seconds": round(elapsed, 1)}

    record = _metrics(run, result, elapsed, prep["boq_index"])
    record.update({
        "model": model,
        "substituted": False,
        "answered_by": answered_by,
        "tokens_in": worker["in"],
        "tokens_out": worker["out"],
        "calls": worker["calls"],
        # `cost` от `_metrics` идва от PRICING и е за сверка, не за сметката.
        "cost_recorded": record.get("cost", 0.0),
        "cost_worker": round(worker_cost, 6),
        "cost_other": round(other_cost, 6),
        "cost": round(worker_cost + other_cost, 6),
    })
    # Големите списъци не влизат в бенчмарка — за тях е телеметрията на сериите.
    for key in ("uncovered_refs", "missing_refs", "unplaced", "over_refs",
                "short_refs", "unknown_refs", "phase_days"):
        record.pop(key, None)
    return record


# ---------------------------------------------------------------------------
# Обобщение
# ---------------------------------------------------------------------------

def summarise(model: str, runs: list[dict], tariff: dict) -> dict:
    ok = [r for r in runs if r.get("status") != "error"]
    clean = [r for r in ok if r.get("clean")]
    exportable = [r for r in ok if r.get("exportable")]
    # ПЛАТЕНИ прогони: всички, стигнали до този работник — включително онези,
    # които са се провалили.  Провалът също се плаща и цената за чист график
    # ще е лъжа, ако провалените се изхвърлят от знаменателя.  Изключват се
    # само заместените (нула токени, чужд модел).
    paid = [r for r in runs if r.get("calls", 0) > 0]
    costs = [r.get("cost_worker", 0.0) for r in paid]
    avg_cost = statistics.fmean(costs) if costs else 0.0

    clean_rate = len(clean) / len(paid) if paid else 0.0
    # При нула чисти прогона цената за чист график НЕ е безкрайност, а „поне
    # толкова" — иначе таблицата мълчи точно там, където е най-важна.
    if clean:
        per_clean: float | None = avg_cost / clean_rate
        per_clean_note = ""
    else:
        per_clean = None
        per_clean_note = f"≥ {avg_cost * len(paid):.4f} (0 чисти от {len(paid)})"

    return {
        "model": model,
        "tariff_in_1m": round(tariff["input"] * 1e6, 3),
        "tariff_out_1m": round(tariff["output"] * 1e6, 3),
        "runs": len(runs),
        "errors": len(runs) - len(ok),
        "substituted": sum(1 for r in runs if r.get("substituted")),
        "clean": len(clean),
        "exportable": len(exportable),
        "paid": len(paid),
        "failed": len(paid) - len(ok),
        "avg_tokens_in": round(statistics.fmean([r["tokens_in"] for r in paid])) if paid else 0,
        "avg_tokens_out": round(statistics.fmean([r["tokens_out"] for r in paid])) if paid else 0,
        "avg_tasks": round(statistics.fmean([r.get("tasks", 0) for r in ok])) if ok else 0,
        "avg_uncovered": round(statistics.fmean([r.get("uncovered", 0) for r in ok]), 1) if ok else 0,
        "avg_seconds": round(statistics.fmean([r.get("seconds", 0) for r in paid]), 1) if paid else 0,
        "cost_per_run": round(avg_cost, 5),
        "cost_per_clean": round(per_clean, 4) if per_clean is not None else None,
        "cost_per_clean_note": per_clean_note,
        "cost_full_series": round(avg_cost * SERIES_RUNS, 3),
    }


def print_table(rows: list[dict]) -> None:
    print()
    print("=" * 108)
    print(f"{'модел':32s} {'$/1M вх/изх':>14s} {'чисти':>7s} {'провал':>6s} "
          f"{'замест':>6s} {'$/прогон':>10s} {'$/чист график':>15s} {'$/серия 4×10':>13s}")
    print("-" * 108)
    for row in sorted(rows, key=lambda r: r["cost_per_run"]):
        clean = f"{row['clean']}/{row['paid']}"
        per_clean = (f"{row['cost_per_clean']:.4f}" if row["cost_per_clean"] is not None
                     else row["cost_per_clean_note"] or "—")
        print(f"{row['model']:32s} "
              f"{row['tariff_in_1m']:6.3f}/{row['tariff_out_1m']:<7.3f} "
              f"{clean:>7s} {row['failed']:>6d} {row['substituted']:>6d} "
              f"{row['cost_per_run']:>10.5f} {per_clean:>15s} "
              f"{row['cost_full_series']:>13.3f}")
    print("=" * 108)


# ---------------------------------------------------------------------------
# Прогноза без заявки
# ---------------------------------------------------------------------------

def dry_projection(models: list[str], prices: dict, profile: dict) -> None:
    """Цена по измерен профил на токените — без нито една заявка.

    Профилът е от РЕАЛНИ прогони (виж `--project`).  Изходните токени варират
    по модел, затова прогнозата казва „при същия профил", не „ще струва".
    """
    tin = profile["tokens_in"]
    tout = profile["tokens_out"]
    src = profile.get("източник", "неизвестен")
    print(f"\nПрофил на прогон: {tin} входни / {tout} изходни токена (от {src})")
    print("Прогноза при СЪЩИЯ профил — качеството НЕ е измерено:\n")
    print(f"{'модел':34s} {'$/1M вх/изх':>15s} {'$/прогон':>10s} {'$/серия 4×10':>13s}")
    print("-" * 76)
    rows = []
    for model in models:
        tariff = price_of(prices, model)
        cost = tin * tariff["input"] + tout * tariff["output"]
        rows.append((cost, model, tariff))
    for cost, model, tariff in sorted(rows):
        print(f"{model:34s} {tariff['input']*1e6:6.3f}/{tariff['output']*1e6:<8.3f} "
              f"{cost:>10.5f} {cost * SERIES_RUNS:>13.3f}")
    print("-" * 76)
    print("Цената за ЧИСТ график = $/прогон ÷ дела чисти прогони.  При 1 чист от")
    print("10 (измереното на 10.08) числото се умножава по 10.\n")


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="папка на проекта (с converted/)")
    parser.add_argument("--models", default=",".join(CANDIDATES))
    parser.add_argument("--runs", type=int, default=3, help="прогони на модел")
    parser.add_argument("--segments", action="store_true",
                        help="с отсечки от чертежа (както серия 2/3)")
    parser.add_argument("--repair-rounds", type=int, default=0,
                        help="кръгове авто-поправка (както серия 3/4)")
    parser.add_argument("--max-tokens", type=int, default=16000,
                        help="таван на изхода (16000 = продукционният по "
                             "подразбиране; вдигни, за да видиш дали "
                             "отрязаните модели се спасяват)")
    parser.add_argument("--dry", action="store_true",
                        help="само прогноза от записания профил, без заявки")
    parser.add_argument("--prices", action="store_true",
                        help="само живите тарифи на кандидатите")
    parser.add_argument("--refresh-prices", action="store_true")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    prices = openrouter_prices(refresh=args.refresh_prices)

    if args.prices:
        print(f"{'модел':34s} {'$/1M вх':>9s} {'$/1M изх':>9s} {'ctx':>9s} {'таван изх':>10s}")
        for model in models:
            t = price_of(prices, model)
            print(f"{model:34s} {t['input']*1e6:9.3f} {t['output']*1e6:9.3f} "
                  f"{t['ctx']//1000:8d}k {t['out_cap']:10d}")
        return 0

    if args.dry:
        if not PROFILE_PATH.exists():
            raise SystemExit(
                f"няма измерен профил ({PROFILE_PATH}).  Пусни веднъж с "
                f"--project и безплатен модел — токените са същите, цената е 0.")
        dry_projection(models, prices, json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
        return 0

    if not args.project:
        raise SystemExit("--project е задължителен (или ползвай --dry / --prices)")

    from tools.rerun_series import _load_env, _prepare

    _load_env()
    project = Path(os.path.expanduser(args.project))
    if not (project / "converted").exists():
        raise SystemExit(f"{project} няма папка converted/")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Проект: {project}")
    prep = _prepare(project)
    print(f"КСС: {len(prep['boq_index'])} реда с количество")
    print(f"Кандидати: {len(models)} × {args.runs} прогона "
          f"(отсечки: {'да' if args.segments else 'не'}, "
          f"авто-поправки: {args.repair_rounds})\n")

    all_runs: list[dict] = []
    summaries: list[dict] = []

    for model in models:
        tariff = price_of(prices, model)
        print(f"── {model}  (${tariff['input']*1e6:.3f}/${tariff['output']*1e6:.3f} за 1M)")
        runs: list[dict] = []
        for run in range(1, args.runs + 1):
            record = run_once(prep, model, tariff, use_segments=args.segments,
                              repair_rounds=args.repair_rounds, run=run,
                              max_tokens=args.max_tokens)
            runs.append(record)
            mark = "✓" if record.get("clean") else ("~" if record.get("exportable") else "·")
            if record.get("status") == "error":
                # Две различни грешки под един статус: заместен работник (моят
                # запис, има `error`) и генерация, която е стигнала до модела и
                # се е провалила (няма `error` — причината е в parse_errors).
                reason = record.get("error") or (
                    f"генерацията върна status=error "
                    f"(parse_errors={record.get('parse_errors', 0)}, "
                    f"ток={record['tokens_in']}/{record['tokens_out']})")
                print(f"    · прогон {run}: ГРЕШКА {reason[:90]} "
                      f"{record['seconds']:.0f}s")
            else:
                print(f"    {mark} прогон {run}: {record['status']:20s} "
                      f"задачи={record.get('tasks', 0):4d} "
                      f"непокрити={record.get('uncovered', 0):2d} "
                      f"структура={'ок' if record.get('structural_ok') else 'НЕ'} "
                      f"ток={record['tokens_in']}/{record['tokens_out']} "
                      f"${record['cost_worker']:.5f} {record['seconds']:.0f}s")
        all_runs.extend(runs)
        summary = summarise(model, runs, tariff)
        summaries.append(summary)
        print(f"  → чисти {summary['clean']}/{summary['runs']}, "
              f"${summary['cost_per_run']:.5f} на прогон, "
              f"${summary['cost_full_series']:.3f} за пълна серия\n")

    print_table(summaries)

    RESULTS_PATH.write_text(json.dumps(
        {"обобщение": summaries, "прогони": all_runs,
         "отсечки": args.segments, "авто_поправки": args.repair_rounds},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # Профилът се пише от прогоните, дали най-много токени — той е основата на
    # `--dry` за модели, които не искаме да плащаме само за да ги премерим.
    measured = [r for r in all_runs if r["tokens_out"] > 0]
    if measured:
        PROFILE_PATH.write_text(json.dumps({
            "tokens_in": round(statistics.median([r["tokens_in"] for r in measured])),
            "tokens_out": round(statistics.median([r["tokens_out"] for r in measured])),
            "източник": f"{len(measured)} прогона, "
                        f"{', '.join(sorted({r['model'] for r in measured}))}",
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nЗаписано: {RESULTS_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
