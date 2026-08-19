"""Колко ДУШИ влизат в една задача и колко има на обекта — от еталонен график.

ЗАЩО.  `config/resource_capacity.json` брои ЕДНОВРЕМЕННИ ЗАДАЧИ.  Това е
грешна мерна единица навсякъде, където бригадата е повече от един човек: в
еталонния график каналджия влиза по 3 на задача, строителен работник по 3, общ
работник по 2.  Обектът има четиринайсет каналджии, а таванът допускаше шест
ЗАДАЧИ — при това всяка уж с един каналджия.

MSPDI записва `Units` на всяко назначение, тоест истинският състав е в
документа и не се налага да се предполага.  Тук се вадят двете числа:

    `на_задача` — колко единици взима ЕДНА задача (средното в еталона);
    `налични`   — върховият едновременен СБОР от единици на обекта.

Пуска се:
    python tools/extract_headcount.py <еталон.xml> [--write]

Без `--write` само показва; с него дописва блока `headcount` в
config/resource_capacity.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
КОНФИГ = ROOT / "config" / "resource_capacity.json"
NS = "{http://schemas.microsoft.com/project}"


def _дата(текст: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(текст)[:19])
    except (TypeError, ValueError):
        return None


def извлечи(път: Path) -> dict[str, dict[str, int]]:
    """Състав и наличност по ресурс.

    Стриймва с `iterparse`: еталонните MSPDI файлове са десетки мегабайти и
    директното четене изяжда паметта.
    """
    имена: dict[str, str] = {}
    задачи: dict[str, tuple[datetime, datetime]] = {}
    назначения: list[tuple[str, str, str]] = []

    for _, елем in ET.iterparse(str(път), events=("end",)):
        if елем.tag == NS + "Resource":
            имена[елем.findtext(NS + "UID")] = (
                елем.findtext(NS + "Name") or "").strip()
            елем.clear()
        elif елем.tag == NS + "Task":
            начало = _дата(елем.findtext(NS + "Start"))
            край = _дата(елем.findtext(NS + "Finish"))
            # Обобщаващите редове НЕ заемат ресурс — техните назначения са
            # сборът на децата и биха удвоили всяко число тук.
            if начало and край and елем.findtext(NS + "Summary") != "1":
                задачи[елем.findtext(NS + "UID")] = (начало, край)
            елем.clear()
        elif елем.tag == NS + "Assignment":
            назначения.append((елем.findtext(NS + "TaskUID"),
                               елем.findtext(NS + "ResourceUID"),
                               елем.findtext(NS + "Units")))
            елем.clear()

    if not задачи:
        raise SystemExit(f"{път} няма задачи с дати — не е MSPDI?")

    нула = min(начало for начало, _ in задачи.values())
    по_ден: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    единици: dict[str, list[float]] = defaultdict(list)

    for tid, rid, units in назначения:
        интервал = задачи.get(tid)
        if not интервал:
            continue
        try:
            брой = float(units or 0) or 1.0
        except ValueError:
            брой = 1.0
        име = имена.get(rid, rid)
        единици[име].append(брой)
        начало, край = интервал
        for ден in range((начало - нула).days, (край - нула).days + 1):
            по_ден[име][ден] += брой

    изход: dict[str, dict[str, int]] = {}
    for име, стойности in единици.items():
        налични = max(по_ден[име].values()) if по_ден[име] else 0
        if налични <= 0:
            continue
        изход[име] = {
            "на_задача": max(1, round(sum(стойности) / len(стойности) + 1e-9)),
            "налични": int(round(налични)),
            "_назначения": len(стойности),
        }
    return изход


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schedule", help="еталонен MSPDI (.xml)")
    parser.add_argument("--write", action="store_true",
                        help="допиши блока `headcount` в конфигурацията")
    args = parser.parse_args()

    състав = извлечи(Path(args.schedule))
    print(f"ресурси с назначения: {len(състав)}\n")
    print(f"{'ресурс':34s} {'на задача':>10s} {'налични':>8s} {'назначения':>11s}")
    for име, d in sorted(състав.items(), key=lambda kv: -kv[1]["налични"]):
        print(f"{име:34s} {d['на_задача']:10d} {d['налични']:8d} "
              f"{d['_назначения']:11d}")

    if not args.write:
        print("\n(само показване — с --write се записва в конфигурацията)")
        return 0

    конфиг = json.loads(КОНФИГ.read_text(encoding="utf-8"))
    конфиг["headcount"] = dict(sorted(състав.items()))
    КОНФИГ.write_text(json.dumps(конфиг, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    print(f"\nзаписано в {КОНФИГ.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
