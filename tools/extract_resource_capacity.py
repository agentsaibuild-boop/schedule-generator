"""Извлича ВЪРХОВАТА едновременност на всеки ресурс от човешкия MSPDI.

ЗАЩО.  `config/resource_capacity.json` сам казва за числата си: „РАЗУМНО
ПОДРАЗБИРАНЕ за обект с два фронта, НЕ са измерени".  Междувременно точно те
решават срока.  Измерено на детерминистичния прогон (17.08.2026, 410 задачи,
1130 дни):

    Бетоновоз            таван 2 — на таван 685 от 1129 дни
    Багер универсален    таван 2 — на таван 533 дни
    Вибратор за бетон    таван 2 — на таван 369 дни
    Багер ескаватор      таван 2 — на таван 365 дни

Тоест срокът не излиза от мрежата, а от догадката „имаме два багера".  При
това положение „1130 дни" не е резултат от графика — то е преразказ на
настройка, която никой не е мерил.

Продължителностите, бригадите, веригите и застъпванията вече са взети от
еталонния човешки график.  Едновременността се вади от СЪЩОТО място и по
същия начин: за всеки ресурс се брои на колко задачи е назначен едновременно
в реалния човешки график.  Това не е настройка „на око, за да излязат 660
дни" — то е броят, при който човекът е карал обекта.

ВНИМАНИЕ.  Върхът в еталона е ДОЛНА граница за наличната техника, не горна:
човекът може да е имал три багера и да е ползвал два.  Затова изходът е
предложение с числа насреща, а решението кое да влезе в конфигурацията е на
човек — виж `--write`, което пише само с изрично съгласие.

    python tools/extract_resource_capacity.py "<път до .xml>"
    python tools/extract_resource_capacity.py "<път>" --write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NS = "{http://schemas.microsoft.com/project}"
CONFIG = ROOT / "config" / "resource_capacity.json"


def _day(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_reference(path: Path) -> tuple[dict, dict, list]:
    """Стрийм-парсър: файлът е 17 MB и не се зарежда наведнъж.

    Връща (задачи по UID, ресурси по UID, назначения).  Вложените Task/Resource
    елементи се броят по дълбочина — иначе `iterparse` връща и вътрешните.
    """
    tasks: dict[str, dict] = {}
    resources: dict[str, str] = {}
    assignments: list[tuple[str, str]] = []
    depth: dict[str, int] = defaultdict(int)

    for event, elem in ET.iterparse(path, events=("start", "end")):
        tag = elem.tag.replace(NS, "")
        if tag not in ("Task", "Resource", "Assignment"):
            continue
        if event == "start":
            depth[tag] += 1
            continue
        depth[tag] -= 1
        if depth[tag]:
            continue

        if tag == "Task":
            uid = elem.findtext(f"{NS}UID")
            tasks[uid] = {
                "name": (elem.findtext(f"{NS}Name") or "").strip(),
                "start": _day(elem.findtext(f"{NS}Start")),
                "finish": _day(elem.findtext(f"{NS}Finish")),
                "summary": elem.findtext(f"{NS}Summary") == "1",
                "milestone": elem.findtext(f"{NS}Milestone") == "1",
            }
        elif tag == "Resource":
            uid = elem.findtext(f"{NS}UID")
            name = (elem.findtext(f"{NS}Name") or "").strip()
            if name:
                resources[uid] = name
        else:
            assignments.append((elem.findtext(f"{NS}TaskUID"),
                                elem.findtext(f"{NS}ResourceUID")))
        elem.clear()

    return tasks, resources, assignments


def peak_by_resource(tasks: dict, resources: dict,
                     assignments: list) -> dict[str, dict]:
    """За всеки ресурс: връх на едновременно заетите задачи и на колко дни е там.

    Обобщаващите задачи и milestone-ите не се броят — те не заемат техника, а
    обхващат чужда работа; иначе един ресурс, закачен за фаза, би дал връх,
    равен на всичко под нея.
    """
    active: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    task_count: dict[str, int] = defaultdict(int)

    for task_uid, res_uid in assignments:
        task, name = tasks.get(task_uid), resources.get(res_uid)
        if not task or not name or task["summary"] or task["milestone"]:
            continue
        start, finish = task["start"], task["finish"]
        if not start or not finish or finish < start:
            continue
        task_count[name] += 1
        day = start
        while day <= finish:
            if day.weekday() < 5:            # работни дни, както в графика
                active[name][day] += 1
            day += timedelta(days=1)

    report: dict[str, dict] = {}
    for name, by_day in active.items():
        if not by_day:
            continue
        top = max(by_day.values())
        report[name] = {
            "peak": top,
            "days_at_peak": sum(1 for n in by_day.values() if n == top),
            "median": sorted(by_day.values())[len(by_day) // 2],
            "tasks": task_count[name],
            "active_days": len(by_day),
        }
    return report


def _normalise(name: str) -> str:
    """Сравнимо име: еталонът пише „Багер  универсален (2 бр.)"."""
    stripped = re.sub(r"\s*\(.*?\)\s*", " ", name)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xml", help="човешкият MSPDI (еталонът)")
    parser.add_argument("--write", action="store_true",
                        help="запиши предложените тавани в config/resource_capacity.json")
    parser.add_argument("--min-days", type=int, default=1,
                        help="пренебрегни ресурс с по-малко активни дни")
    args = parser.parse_args()

    path = Path(args.xml)
    if not path.exists():
        print(f"няма такъв файл: {path}")
        return 2

    tasks, resources, assignments = load_reference(path)
    print(f"еталон: {len(tasks)} задачи, {len(resources)} ресурса, "
          f"{len(assignments)} назначения")

    measured = {n: d for n, d in
                peak_by_resource(tasks, resources, assignments).items()
                if d["active_days"] >= args.min_days}

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    current = config.get("capacity") or {}
    default = int(config.get("default", 1))
    by_norm = {_normalise(n): n for n in current}

    rows: list[tuple] = []
    for name, data in sorted(measured.items(), key=lambda x: -x[1]["peak"]):
        ours = by_norm.get(_normalise(name))
        cap = current.get(ours, default) if ours else default
        rows.append((name, ours, cap, data))

    print(f"\n{'ресурс в еталона':38s}{'наш таван':>10}{'връх':>6}"
          f"{'мед':>5}{'дни на връх':>12}{'задачи':>8}")
    for name, ours, cap, d in rows:
        mark = "" if ours else "  ← няма при нас"
        print(f"{name[:37]:38s}{cap:>10}{d['peak']:>6}{d['median']:>5}"
              f"{d['days_at_peak']:>12}{d['tasks']:>8}{mark}")

    raised = {ours: d["peak"] for name, ours, cap, d in rows
              if ours and d["peak"] > cap}
    print(f"\nпод измереното: {len(raised)} ресурса")
    for name, peak in sorted(raised.items(), key=lambda x: -x[1]):
        print(f"  {name:35s} {current.get(name, default)} → {peak}")

    if not args.write:
        print("\n(нищо не е записано — виж --write)")
        return 0

    if not raised:
        print("\nняма какво да се промени")
        return 0

    config["capacity"] = {**current, **raised}
    # Името на файла НЕ влиза в бележката: то носи името на обекта, а
    # конфигурацията е в git.  Записва се какво е мерено, не чие е.
    config.setdefault("_note", []).extend([
        "",
        f"ИЗМЕРЕНО {date.today().isoformat()} от еталонния човешки график с "
        f"tools/extract_resource_capacity.py: таванът на {len(raised)} "
        "ресурса беше ПОД върховата едновременност, с която човекът реално е "
        "карал обекта.  Догадката за два багера държеше срока, не мрежата.",
    ])
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print(f"\nзаписано в {CONFIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
