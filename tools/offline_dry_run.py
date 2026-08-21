"""Целият път надолу, БЕЗ модел — с разпределение, което е вярно по строеж.

ЗАЩО.  Всичките 40 живи прогона (10.08.2026) паднаха на ЕДНО нещо: сборът на
разпределените количества не е равен на КСС.  Разпределението е преценка на
модела.  Тоест за всичко ОСТАНАЛО — вериги, WBS, кръстосани връзки,
продължителности от нормите, CPM, ресурсно изравняване, надзор, roll-up,
XML — нямаме нито едно измерване, при което входът да е бил редовен.

Този инструмент прави точно това: сглобява разпределение, което покрива
истинския КСС ТОЧНО (по строеж, не по късмет), подава го на мястото на модела
и пуска целия останал път.  Отговаря на въпроса, който досега стоеше отворен:

    „Ако моделът раздели количествата правилно, получава ли се чист,
     пълен, експортируем график?"

Всичко е детерминистично и не струва нито един токен, затова всяка следваща
поправка може да се провери преди да се харчат кредити.

ВНИМАНИЕ ЗА ЧЕТЕНЕТО НА РЕЗУЛТАТА.  Тук НЕ се мери качеството на генерацията —
входът е нагласен.  Провал тук значи дефект в кода надолу по веригата; успех
тук НЕ значи, че живият прогон ще мине.

Пуска се:
    python tools/offline_dry_run.py
    python tools/offline_dry_run.py --project "<път>" --packages 4 --xml out.xml
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")

#: Листът в КСС → мрежа.  Речникът е нарочно тесен: непознат лист пада в
#: канализацията само ако класът му го позволява, иначе се вижда в отчета.
_SHEET_NETWORK = (
    ("vodoprovod", "В"),
    ("водопровод", "В"),
    ("kanaliz", "К"),
    ("канализац", "К"),
    ("пътни", "П"),
    ("patni", "П"),
    ("ел и тт", "ЕЛ"),
)

#: Клас на реда → веригата, която ГАРАНТИРАНО го покрива, независимо от листа.
#: Настилките и кабелите живеят в собствени пакети и в еталона.
_CLASS_CHAIN = {"pavement": "pavement_section", "cable": "cable_section"}

_NETWORK_CHAIN = {"В": "water_section", "К": "sewer_section",
                  "П": "pavement_section", "ЕЛ": "cable_section"}

_CHAIN_NETWORK = {"water_section": "В", "sewer_section": "К",
                  "pavement_section": "П", "cable_section": "ЕЛ"}


def _sheet_of(ref: str) -> str:
    parts = str(ref).split("!")
    return parts[1] if len(parts) >= 3 else ""


def _network_of(ref: str) -> str:
    sheet = _sheet_of(ref).lower()
    for needle, network in _SHEET_NETWORK:
        if needle in sheet:
            return network
    return ""


def _split_exactly(total: float, parts: int) -> list[float]:
    """Раздели на `parts` дяла, чийто сбор е ТОЧНО `total`.

    Последният дял поема остатъка от закръглянето.  Ако това не се направи,
    самият харнес внася дрейфа, който уж проверява.
    """
    if parts <= 1:
        return [total]
    share = round(total / parts, 6)
    head = [share] * (parts - 1)
    return head + [round(total - share * (parts - 1), 6)]


def build_perfect_allocation(boq_index: list[Any], per_chain: int) -> dict:
    """Отговорът, който моделът ТРЯБВАШЕ да върне — сглобен от самия КСС."""
    from src.provenance import _coverer_class

    buckets: dict[str, list[tuple[Any, str]]] = {}
    unroutable: list[str] = []

    for row in boq_index:
        quantity = getattr(row, "quantity", None)
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
            continue                      # заглавия и „ОБЩО" — нямат количество
        activity = _coverer_class(row)
        if not activity:
            unroutable.append(str(row.ref))
            continue
        chain = _CLASS_CHAIN.get(activity) or _NETWORK_CHAIN.get(
            _network_of(row.ref), "")
        if not chain:
            unroutable.append(str(row.ref))
            continue
        buckets.setdefault(chain, []).append((row, activity))

    packages: list[dict] = []
    for chain, rows in sorted(buckets.items()):
        network = _CHAIN_NETWORK[chain]
        count = max(1, per_chain)
        made: list[dict] = [
            {
                "id": f"{network}{i + 1}",
                "name": f"кл. {i + 1} от {network}Ш {i + 1} до {network}Ш {i + 2}",
                "network": network,
                "chain": chain,
                "branch": f"кл. {i + 1}",
                "items": [],
            }
            for i in range(count)
        ]
        for row, _activity in rows:
            for pkg, share in zip(made, _split_exactly(float(row.quantity), count)):
                if share <= 0:
                    continue
                pkg["items"].append(
                    {"source_ref": str(row.ref), "quantity": share})
        packages.extend(p for p in made if p["items"])

    return {"packages": packages, "_unroutable": unroutable}


class _ScriptedRouter:
    """Работник, който връща подготвения отговор — и КРЕЩИ при втори въпрос.

    Пакетният път може да поиска допълнение за неразпределените позиции.  Ако
    го направи тук, значи разпределението НЕ е било пълно — а то е сглобено от
    самия КСС.  Тогава проблемът е в кода, не в модела, и не бива да се замаже
    с втори подготвен отговор.
    """

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0
        self.deepseek_available = True
        self.anthropic_available = False

    def chat(self, messages, system_prompt, **kwargs) -> dict:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError(
                f"пакетният път зададе {self.calls}-и въпрос към модела, "
                "въпреки че разпределението покрива КСС точно")
        return {
            "content": json.dumps(self._payload, ensure_ascii=False),
            "model": "offline-dry-run",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost": 0.0,
            "fallback": False,
            "truncated": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=str(Path.home() / "Desktop" / "2026"))
    parser.add_argument("--packages", type=int, default=4,
                        help="участъци на верига")
    parser.add_argument("--teams", type=int, default=2)
    parser.add_argument("--xml", default="", help="запиши MSPDI тук")
    parser.add_argument("--situation", default="",
                        help="ситуационен чертеж: участъците идват от него, "
                             "вместо да се делят на равни етапи")
    parser.add_argument("--water", default="",
                        help="водопроводна ситуация (етикети PEHD)")
    parser.add_argument("--water-area", default="",
                        help="подобект на водопровода: И / Р / ПЗ")
    parser.add_argument("--bundle", default="",
                        help="папка за ЦЕЛИЯ комплект от една версия: график, "
                             "опис, диагностика, сравнение на календарите и "
                             "audit_manifest.json")
    parser.add_argument("--reference", default="",
                        help="еталонен MSPDI за сравнението на календарите")
    # Договорният обхват НЕ идва от КСС: проектирането и авторският надзор се
    # създават само когато поръчката е инженеринг.  Затова текстът на анализа
    # мени `contract_scope_complete` — при неутрален текст графикът законно
    # няма фаза „проектиране" и флагът пада.
    parser.add_argument("--analysis",
                        default="инженеринг — проектиране и строителство",
                        help="текст на анализа (реши договорния обхват)")
    args = parser.parse_args()

    from src.ai_processor import AIProcessor
    from src.provenance import build_quantity_index
    from src.schedule_diagnostics import (duration_report, is_clean,
                                          structural_flags)
    from src.work_package import load_chains

    project = Path(args.project)
    boq_index = build_quantity_index(project)
    if not boq_index:
        print(f"няма индексируем КСС в {project}")
        return 2

    allocation = build_perfect_allocation(boq_index, args.packages)
    unroutable = allocation.pop("_unroutable")
    print(f"КСС: {len(boq_index)} реда")
    print(f"разпределение: {len(allocation['packages'])} участъка, "
          f"{sum(len(p['items']) for p in allocation['packages'])} количества")
    if unroutable:
        print(f"БЕЗ ВЕРИГА: {len(unroutable)} реда — {unroutable[:5]}")

    router = _ScriptedRouter(allocation)
    ai = AIProcessor(router=router)

    situation_segments = []
    if args.situation:
        from src.situation_reader import read_sewer_situation, summarize
        отсечки = read_sewer_situation(args.situation)
        situation_segments = [dict(о._asdict()) for о in отсечки]
        обобщение = summarize(отсечки)
        print(f"ситуация: {обобщение['segments']} участъка в обхват, "
              f"{обобщение['dropped']} отпаднали, "
              f"{обобщение['total_m']} м")

    if args.water:
        from src.situation_reader import read_water_situation, summarize
        водни = read_water_situation(args.water, area=args.water_area)
        situation_segments += [dict(о._asdict()) for о in водни if о.in_scope]
        w = summarize(водни)
        print(f"водопровод: {w['segments']} участъка в обхват, "
              f"{w['dropped']} отпаднали, {w['total_m']} м")

    result = ai.generate_schedule_packaged(
        {"analysis": args.analysis}, boq_index, num_teams=args.teams,
        segments=situation_segments, project_path=project)

    status = result.get("status")
    # Външният слой връща графика в `schedule.tasks`; вътрешният — в `tasks`.
    tasks = ((result.get("schedule") or {}).get("tasks")
             or result.get("tasks") or [])
    conservation = result.get("conservation") or {}
    print(f"\nстатус: {status}   задачи: {len(tasks)}   "
          f"пакети: {len(result.get('packages') or [])}")

    if not tasks:
        print("СПРЯ ПРЕДИ ДА ИЗЛЕЗЕ ГРАФИК:", result.get("message"))
        return 1

    flags = structural_flags(tasks, packages=result.get("packages") or [],
                             chains=load_chains(), boq_index=boq_index,
                             conservation=conservation,
                             parse_errors=result.get("parse_errors") or [])
    print("\n--- структурни флагове ---")
    for key, value in flags.items():
        if isinstance(value, bool):
            print(f"  {'ок ' if value else 'НЕ '} {key}")
    print(f"  проследимост до КСС: {flags['source_ref_resolvable_pct']}%")
    print(f"\nЧИСТ: {is_clean(flags)}")

    timing = duration_report(tasks)
    print(f"срок: {timing['total_days']} дни, критичен път "
          f"{timing['critical_path_days']} дни")

    summary = (result.get("duration_report") or {}).get("summary") or {}
    print(f"продължителности: {summary.get('recomputed', 0)} по норма, "
          f"{summary.get('unresolved', 0)} недоказани {summary.get('by_code', {})}")

    for blocker in result.get("blockers") or []:
        print(f"  БЛОКИРА: {blocker}")

    if args.xml:
        from src.export_xml import export_to_mspdi_xml
        target = Path(args.xml)
        payload = export_to_mspdi_xml(tasks, "Офлайн проба",
                                      filename=str(target))
        if not payload:
            print("\nXML: експортът не върна нищо")
            return 1
        print(f"\nXML: {target} ({len(payload)} байта)")

    if args.bundle:
        # ЦЕЛИЯТ КОМПЛЕКТ ОТ ЕДНА ВЕРСИЯ (одит 18.08.2026, P0.4).
        #
        # „От ZIP-а самостоятелно не може да се изпълни: input fixture ->
        # current code -> 642-day XML."  Прав е — досега този инструмент
        # печаташе числа на екрана и по желание един XML, а всичко останало се
        # сглобяваше отделно и по различно време.  Оттам и смесеният пакет.
        #
        # Тук излизат ЗАЕДНО: графикът, описът, диагностиката и сравнението на
        # календарите, всеки с един и същ `manifest_id`.
        from src.audit_manifest import write_manifest
        from src.export_xml import export_to_mspdi_xml
        from src.schedule_diagnostics import concurrency_report, duration_report
        from src.work_package import format_allocation_ledger

        папка = Path(args.bundle)
        манифест = write_manifest(папка, artifact="offline_dry_run")
        ид = манифест["manifest_id"]
        print(f"\nКомплект: {папка}  (версия {ид})")

        xml_път = папка / "график.xml"
        payload = export_to_mspdi_xml(tasks, "Детерминистичен прогон",
                                      filename=str(xml_път))
        if not payload:
            print("  XML: експортът не върна нищо")
            return 1

        (папка / "опис-на-разпределението.md").write_text(
            format_allocation_ledger(result.get("ledger") or [],
                                     result.get("resolutions") or []),
            encoding="utf-8")

        диагностика = {
            "manifest_id": ид,
            "структурни_флагове": {k: v for k, v in flags.items()
                                   if isinstance(v, (bool, int, float))},
            "чист": is_clean(flags),
            "срок": duration_report(tasks),
            "едновременност": concurrency_report(tasks),
            "задачи": len(tasks),
            "пакети": len(result.get("packages") or []),
        }
        (папка / "диагностика.json").write_text(
            json.dumps(диагностика, ensure_ascii=False, indent=1), encoding="utf-8")

        # Сравнението с еталона е част от комплекта, а не отделно упражнение —
        # точно защото сгрешеното сравнение веднъж вече мина за резултат.
        сравнение: dict = {"manifest_id": ид, "нашият": None, "еталон": None}
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            from compare_calendars import обобщи

            сравнение["нашият"] = обобщи(xml_път)
            ако = Path(args.reference) if args.reference else None
            if ако and ако.exists():
                сравнение["еталон"] = обобщи(ако)
        except Exception as exc:                          # noqa: BLE001
            сравнение["грешка"] = str(exc)[:200]
        (папка / "сравнение-на-календарите.json").write_text(
            json.dumps(сравнение, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")

        print(f"  график.xml ({len(payload)} байта)")
        for име in ("опис-на-разпределението.md", "диагностика.json",
                    "сравнение-на-календарите.json", "audit_manifest.json"):
            print(f"  {име}")

    return 0 if is_clean(flags) else 1


if __name__ == "__main__":
    raise SystemExit(main())
