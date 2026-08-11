"""Извлича ТОПОЛОГИЯТА на веригите от човешкия MSPDI — не само реда.

ОДИТ 10.08.2026, P0.2: „tech_chains.json съдържа step names, durations и crews,
но не пази predecessor relation type и lag.  Генерираният design chain става
практически FS0 serial."

Прав е.  Измерено върху еталона:

    23 стъпки, сбор на продължителностите 245 дни
    фазата обаче трае 120 — защото ДЕВЕТ връзки застъпват работата

Тоест имената и продължителностите ги пренесохме, а застъпванията — не.  При
FS0 същите 23 стъпки дават 249 дни; при своята топология — около 120.

ВАЖНО ЗА ЧЕТЕНЕТО НА ТИПОВЕТЕ.  Схемата на MSPDI казва 0=FF, 1=FS, 2=SS, 3=SF.
В този файл обаче тип 3 се държи като SS: наследникът тръгва в деня на
предшественика, а лагът 48000 (десети от минутата) дава точно 10 работни дни
отместване.  Затова връзката се определя ПО ДАТИТЕ, а не по кода — така
извличането не зависи от това чия конвенция е вярна.

    python tools/extract_chain_topology.py "<път до .xml>" [--chain design]
    python tools/extract_chain_topology.py "<път>" --chain design --write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NS = "{http://schemas.microsoft.com/project}"
CONFIG = ROOT / "config" / "tech_chains.json"

#: Заглавието на фазата в човешкия график → ключ на веригата у нас.
PHASE_BY_CHAIN = {"design": "ПРОЕКТИРАНЕ"}

MINUTES_PER_DAY = 480
LAG_TENTHS_PER_DAY = MINUTES_PER_DAY * 10


def _days(duration: str | None) -> float:
    match = re.match(r"PT(?:(\d+)H)?", duration or "")
    return round(int(match.group(1) or 0) / 8, 2) if match else 0.0


def _day(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_tasks(path: Path) -> tuple[dict, list[str]]:
    """Стрийм-парсър: файлът е 17 MB и не се зарежда наведнъж."""
    tasks: dict[str, dict] = {}
    order: list[str] = []
    depth = 0
    for event, elem in ET.iterparse(path, events=("start", "end")):
        if elem.tag.replace(NS, "") != "Task":
            continue
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth:
            continue
        uid = elem.findtext(f"{NS}UID")
        tasks[uid] = {
            "uid": uid,
            "name": (elem.findtext(f"{NS}Name") or "").strip(),
            "level": int(elem.findtext(f"{NS}OutlineLevel") or 0),
            "start": _day(elem.findtext(f"{NS}Start")),
            "finish": _day(elem.findtext(f"{NS}Finish")),
            "days": _days(elem.findtext(f"{NS}Duration")),
            "summary": elem.findtext(f"{NS}Summary") == "1",
            "preds": [
                {"uid": p.findtext(f"{NS}PredecessorUID"),
                 "code": p.findtext(f"{NS}Type"),
                 "lag": int(p.findtext(f"{NS}LinkLag") or 0)}
                for p in elem.findall(f"{NS}PredecessorLink")
            ],
        }
        order.append(uid)
        elem.clear()
    return tasks, order


def phase_tasks(tasks: dict, order: list[str], title: str) -> list[dict]:
    """Листата на фаза от ниво 1, в реда, в който стоят във файла."""
    start = next(i for i, uid in enumerate(order) if tasks[uid]["name"] == title)
    out = []
    for uid in order[start + 1:]:
        task = tasks[uid]
        if task["level"] <= 1:
            break
        if not task["summary"]:
            out.append(task)
    return out


def relation(predecessor: dict, successor: dict) -> tuple[str, int]:
    """Каква е връзката СПОРЕД ДАТИТЕ, и с какъв лаг в дни.

    Две хипотези се проверяват срещу действителните дати:

        SS  наследникът тръгва спрямо НАЧАЛОТО на предшественика
        FS  наследникът тръгва спрямо КРАЯ на предшественика

    Печели тази, чието отместване е по-малко по абсолютна стойност — тоест
    която обяснява графика с по-малко необяснено.  Равенство → FS, защото то е
    по-консервативното твърдение (никакво застъпване).
    """
    if not (predecessor["start"] and predecessor["finish"] and successor["start"]):
        return "FS", 0

    # Милстоун с нулева продължителност седи В ДЕНЯ на предшественика си, а не
    # на следващия.  Без това изключение датите изглеждат като застъпване и
    # веригата от съгласувания излиза SS с лаг −1 — артефакт, не топология.
    if not predecessor["days"] or not successor["days"]:
        return "FS", (successor["start"] - predecessor["finish"]).days

    from_start = (successor["start"] - predecessor["start"]).days
    from_finish = (successor["start"] - predecessor["finish"]).days - 1
    if abs(from_start) < abs(from_finish):
        return "SS", from_start
    return "FS", from_finish


def extract(path: Path, chain_key: str) -> list[dict]:
    tasks, order = load_tasks(path)
    leaves = phase_tasks(tasks, order, PHASE_BY_CHAIN[chain_key])
    by_uid = {t["uid"]: t for t in leaves}

    topology = []
    for position, task in enumerate(leaves):
        inside = [p for p in task["preds"] if p["uid"] in by_uid]
        entry = {"position": position, "name": task["name"], "days": task["days"],
                 "predecessor": None, "type": None, "lag_days": 0,
                 "declared_code": None}
        if inside:
            link = inside[0]
            predecessor = by_uid[link["uid"]]
            kind, lag = relation(predecessor, task)
            entry.update(
                predecessor=leaves.index(predecessor),
                type=kind, lag_days=lag,
                declared_code=link["code"],
                declared_lag_days=round(link["lag"] / LAG_TENTHS_PER_DAY, 2))
        topology.append(entry)
    return topology


def serial_span(topology: list[dict]) -> float:
    return sum(step["days"] for step in topology)


def topological_span(topology: list[dict]) -> float:
    """Кога свършва всяка стъпка, ако се спазва извлечената топология."""
    finish: dict[int, float] = {}
    start: dict[int, float] = {}
    for step in topology:
        pos = step["position"]
        if step["predecessor"] is None:
            begin = 0.0
        elif step["type"] == "SS":
            begin = start[step["predecessor"]] + step["lag_days"]
        else:
            begin = finish[step["predecessor"]] + step["lag_days"]
        start[pos] = max(0.0, begin)
        finish[pos] = start[pos] + step["days"]
    return max(finish.values()) if finish else 0.0


def write_into_config(chain_key: str, topology: list[dict]) -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    steps = config["chains"][chain_key]["steps"]
    if len(steps) != len(topology):
        raise SystemExit(
            f"{chain_key}: {len(steps)} стъпки в конфигурацията срещу "
            f"{len(topology)} в еталона — не ги свързвам напосоки")

    written = 0
    for step, extracted in zip(steps, topology):
        if extracted["predecessor"] is None:
            step.pop("predecessor", None)
            step.pop("relation", None)
            step.pop("lag_days", None)
            continue
        step["predecessor"] = steps[extracted["predecessor"]]["key"]
        step["relation"] = extracted["type"]
        step["lag_days"] = extracted["lag_days"]
        written += 1

    config["chains"][chain_key]["_topology_note"] = (
        "Връзките са ИЗВЛЕЧЕНИ от човешкия график по датите, не по кода на "
        "типа (одит 10.08.2026, P0.2).  Без тях 23-те стъпки дават 245 дни "
        "последователно; със застъпванията — около 120, както е в еталона."
    )
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mspdi", help="човешкият график (.xml, не .mpp)")
    parser.add_argument("--chain", default="design", choices=sorted(PHASE_BY_CHAIN))
    parser.add_argument("--write", action="store_true",
                        help="запиши връзките в config/tech_chains.json")
    args = parser.parse_args()

    topology = extract(Path(args.mspdi), args.chain)

    print(f"{'#':>3} {'дни':>6} {'връзка':>10} {'лаг':>5} {'код':>4}  стъпка")
    for step in topology:
        link = "—" if step["predecessor"] is None else \
            f"{step['type']}←{step['predecessor']}"
        print(f"{step['position']:>3} {step['days']:>6.1f} {link:>10} "
              f"{step['lag_days']:>5} {str(step['declared_code'] or ''):>4}  "
              f"{step['name'][:44]}")

    overlaps = sum(1 for s in topology if s["type"] == "SS")
    print(f"\nзастъпващи връзки (SS): {overlaps}")
    print(f"последователно:      {serial_span(topology):.0f} дни")
    print(f"по извлечената топология: {topological_span(topology):.0f} дни")

    if args.write:
        written = write_into_config(args.chain, topology)
        print(f"\nзаписани {written} връзки в {CONFIG.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
