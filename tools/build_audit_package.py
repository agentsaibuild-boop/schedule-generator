"""Сглобява пакета за одитора върху десктопа — включително живия график.

Миналият пакет беше сглобен на ръка.  Следствието се видя веднага: описът и
анонимизираният КСС се разминаваха, защото идваха от два различни момента, и
одиторът не можеше да провери Σ = КСС.  Тук всичко излиза от ЕДИН прогон и
от текущото състояние на репото.

Пуска се:
    uv run --with markdown python tools/build_audit_package.py \\
        --project "<папка на проекта>" [--out "<папка>"] [--no-generate]

`--no-generate` пропуска живата генерация и преизползва графика и описа от
предишния пакет — за когато се сменя само текст.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("package")

DESKTOP = Path.home() / "Desktop"
RUNS_DIR = ROOT / "docs" / "прогони"
BRIEF = ROOT / "docs" / "BRIEF_ZA_ODITORA_2026-08-07.md"

HTML_HEAD = """<!doctype html>
<html lang="bg"><head><meta charset="utf-8">
<title>{title}</title>
<style>
 body {{ font-family: Georgia, 'Times New Roman', serif; max-width: 46em;
        margin: 3em auto; padding: 0 1.5em; line-height: 1.65; color: #1a1a1a; }}
 h1 {{ font-size: 1.7em; border-bottom: 2px solid #333; padding-bottom: .3em; }}
 h2 {{ font-size: 1.3em; margin-top: 2em; border-bottom: 1px solid #ccc;
       padding-bottom: .2em; }}
 h3 {{ font-size: 1.1em; margin-top: 1.5em; }}
 table {{ border-collapse: collapse; width: 100%; margin: 1.2em 0;
          font-family: system-ui, sans-serif; font-size: .92em; }}
 th, td {{ border: 1px solid #bbb; padding: .45em .7em; text-align: left; }}
 th {{ background: #f0f0f0; }}
 code {{ background: #f4f4f4; padding: .1em .35em; border-radius: 3px;
         font-size: .9em; }}
 pre {{ background: #f6f6f6; padding: 1em; overflow-x: auto;
        border-left: 3px solid #999; }}
 blockquote {{ border-left: 3px solid #c0392b; margin-left: 0; padding-left: 1em;
               color: #444; background: #fdf6f5; }}
 hr {{ border: none; border-top: 1px solid #ddd; margin: 2.5em 0; }}
 strong {{ color: #000; }}
 @media print {{ body {{ margin: 0; max-width: none; }} }}
</style></head><body>
"""


def to_html(markdown_text: str, title: str) -> str:
    import markdown as md

    body = md.markdown(markdown_text, extensions=["tables", "fenced_code"])
    return HTML_HEAD.format(title=title) + body + "\n</body></html>\n"


def write_pair(folder: Path, name: str, text: str, title: str) -> None:
    """Всеки документ е и .md, и .html — със същото съдържание."""
    (folder / f"{name}.md").write_text(text, encoding="utf-8")
    (folder / f"{name}.html").write_text(to_html(text, title), encoding="utf-8")


# ---------------------------------------------------------------------------
# Живият график и описът — от ЕДИН прогон
# ---------------------------------------------------------------------------


def generate_clean_schedule(project: Path, attempts: int = 4) -> dict:
    """Генерирай, докато системата сама обяви графика за готов.

    Работната конфигурация (контролата от изпитанията): без отсечки от
    чертежа, с допитване за неразпределени.  Тя дава 9 чисти от 10.
    """
    from src.ai_processor import AIProcessor
    from src.ai_router import AIRouter
    from src.provenance import build_quantity_index

    prep = json.loads((RUNS_DIR / "_подготовка.json").read_text(encoding="utf-8"))
    ai = AIProcessor(router=AIRouter())
    boq_index = build_quantity_index(project)

    for attempt in range(1, attempts + 1):
        print(f"  генерация, опит {attempt}...")
        result = ai.generate_schedule_packaged(
            prep["analysis"], boq_index, num_teams=2,
            locations=prep["locations"], segments=None)
        if result.get("exportable"):
            print(f"  чист график: {len(result['schedule']['tasks'])} задачи")
            return result
        print(f"    не е чист ({result.get('status')}) — пробвам пак")

    raise SystemExit(f"{attempts} опита без чист график — пусни пак")


def build_schedule_files(folder: Path, result: dict) -> dict:
    from src.export_xml import export_to_mspdi_xml
    from src.work_package import format_allocation_ledger

    tasks = result["schedule"]["tasks"]
    xml = export_to_mspdi_xml(tasks, "Реконструкция ВиК мрежа — кв. Пример",
                              "2026-09-01")
    (folder / "4. Генериран график (MS Project).xml").write_bytes(xml)

    # `format_allocation_ledger` носи собствено заглавие — не му слагаме второ.
    ledger = result.get("ledger") or []
    text = format_allocation_ledger(ledger).replace(
        "Сборът може да бъде пресметнат независимо от този документ.",
        "Сборът може да бъде пресметнат независимо от този документ — срещу\n"
        "анонимизирания КСС в `технически/`, чиито количества са автентичните.")
    write_pair(folder, "5. Опис на разпределението", text,
               "Опис на разпределението")

    exact = sum(1 for e in ledger if e.get("status") == "ок")
    return {"tasks": len(tasks), "packages": len(result.get("packages") or []),
            "critical": result.get("critical_count", 0),
            "ledger_ok": exact, "ledger_total": len(ledger)}


# ---------------------------------------------------------------------------
# Резултатите от изпитанията — от суровите прогони
# ---------------------------------------------------------------------------


SERIES_LABELS = {
    1: ("Серия 1 — първо питане, без участъци", "не", "не"),
    2: ("Серия 2 — първо питане, с участъци", "ДА", "не"),
    3: ("Серия 3 — с участъци, пълна конфигурация", "ДА", "ДА"),
    4: ("Серия 4 — контрола, без участъци", "не", "ДА"),
}


def build_results_doc(folder: Path) -> dict:
    rows, totals = [], {"cost": 0.0, "minutes": 0.0}
    for number, (label, segs, repair) in SERIES_LABELS.items():
        path = next(RUNS_DIR.glob(f"серия-{number}-*.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        clean = sum(1 for r in data if r.get("exportable"))
        valid = sum(1 for r in data if r.get("valid"))
        over = sum(1 for r in data if r.get("over"))
        unc = sum(1 for r in data if r.get("uncovered"))
        totals["cost"] += sum(r.get("cost") or 0 for r in data)
        totals["minutes"] += sum(r.get("seconds") or 0 for r in data) / 60
        rows.append(f"| {label} | {segs} | {repair} | **{clean}/{len(data)}** | "
                    f"{valid}/{len(data)} | {over} | {unc} |")

    text = f"""# Резултати от изпитанията

Всяка серия е 10 генерации на един и същ обект. Сменя се по едно нещо.

| Серия | Участъци от чертежа | Допитване за неразпределени | Чисти | Валидна структура | Превишени количества | Непокрити редове |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

Общата цена на четирите серии е {totals['cost']:.2f} USD,
времето — {totals['minutes']:.0f} минути.

---

## Кое е съпоставимо с предишния пакет

**Само серия 4.** Тя е единствената, която мери същата конфигурация като
преди: пълна работна настройка без участъци от чертежа. Беше 5/10, сега е
**9/10**.

Серии 1 и 2 мерят друго — в тях е изключен цикълът на повторно питане за
неразпределените позиции. Не ги представяме като повторение на предишните.

Като страничен резултат обаче те показват нещо, което не бяхме мервали:
**от първото питане моделът не покрива нищо** — 0 от 10, с 10 до 19 непокрити
реда. Цялото покритие идва от повторното питане, което сваля непокритите от
25 на 1.

## Как да четете „чист“

Чист прогон означава: всяко количество от КСС е разпределено точно веднъж,
всяка позиция е покрита от дейност от правилния клас, мрежата е валидна и
файлът е готов за MS Project — без човешка намеса.

Прогон, който не е чист, **не значи грешен график**. Значи, че програмата
сама е отказала да го обяви за готов и е посочила кои редове са проблемни.

## Основният извод

Участъците от чертежа продължават да влошават разпределянето — но причината
не е тази, която посочихме миналия път.

Блокерът е **един ред, в 10 от 10 прогона**: „Реконструкция на Главни
водопроводни клонове". Той е единствената позиция, която физически минава
през няколко участъка. Останалите 27 реда се разпределят коректно.

Тоест въпросът не е „моделът вижда твърде много места", а „как се дели
позиция, обхващаща целия обект". Виж раздел 10.6 от брийфа.
"""
    write_pair(folder, "3. Резултати от изпитанията", text,
               "Резултати от изпитанията")
    return totals


# ---------------------------------------------------------------------------
# Сглобяване
# ---------------------------------------------------------------------------


# Името на обекта е ДАННА НА КЛИЕНТА, не константа в кода: пакетът се сглобява
# на десктопа, а репото не бива да го носи (pre-commit го отхвърля с право).
# Подава се с --object; по подразбиране пакетът излиза без име на обект.
DEFAULT_OBJECT = "(обектът не е посочен — подай --object)"

README = """ПАКЕТ ЗА ОДИТОРА
Обект: {object}
Дата: {date} (втора редакция)

ОТКЪДЕ ДА ЗАПОЧНЕТЕ
-------------------
Отворете „1. Брийф".  Новото е в РАЗДЕЛ 10 — той отговаря на петте Ви
приоритета по реда, в който ги дадохте.

Два от подразделите оттеглят наши собствени твърдения: 10.6 (защо участъците
от чертежа вредят — предишното обяснение беше грешно) и 10.8 (под-
сегментацията НЕ е следствие от технически таван, както предположихме).

КАКВО СЕ ПРОМЕНИ СПРЯМО ПЪРВИЯ ПАКЕТ
------------------------------------
1. Анонимизираният КСС вече носи АВТЕНТИЧНИТЕ количества.  Бяхте прав, че от
   предишния описът не може да се възпроизведе — беше мащабиран.  По-лошо:
   коефициентът падаше и върху редовете на брой, тоест файлът твърдеше 127,26
   СВО.  Сега Σ = КСС може да се пресметне независимо.

2. Настилките се пакетират по ЗОНА, не по ред от КСС.  Дублираният обхват,
   който описахте, го няма.

3. Възстановяването чака подземните работи по СВОЯТА улица, не глобално.

4. Всяка задача носи цитат към реда в КСС (поле „Източник") и
   пространствената си идентичност (участък, улица, възли, пикетаж).

5. Прогоните са пуснати наново върху сегашната архитектура.

КАКВО ИМА В ПАПКАТА
-------------------
1. Брийф
   Пълната история, включително предишните раздели както си бяха.
   Новото е раздел 10.

2. Технологични вериги
   Непроменени спрямо първия пакет.

3. Резултати от изпитанията
   Новите четири серии по 10 генерации.

4. Генериран график (MS Project).xml
   Реалният изход от текущата версия — от прогон, който системата сама обяви
   за готов.  Отваря се направо в MS Project.

5. Опис на разпределението
   Ред по ред: колко се иска, колко е разпределено, къде отиде.  Този опис и
   анонимизираният КСС в „технически/" вече идват от един и същ момент.

технически/
   Анонимизираният КСС, конфигурацията, суровите прогони и скриптовете —
   включително този, с който сериите се пускат наново, и този, с който КСС се
   анонимизира.  Миналия път скриптът за прогоните беше еднократен и не
   остана никъде; затова числата не можеха да бъдат повторени дори от нас.

ФОРМАТИ
-------
Всеки документ е и в .html, и в .md.  Съдържанието е идентично.
"""

TECH_README = """# Технически материали

Целта е да можете да проверите твърденията сами.

## `анонимизиран-КСС/`

28 реда, същата структура като реалния търг, **автентични количества**, без
име на обект и без цени.  Срещу описа на разпределението Σ = КСС се смята
независимо.

Правилото е приложено от `anonymize_kss.py`: маха се името на обекта и
цените, остават количествата, мерките, диаметрите и структурата.  Миналия
път количествата бяха мащабирани — това не пазеше нищо (сумите така или
иначе са в брийфа), а правеше проверката невъзможна.

## `rerun_series.py` — пуснете изпитанията сами

Четирите серии по 10 генерации.  Изисква ключове за модела.

    python rerun_series.py --project "<папка>" --runs 10

Всяка серия сменя по едно нещо; какво точно — виж горе във файла.

## `tech_chains.json`

Технологичните вериги в редактируем вид.  Непроменени спрямо първия пакет.
`observed_count` показва колко пъти веригата се среща във Вашия график.
Където е 0, веригата НЕ е извлечена от Вас — тези са за преглед на първо
място.

## `extract_chains.py`, `extract.py`

Скриптовете, с които извадихме веригите от Вашия график и с които мерим
показателите на двата файла.  Стрийм-парсер — не зареждат 17 MB в паметта.

    python extract_chains.py "вашия-график.xml" chains.json
    python extract.py "график.xml" изходна-папка етикет

## `прогони/`

Суровите записи от всичките 40 нови генерации.  Полетата са същите като
преди, плюс `seconds` и `leveling_shifted`.  Обобщението в документ 3 се
получава от тези файлове.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="папка на проекта (за живата генерация)")
    parser.add_argument("--out", default=str(DESKTOP), help="къде да излезе пакетът")
    parser.add_argument("--date", default="10.08.2026")
    parser.add_argument("--object", default=DEFAULT_OBJECT,
                        help="име на обекта за заглавието и архива")
    parser.add_argument("--no-generate", action="store_true",
                        help="преизползвай графика и описа от предишния пакет")
    args = parser.parse_args()

    if not args.no_generate:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")

    out_root = Path(args.out)
    folder = out_root / "за одитора"
    previous = folder.exists()
    if previous:
        backup = out_root / "за одитора (първи пакет 07.08)"
        if not backup.exists():
            print(f"Запазвам предишния пакет като „{backup.name}“")
            shutil.copytree(folder, backup)
        shutil.rmtree(folder)

    tech = folder / "технически"
    tech.mkdir(parents=True, exist_ok=True)

    print("Документи...")
    write_pair(folder, "1. Брийф", BRIEF.read_text(encoding="utf-8"), "Брийф")
    totals = build_results_doc(folder)

    # Веригите са непроменени — носим ги както си бяха.
    source_chains = out_root / "за одитора (първи пакет 07.08)"
    for name in ("2. Технологични вериги.md", "2. Технологични вериги.html"):
        src = source_chains / name
        if src.exists():
            shutil.copy2(src, folder / name)

    print("Технически материали...")
    shutil.copy2(ROOT / "config" / "tech_chains.json", tech)
    shutil.copy2(ROOT / "config" / "resource_capacity.json", tech)
    shutil.copy2(ROOT / "tools" / "rerun_series.py", tech)
    shutil.copy2(ROOT / "tools" / "anonymize_kss.py", tech)
    for name in ("extract.py", "extract_chains.py"):
        src = source_chains / "технически" / name
        if src.exists():
            shutil.copy2(src, tech)

    fixture_dst = tech / "анонимизиран-КСС" / "converted"
    fixture_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "tests" / "fixtures" / "kss_anonymized" / "converted" / "КСС-пример.json",
        fixture_dst)

    runs_dst = tech / "прогони"
    runs_dst.mkdir(parents=True, exist_ok=True)
    for run_file in sorted(RUNS_DIR.glob("серия-*.json")):
        shutil.copy2(run_file, runs_dst)
    shutil.copy2(RUNS_DIR / "обобщение.json", runs_dst)
    (tech / "ПРОЧЕТИ МЕ.md").write_text(TECH_README, encoding="utf-8")

    if args.no_generate:
        for name in ("4. Генериран график (MS Project).xml",
                     "5. Опис на разпределението.md",
                     "5. Опис на разпределението.html"):
            src = source_chains / name
            if src.exists():
                shutil.copy2(src, folder / name)
        stats = {}
    else:
        if not args.project:
            raise SystemExit("--project е задължителен без --no-generate")
        print("Жива генерация за графика и описа...")
        result = generate_clean_schedule(Path(args.project))
        stats = build_schedule_files(folder, result)

    (folder / "ПРОЧЕТИ МЕ.txt").write_text(
        README.format(date=args.date, object=args.object), encoding="utf-8")

    stamp = args.date.replace(".", "-")
    archive = out_root / (f"За одитора — {args.object} {stamp}.zip"
                          if args.object != DEFAULT_OBJECT
                          else f"За одитора {stamp}.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_root))

    print(f"\nПакет: {folder}")
    print(f"Архив: {archive.name} ({archive.stat().st_size / 1024:.0f} KB)")
    if stats:
        print(f"График: {stats['tasks']} задачи, {stats['packages']} пакета, "
              f"критичен път {stats['critical']}")
        print(f"Опис: {stats['ledger_ok']} от {stats['ledger_total']} реда точно")
    print(f"Изпитания: ${totals['cost']:.2f}, {totals['minutes']:.0f} мин")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
