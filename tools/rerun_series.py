"""Четирите серии по 10 генерации — възпроизводимо, не ad hoc.

ОДИТ 07.08.2026: „Суровите 40 runs са от предишната архитектура.  Новият XML
вече има contract phases, mandatory chains, resource leveling, final milestone
и corrected roll-up.  Следователно твърдението „39/39 produced schedules are
structurally valid" не може автоматично да се пренесе върху сегашната версия.
Трябва нов 4×10 rerun."

Прав е, и по-важното: миналия път сериите бяха пуснати с еднократен скрипт,
който не остана никъде.  Затова числата в брийфа не можеха да бъдат повторени
дори от нас.  Този файл е самият експеримент.

Какво мени всяка серия (както в оригиналната таблица):

    1  пакети                                    без отсечки, без авто-поправки
    2  пакети + отсечки от ситуационния чертеж    без авто-поправки
    3  пакети + отсечки + авто-поправки
    4  пакети + авто-поправки                     БЕЗ отсечки  (контрола)

Анализът на документите и отсечките от чертежа се вадят ВЕДНЪЖ и се ползват от
всички серии.  Иначе всеки прогон би мерил и стабилността на четенето на
чертежа, а разликата между сериите вече не би значела само това, което сменяме.

Пуска се:
    python tools/rerun_series.py --project "<път до папката на проекта>"
    python tools/rerun_series.py --project ... --series 4 --runs 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("rerun")

OUT_DIR = ROOT / "docs" / "прогони"

#: (номер, етикет, отсечки, кръгове авто-поправка)
SERIES: tuple[tuple[int, str, bool, int], ...] = (
    (1, "само-пакети", False, 0),
    (2, "с-участъци", True, 0),
    (3, "с-участъци-и-поправки", True, 2),
    (4, "контрола-без-участъци", False, 2),
)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv липсва — разчитам на средата")
        return
    load_dotenv(ROOT / ".env")


def _prepare(project: Path) -> dict[str, Any]:
    """Анализ, индекс на КСС и отсечки — веднъж за всичките серии."""
    from src.ai_processor import AIProcessor
    from src.ai_router import AIRouter
    from src.file_manager import FileManager
    from src.provenance import build_quantity_index

    router = AIRouter()
    ai = AIProcessor(router=router)
    files = FileManager(base_path=str(project))

    boq_index = build_quantity_index(project)
    if not boq_index:
        raise SystemExit(f"няма индексируем КСС в {project}")

    cache = OUT_DIR / "_подготовка.json"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        print(f"  подготовката е от кеша ({cache.name})")
        return {"ai": ai, "boq_index": boq_index, **saved}

    print("  анализ на документите...")
    analysis = ai.analyze_documents(files.get_converted_files(), files.get_all_text())

    print("  четене на ситуационните чертежи...")
    classification = files.classify_files(ai_processor=ai)
    locations: list[str] = []
    segments: list[dict] = []
    for path in classification.get("situation_paths", []):
        try:
            locations.extend(ai.extract_situation_locations(path))
            segments.extend(ai.extract_situation_segments(path))
        except Exception as exc:                        # noqa: BLE001
            logger.warning("Ситуация %s: %s", path, exc)

    saved = {"analysis": analysis, "locations": locations, "segments": segments}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {len(locations)} места, {len(segments)} отсечки от чертежа")
    return {"ai": ai, "boq_index": boq_index, **saved}


def _metrics(run: int, result: dict, elapsed: float,
             boq_index: list | None = None) -> dict:
    """Показателите от предишния пакет ПЛЮС структурните флагове.

    ОДИТ 10.08.2026, P1.3: „rerun telemetry не съдържа пълните structural
    acceptance flags за текущата архитектура.  Освен това всички 10 runs в
    серия 4 имат поне един parse_error, включително exportable runs."

    Затова „чист" вече не значи `exportable`, а `exportable` И всички твърди
    структурни флагове.  По-строгата мярка ще свали процента — това не е
    влошаване, а спиране на самозаблудата: досега не се проверяваха нито
    надзорът, нито roll-up-ът, нито проследимостта до КСС.
    """
    from src.schedule_diagnostics import (
        duration_report, is_clean, is_clean_but_for_the_input, structural_flags)
    from src.work_package import load_chains

    conservation = result.get("conservation") or {}
    citation = result.get("citation_report") or {}
    validation = result.get("validation") or {}
    tasks = (result.get("schedule") or {}).get("tasks") or []
    duration = (result.get("duration_report") or {}).get("summary") or {}

    flags = structural_flags(
        tasks, packages=result.get("packages") or [], chains=load_chains(),
        boq_index=boq_index or [], conservation=conservation,
        parse_errors=result.get("parse_errors") or [])
    timing = duration_report(tasks)

    structural_ok = is_clean(flags)

    return {
        **flags,
        "clean": bool(result.get("exportable")) and structural_ok,
        "structural_ok": structural_ok,
        # Втори показател, НЕ заместител: без него 40 прогона върху търг с
        # един противоречив ред дават 40 еднакви провала и стабилността на
        # генерацията остава неизмерена.
        "clean_but_for_input": (bool(result.get("exportable"))
                                and is_clean_but_for_the_input(flags)),
        "total_days": timing["total_days"],
        "critical_path_days": timing["critical_path_days"],
        "resource_delay_days": timing["resource_delay_days"],
        "phase_days": {k: v["days"] for k, v in timing["phases"].items()},
        "fallback_used": not bool(result.get("packaged")),
        "repair_rounds_used": int(os.environ.get("PACKAGE_REPAIR_ROUNDS", "2")),
        "run": run,
        "status": result.get("status"),
        "exportable": bool(result.get("exportable")),
        "packages": len(result.get("packages") or []),
        "tasks": len(tasks),
        "critical": result.get("critical_count", 0),
        "cons_ok": bool(conservation.get("ok")),
        "over": len(conservation.get("over") or []),
        "short": len(conservation.get("short") or []),
        "missing": len(conservation.get("missing") or []),
        "uncovered": len(citation.get("uncovered") or []),
        "over_covered": len(citation.get("over_covered") or []),
        "recomputed": duration.get("recomputed", 0),
        "valid": bool(validation.get("valid")),
        "val_errors": len(validation.get("errors") or []),
        "parse_errors": len(result.get("parse_errors") or []),
        "cost": result.get("total_cost", 0.0),
        "seconds": round(elapsed, 1),
        "leveling_shifted": (result.get("leveling") or {}).get("shifted", 0),
        "uncovered_refs": list(citation.get("uncovered") or []),
        "missing_refs": list(conservation.get("missing") or []),
        "unplaced": list(result.get("unplaced") or []),
        # КОЛКО, не само КОЙ (проба 10.08.2026).  `check_conservation` връща
        # {ref: {required, planned, packages}}, а тук стоеше `list(...)` —
        # тоест само КЛЮЧОВЕТЕ.  Резултатът: 40 прогона, ред
        # „2. Chast Vodoprovodna!12" превишен в 15 от тях, и нито едно число, с
        # което да се различи РАЗПРЕДЕЛИТЕЛЕН ДРЕЙФ (свива се пропорционално)
        # от ДУБЛИРАНА РАБОТА (блокира).  Разликата решава дали дефектът е наш
        # или на модела, а телеметрията я изхвърляше.
        "over_refs": _amounts(conservation.get("over")),
        "short_refs": _amounts(conservation.get("short")),
        # `ok` пада и при непознат цитат, а той не се записваше НИКЪДЕ — такъв
        # прогон изглеждаше провален без нито една причина.
        "unknown_refs": list(conservation.get("unknown_ref") or []),
    }


def _amounts(entries: Any) -> list[dict]:
    """{ref: {required, planned, packages}} → списък с числата, запазени."""
    if not isinstance(entries, dict):
        return list(entries or [])
    out = []
    for ref, data in entries.items():
        if not isinstance(data, dict):
            out.append({"ref": ref})
            continue
        want = data.get("required")
        got = data.get("planned")
        row = {"ref": ref, "required": want, "planned": got,
               "packages": list(data.get("packages") or [])}
        if isinstance(want, (int, float)) and want:
            row["excess_pct"] = round((got / want - 1) * 100, 1)
        out.append(row)
    return out


def _run_series(prep: dict, number: int, label: str,
                use_segments: bool, repair_rounds: int, runs: int) -> list[dict]:
    os.environ["PACKAGE_REPAIR_ROUNDS"] = str(repair_rounds)
    segments = prep["segments"] if use_segments else None

    records: list[dict] = []
    for run in range(1, runs + 1):
        started = time.monotonic()
        # ПОВТОРЕН ОПИТ САМО ПРИ ТЕХНИЧЕСКИ ПРОВАЛ (безплатните модели се
        # ограничават от споделен пул и връщат 429).  Това НЕ е повтаряне на
        # лош резултат — прогон, който е дал график, се записва какъвто е.
        # Иначе едно прекъсване на доставчика минава за качество на генерацията.
        result, failure = None, None
        for attempt in range(1, 4):
            try:
                result = prep["ai"].generate_schedule_packaged(
                    prep["analysis"], prep["boq_index"],
                    num_teams=2, locations=prep["locations"], segments=segments)
                break
            except Exception as exc:                    # noqa: BLE001
                failure = exc
                logger.warning("серия %d прогон %d, опит %d: %s",
                               number, run, attempt, exc)
                time.sleep(20 * attempt)

        if result is None:
            records.append({"run": run, "status": "error", "exportable": False,
                            "error": str(failure), "cost": 0.0,
                            "seconds": round(time.monotonic() - started, 1)})
            continue

        record = _metrics(run, result, time.monotonic() - started,
                          prep["boq_index"])
        records.append(record)
        mark = "✓" if record["clean"] else ("~" if record["exportable"] else "·")
        print(f"    {mark} прогон {run:2d}: {record['status']:20s} "
              f"пакети={record['packages']:3d} задачи={record['tasks']:4d} "
              f"крит={record['critical']:3d} превиш={record['over']} "
              f"непокрити={record['uncovered']} "
              f"структура={'ок' if record['structural_ok'] else 'НЕ'} "
              f"${record['cost']:.4f} {record['seconds']:.0f}s")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="папка на проекта (с converted/)")
    parser.add_argument("--series", type=int, action="append",
                        help="само тази серия (може многократно); по подразбиране всички")
    parser.add_argument("--runs", type=int, default=10, help="прогони на серия")
    args = parser.parse_args()

    _load_env()
    project = Path(args.project)
    if not (project / "converted").exists():
        raise SystemExit(f"{project} няма папка converted/")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Проект: {project}")
    prep = _prepare(project)
    print(f"КСС: {len(prep['boq_index'])} реда с количество\n")

    wanted = set(args.series or [s[0] for s in SERIES])
    summary: list[dict] = []
    grand_total = 0.0

    for number, label, use_segments, repair_rounds in SERIES:
        if number not in wanted:
            continue
        print(f"Серия {number} — {label} "
              f"(отсечки: {'да' if use_segments else 'не'}, "
              f"авто-поправки: {repair_rounds})")
        records = _run_series(prep, number, label, use_segments,
                              repair_rounds, args.runs)

        clean = sum(1 for r in records if r.get("clean"))
        but_for_input = sum(1 for r in records if r.get("clean_but_for_input"))
        exportable = sum(1 for r in records if r.get("exportable"))
        over = sum(1 for r in records if r.get("over"))
        cost = sum(r.get("cost") or 0.0 for r in records)
        grand_total += cost

        out = OUT_DIR / f"серия-{number}-{label}.json"
        out.write_text(json.dumps(records, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        summary.append({"серия": number, "етикет": label, "чисти": clean,
                        "чисти_без_входния_конфликт": but_for_input,
                        "експортируеми": exportable,
                        "превишени": over, "цена": round(cost, 4),
                        "файл": out.name})
        print(f"  → чисти {clean}/{len(records)}, без входния конфликт "
              f"{but_for_input}/{len(records)}, експортируеми {exportable}; "
              f"превишени количества в {over} прогона, ${cost:.4f}")

    print("=" * 72)
    print(f"{'Серия':6s} {'Етикет':26s} {'Чисти':>7s} {'Превишени':>10s} {'Цена':>10s}")
    for row in summary:
        print(f"{row['серия']:<6d} {row['етикет']:26s} "
              f"{row['чисти']:>4d}/{args.runs:<2d} {row['превишени']:>10d} "
              f"${row['цена']:>9.4f}")
    print(f"{'ОБЩО':33s} {'':>7s} {'':>10s} ${grand_total:>9.4f}")

    (OUT_DIR / "обобщение.json").write_text(
        json.dumps({"серии": summary, "общо_цена": round(grand_total, 4),
                    "прогони_на_серия": args.runs},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
