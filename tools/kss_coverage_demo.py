# -*- coding: utf-8 -*-
"""Независимо възпроизводима демонстрация на детекция+норми (одит 2026-08, т.7).

Пуска детерминистичния калкулатор върху СИНТЕТИЧЕН КСС (генерични имена, реалната
българска нотация: Ф-диаметри, PP материал, „брой", кв.м) и печата разпределение
по код + брой доказани.  Реалният КСС е клиентски и не е в репото; този fixture
възпроизвежда СЪЩИТЕ капани, така че одиторът да провери числата от пакета.

    python tools/kss_coverage_demo.py

За before/after: `git checkout ecb63f3 -- src/duration_calculator.py config/productivities.json`
(старата версия) → пусни пак → преди фиксовете доказаните са 0 (всички Ф-диаметри
падат в MISSING_DN).  После `git checkout HEAD -- ...` за да върнеш.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.duration_calculator import calculate_task_duration  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "synthetic_kss_rows.json"


def run(fixture: Path = FIXTURE) -> dict:
    rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]
    codes: Counter = Counter()
    proven = 0
    mismatches = []
    for r in rows:
        task = {k: v for k, v in r.items() if not k.startswith("_")}
        res = calculate_task_duration(task)
        codes[res.code] += 1
        if res.days is not None:
            proven += 1
        if r.get("_expect") and r["_expect"] != res.code:
            mismatches.append((r["name"][:40], r["_expect"], res.code))
    return {"total": len(rows), "proven": proven, "codes": dict(codes),
            "mismatches": mismatches}


def main() -> int:
    out = run()
    print(f"Синтетичен КСС: {out['total']} дейности")
    print(f"Доказани (детерминистично): {out['proven']}/{out['total']}")
    for code, n in sorted(out["codes"].items()):
        print(f"  {code:20} × {n}")
    if out["mismatches"]:
        print("\nРАЗМИНАВАНИЯ с очакваното:")
        for name, exp, got in out["mismatches"]:
            print(f"  {name}: очаквано {exp}, получено {got}")
        return 1
    print("\nВсички дейности отговарят на очаквания код.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
