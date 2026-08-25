"""Чете .mpp през самия MS Project — за сравнение с чужд график.

ЗАЩО.  `.mpp` е двоичен формат на Microsoft; ние пишем и четем MSPDI XML,
защото той е отворен.  Но когато изпълнителят даде готов график, направен от
друга програма, единственият честен начин да го сравним е да го отворим с
програмата, която го е писала.

Работи САМО на машина с инсталиран MS Project (`MSProject.Application`).  Ако
го няма, казва го и спира — вместо да гадае по байтовете.

    python tools/read_mpp.py <файл.mpp> --tasks tasks.json
    python tools/read_mpp.py <файл.mpp> --segments segments.json

`--segments` вади КЛОНОВЕТЕ: редовете от вида

    кл.83/кл.75: ОТ86 – ОТ104, ул. „Сливница", L=588 m, DN90

стават отсечки (`situation_reader.Segment`), с които нашият конвейер може да
произведе същата подредба — по клонове, а не „Етап N от 8".  Мрежата се чете
от екипа, под който стои клонът.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: „кл.83/кл.75: ОТ86 – ОТ104, ул. „Сливница", L=588 m, DN90"
_КЛОН = re.compile(
    r"^(?P<branch>.+?):\s*(?P<start>.+?)\s+[–—-]\s+(?P<end>.+?),\s*"
    r"ул\.\s*[„\"](?P<street>[^“”\"]+)[“”\"],\s*"
    r"L\s*=\s*(?P<length>[\d.,]+)\s*m,\s*DN\s*(?P<dn>\d+)",
    re.IGNORECASE)

#: Име на екипа → мрежа.  Другите нива нямат мрежа и не раждат отсечки.
_МРЕЖА_НА_ЕКИПА = (("водопроводен", "В"), ("канализационен", "К"))


def прочети(път: Path) -> list[dict]:
    """Всички задачи от файла, с ниво, продължителност, дати и ресурси."""
    try:
        import win32com.client
    except ImportError as exc:      # pragma: no cover — зависи от машината
        raise SystemExit(
            "Липсва pywin32 — четенето на .mpp минава през самия MS Project.\n"
            "  python -m pip install pywin32") from exc

    app = win32com.client.Dispatch("MSProject.Application")
    app.Visible = False
    app.DisplayAlerts = False
    try:
        app.FileOpen(str(път), ReadOnly=True)
        проект = app.ActiveProject
        редове = []
        for задача in проект.Tasks:
            if задача is None:      # изтрит ред — MS Project ги връща като None
                continue
            редове.append({
                "id": задача.ID,
                "level": задача.OutlineLevel,
                "name": задача.Name,
                "duration_min": задача.Duration,
                "start": str(задача.Start),
                "finish": str(задача.Finish),
                "predecessors": str(задача.Predecessors or ""),
                "resources": str(задача.ResourceNames or ""),
                "summary": bool(задача.Summary),
            })
        return редове
    finally:
        app.FileCloseAll(0)
        app.Quit()


def отсечки(редове: list[dict]) -> list[dict]:
    """Клоновете от чуждия график, като отсечки за нашия конвейер.

    Мрежата НЕ се гадае от името на клона: чете се от екипа, под който той
    стои („Водопроводен екип 3" → „В").  Ред, който не се разчита — например
    „Чакълозадържател – 1 бр. в зоната на КПС" — се пропуска и се брои.
    """
    мрежа = ""
    готови: list[dict] = []
    пропуснати: list[str] = []
    for ред in редове:
        име = str(ред.get("name") or "").strip()
        малко = име.lower()
        за_екипа = next((м for дума, м in _МРЕЖА_НА_ЕКИПА if дума in малко), "")
        if за_екипа:
            мрежа = за_екипа
            continue
        if not мрежа:
            continue
        съвпадение = _КЛОН.match(име)
        if not съвпадение:
            if ред.get("summary"):
                пропуснати.append(име)
            continue
        готови.append({
            "network": мрежа,
            "branch": съвпадение.group("branch").strip(),
            "start_node": съвпадение.group("start").strip(),
            "end_node": съвпадение.group("end").strip(),
            "length_m": float(съвпадение.group("length").replace(",", ".")),
            "dn": int(съвпадение.group("dn")),
            "street": съвпадение.group("street").strip(),
            "source": "MS Project файл на изпълнителя",
            # СТРУКТУРИРАН източник: клонът, възлите, улицата, дължината и
            # диаметърът идват от полета на готов график, не от четене на
            # чертеж с око.  Затова възлите могат да излязат в нашия график —
            # виж `spatial_source.STRUCTURED_SEGMENTS`.
            "spatial_source": "structured_segments",
            "in_scope": True,
            "scope_reason": "клон от предоставения график",
        })
    if пропуснати:
        print(f"неразчетени обобщаващи редове: {len(пропуснати)}")
        for име in пропуснати[:5]:
            print(f"  · {име}")
    return готови


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mpp")
    parser.add_argument("--tasks", default="", help="запиши всички задачи тук")
    parser.add_argument("--segments", default="", help="запиши клоновете тук")
    args = parser.parse_args()

    редове = прочети(Path(args.mpp))
    print(f"{len(редове)} задачи в {Path(args.mpp).name}")

    if args.tasks:
        Path(args.tasks).write_text(
            json.dumps(редове, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"задачи → {args.tasks}")

    if args.segments:
        сегменти = отсечки(редове)
        по_мрежа: dict[str, float] = {}
        for с in сегменти:
            по_мрежа[с["network"]] = по_мрежа.get(с["network"], 0.0) + с["length_m"]
        Path(args.segments).write_text(
            json.dumps(сегменти, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(сегменти)} клона → {args.segments}")
        for мрежа, метри in sorted(по_мрежа.items()):
            брой = sum(1 for с in сегменти if с["network"] == мрежа)
            print(f"  {мрежа}: {брой} клона, {метри:.0f} м")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
