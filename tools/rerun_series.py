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

# Конзолата на Windows е cp1252 по подразбиране, а целият изход е на кирилица —
# без това серията пада на първия print, преди да е пуснала един прогон.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


def _цена_по_токени(usage: dict) -> float:
    """Цена от изгорените токени, по тарифата на работника.

    Ползва се, когато прогонът не си каже цената — тоест точно при провал,
    който също е платен.  Тарифата идва от `ai_router.PRICING`, за да не се
    появи трето място, което държи цени.
    """
    from src.ai_router import MODEL_WORKER, PRICING

    тарифа = PRICING.get(MODEL_WORKER) or {}
    вх = float(тарифа.get("input") or 0.0)
    изх = float(тарифа.get("output") or 0.0)
    return round(int(usage.get("tokens_in") or 0) * вх
                 + int(usage.get("tokens_out") or 0) * изх, 6)


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
        concurrency_report, duration_report, is_clean,
        is_clean_but_for_the_input, structural_flags)
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
        # Едновременност: броят задачи вече е близък до еталона, срокът — не.
        # Разликата е в паралелизацията, затова тя се мери на всеки прогон.
        **{f"concurrency_{k}": v
           for k, v in concurrency_report(tasks).items() if k != "evaluated"},
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
        # ТЕКСТЪТ, не само броят (17.08.2026).  Контролната серия показа два
        # прогона с ФАТАЛНИ бележки при парсването — изхвърлена работа — и
        # въпросът „каква точно" остана без отговор, защото записът пазеше
        # число.  Фаталните са малко и си заслужават мястото; останалите се
        # режат на 40, за да не подуят файла.
        "fatal_parse_errors": [
            str(e) for e in (result.get("parse_errors") or [])
            if "пропуснат" in str(e) or "не е ред от КСС" in str(e)],
        "parse_notes": [str(e)[:200]
                        for e in (result.get("parse_errors") or [])][:40],
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


def _diagnosis_only(message: str) -> str:
    """Диагнозата от предупреждението, БЕЗ суровия отговор на модела.

    Предупреждението за невалиден JSON носи и парчето от отговора, а в него
    влизат имена на обекта и улиците.  Телеметрията отива в git — тоест
    прихващачът щеше да изнесе клиентски данни, ако pre-commit проверката не
    беше го хванала (13.08.2026).  Оставяме само първия ред до началото на
    полезния товар.
    """
    return message.split("\n", 1)[0].split("{", 1)[0].strip()[:200]


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
                use_segments: bool, repair_rounds: int, runs: int,
                манифест: dict | None = None) -> list[dict]:
    os.environ["PACKAGE_REPAIR_ROUNDS"] = str(repair_rounds)
    segments = prep["segments"] if use_segments else None

    router = prep["ai"].router

    # ДОСТИГНА ЛИ ПРОГОНЪТ РАБОТНИКА.  Проба 13.08.2026: интернетът прекъсна по
    # средата на измерване и шест прогона се записаха като провал на модела —
    # с нула токени и по 5 секунди.  Без токените в телеметрията мрежово
    # прекъсване е неразличимо от провалена генерация, а точно това смесване
    # одиторът вече ни посочи веднъж.  Затова всеки запис носи и токените.
    captured: list[str] = []

    class _RouterWarnings(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                captured.append(_diagnosis_only(record.getMessage()))

    sniffer = _RouterWarnings()
    logging.getLogger("src.ai_router").addHandler(sniffer)

    records: list[dict] = []
    for run in range(1, runs + 1):
        started = time.monotonic()
        captured.clear()
        usage_mark = len(router.usage_log)
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

        calls = list(router.usage_log[usage_mark:])
        изход = sum(int(c.get("tokens_out") or 0) for c in calls)
        предупреждения = captured[-3:]
        # ЧЕТИРИ РАЗЛИЧНИ НЕЩА, не едно.  ОДИТ 13.08.2026: „reached_worker=true
        # е записано във всичките 40 runs, включително петте с tokens_out=7 —
        # името не значи получен реален worker response."  Вярно е: флагът
        # означаваше „имаше платена заявка".  Заявка, отговор, разбираем
        # отговор и цял отговор са четири отделни събития и се провалят по
        # различни причини — доставчик, модел, формат, таван.
        usage = {
            "tokens_in": sum(int(c.get("tokens_in") or 0) for c in calls),
            "tokens_out": изход,
            "calls": len(calls),
            "request_reached_provider": bool(calls),
            "nonempty_worker_response": изход >= 100,
            "response_parseable": not any(
                "парсване" in w or "невалиден JSON" in w for w in предупреждения),
            "output_truncated": any("ОТРЯЗАН" in w for w in предупреждения),
            "router_warnings": предупреждения,
        }

        if result is None:
            records.append({"run": run, "status": "error", "exportable": False,
                            "error": str(failure),
                            "cost": _цена_по_токени(usage), **usage,
                            "seconds": round(time.monotonic() - started, 1)})
            continue

        record = _metrics(run, result, time.monotonic() - started,
                          prep["boq_index"])
        record["manifest_id"] = (манифест or {}).get("manifest_id", "")
        record["git_commit"] = (манифест or {}).get("git_commit", "")
        record.update(usage)
        # ПРОВАЛЕНИЯТ ПРОГОН СЪЩО СЕ Е ПЛАТИЛ.  Прогон, който не е върнал
        # график, връща и резултат без цена — затова 8 от 40 записани прогона
        # казваха $0.0000, макар да бяха изгорели токени (отрязан отговор на
        # 32 000, празен отговор от 7).  Сборът излизаше $0.4595 при
        # действителни $0.6333 по токени, тоест занижен с 37 %.
        if not record.get("cost"):
            record["cost"] = _цена_по_токени(usage)
        records.append(record)
        mark = "✓" if record["clean"] else ("~" if record["exportable"] else "·")
        print(f"    {mark} прогон {run:2d}: {record['status']:20s} "
              f"пакети={record['packages']:3d} задачи={record['tasks']:4d} "
              f"крит={record['critical']:3d} превиш={record['over']} "
              f"непокрити={record['uncovered']} "
              f"структура={'ок' if record['structural_ok'] else 'НЕ'} "
              f"ток={record['tokens_in']}/{record['tokens_out']} "
              f"${record['cost']:.4f} {record['seconds']:.0f}s")
    logging.getLogger("src.ai_router").removeHandler(sniffer)
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

    # ОТПЕЧАТЪК НА ВЕРСИЯТА (одит 18.08.2026, P0.1).  Без него не може машинно
    # да се каже кой прогон е от коя версия — и точно затова пакетът два пъти
    # смеси документи от една версия с прогони от друга.
    from src.audit_manifest import write_manifest
    манифест = write_manifest(OUT_DIR)
    print(f"Версия: {манифест['manifest_id']} "
          f"(commit {манифест['git_commit']}"
          + (", РАБОТНОТО ДЪРВО Е МРЪСНО" if манифест["git_dirty"] else "")
          + ")")
    print(f"Проект: {project}")
    prep = _prepare(project)
    print(f"КСС: {len(prep['boq_index'])} реда с количество\n")

    wanted = set(args.series or [s[0] for s in SERIES])
    частично = bool(args.series) or args.runs != 10
    summary: list[dict] = []
    grand_total = 0.0

    for number, label, use_segments, repair_rounds in SERIES:
        if number not in wanted:
            continue
        print(f"Серия {number} — {label} "
              f"(отсечки: {'да' if use_segments else 'не'}, "
              f"авто-поправки: {repair_rounds})")
        records = _run_series(prep, number, label, use_segments,
                              repair_rounds, args.runs, манифест)

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

    # ОБОБЩЕНИЕТО СЕ ГРАДИ ОТ СУРОВИТЕ ФАЙЛОВЕ, НЕ ОТ ТОВА ПУСКАНЕ.
    #
    # НЕЗАВИСИМ ОДИТ 17.08.2026: „пакетът не съдържа 40 завършени прогона —
    # серия 3 има 8; обобщение.json съдържа само серия 3."  Прав е, и причината
    # е точно тук: частично пускане (`--series 3 --runs 8`) презаписваше файла
    # на пълната серия И пренаписваше обобщението само със себе си.  Тоест
    # контролен прогон мълчаливо унищожаваше записа на целия набор, а
    # обобщението продължаваше да се представя за него.
    #
    # Сега частичното пускане пише в СВОЙ файл, а обобщението се сглобява от
    # всички серии, налични на диска.
    if частично and summary:
        свое = OUT_DIR / ("контролна-серия-"
                          + "-".join(str(n) for n in sorted(wanted)) + ".json")
        for row in summary:
            източник = OUT_DIR / row["файл"]
            if източник.exists():
                източник.replace(свое)
                row["файл"] = свое.name
        print("")
        print("ЧАСТИЧНО ПУСКАНЕ — записано в " + свое.name
              + "; пълният набор остава непокътнат.")

    от_диска: list[dict] = []
    общо_прогони = 0
    for файл in sorted(OUT_DIR.glob("серия-[1-4]-*.json")):
        записи = json.loads(файл.read_text(encoding="utf-8"))
        общо_прогони += len(записи)
        от_диска.append({
            "серия": int(файл.name.split("-")[1]),
            "етикет": файл.stem.split("-", 2)[-1],
            "прогони": len(записи),
            "чисти": sum(1 for r in записи if r.get("clean")),
            "експортируеми": sum(1 for r in записи if r.get("exportable")),
            "превишени": sum(1 for r in записи if r.get("over")),
            "грешки": sum(1 for r in записи if r.get("status") == "error"),
            "цена": round(sum(r.get("cost") or 0.0 for r in записи), 4),
            "файл": файл.name,
        })

    (OUT_DIR / "обобщение.json").write_text(
        json.dumps({"серии": от_диска,
                    "прогони_общо": общо_прогони,
                    "чисти_общо": sum(s["чисти"] for s in от_диска),
                    "общо_цена": round(sum(s["цена"] for s in от_диска), 4),
                    "_източник": "сглобено от суровите файлове на диска, "
                                 "не от последното пускане"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("Обобщение от диска: " + str(общо_прогони) + " прогона, "
          + str(sum(s["чисти"] for s in от_диска)) + " чисти.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
