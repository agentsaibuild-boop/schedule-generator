"""Анонимизация на КСС за одиторския пакет — какво се маха и какво НЕ.

Одит 07.08.2026, находка 1: „fixture ≈ ledger × 0,731396.  Твърдението, че от
този fixture мога да възпроизведа ledger-а от нула, не е вярно."

Одиторът е прав, и причината е грешна преценка от наша страна: мащабирахме
количествата „за всеки случай".  Това не пази нищо — количествата в открита
процедура са публични, а самият брийф вече ги цитира — но чупи единственото,
за което fixture-ът съществува: независимата проверка на Σ = КСС.

Затова правилото сега е изрично:

    МАХА СЕ    името на обекта, цените (ед. цена, обща цена)
    ОСТАВА     количествата, мерките, диаметрите, структурата на листовете

Мащабирането беше и по-вредно, отколкото изглежда отвън.  Прилагаше се на
колоната „Дължина /m/", която за редовете на брой носи БРОЯ (174 СВО, 180 СКО,
100 УО).  Тоест fixture-ът даваше 127,26 СВО и 0,73 преливни шахти — от него
28/28 не можеше да излезе дори на теория, а не само да се разминава.

Пуска се ръчно при подготовка на нов одиторски пакет:
    python tools/anonymize_kss.py --check
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "kss_anonymized" / "converted" / "КСС-пример.json"

#: Колоните, в които конвертираният формат държи количеството.
QTY_COLUMNS = ("Дължина  /m/", "Дължина /m/", "количество")

#: Колони с цени — изчистват се безусловно.
PRICE_COLUMNS = ("ед.цена\nЕвро/m'", "Обща цена", "СТОЙНОСТ\nв Евро без ДДС")

#: Къде стоят имената на обектите, които не бива да излизат от къщата.
#:
#: НЕ в кода.  Списъкът с имена е самата данна, която пазим — записан като
#: литерал тук, той пътува в git завинаги и pre-commit скенерът го отхвърля с
#: право.  Затова файлът е ЛОКАЛЕН и извън git (виж .gitignore), а форматът е
#: {"names": ["…"]}.  Алтернатива за CI: променливата CLIENT_NAMES, със
#: запетаи.
#:
#: Празен списък НЕ е тиха победа: `strip_client_name` вдига грешка, защото
#: „нямаше какво да се маха" и „не знаехме какво да махнем" изглеждат
#: еднакво в изхода, а само едното е безопасно.
CLIENT_NAMES_PATH = Path(__file__).resolve().parent.parent / "config" / "client_names.local.json"


def load_client_names() -> tuple[str, ...]:
    """Имената за заличаване — от средата или от локалния файл."""
    from_env = os.getenv("CLIENT_NAMES", "").strip()
    if from_env:
        return tuple(n.strip().lower() for n in from_env.split(",") if n.strip())
    try:
        data = json.loads(CLIENT_NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(str(n).strip().lower() for n in (data.get("names") or [])
                 if str(n).strip())

PLACEHOLDER = "ПРИМЕР"

#: (лист, __excel_row__) -> количеството както е в търга.
#: Възстановено от описа на разпределението, който е правен върху същия търг.
REAL_QUANTITIES: dict[tuple[str, int], float] = {
    ("2. Chast Vodoprovodna", 9): 538.12,
    ("2. Chast Vodoprovodna", 10): 1758.86,
    ("2. Chast Vodoprovodna", 11): 68.6,
    ("2. Chast Vodoprovodna", 12): 881.45,
    ("2. Chast Vodoprovodna", 16): 174,       # СВО, брой
    ("2. Chast Vodoprovodna", 19): 1,         # Водомерна шахта, бр.
    ("3. Chast Kanalizaciya", 9): 1182,
    ("3. Chast Kanalizaciya", 10): 260,
    ("3. Chast Kanalizaciya", 11): 509,
    ("3. Chast Kanalizaciya", 12): 525,
    ("3. Chast Kanalizaciya", 13): 215,
    ("3. Chast Kanalizaciya", 14): 226,
    ("3. Chast Kanalizaciya", 15): 230,
    ("3. Chast Kanalizaciya", 16): 74.5056,   # бетонов кожух DN500, m3/m'
    ("3. Chast Kanalizaciya", 17): 55.428,    # бетонов кожух DN700
    ("3. Chast Kanalizaciya", 18): 677.6,     # бетонов кожух DN1000
    ("3. Chast Kanalizaciya", 19): 620,
    ("3. Chast Kanalizaciya", 20): 308,
    ("3. Chast Kanalizaciya", 24): 180,       # СКО, брой
    ("3. Chast Kanalizaciya", 28): 100,       # УО единичен, брой
    ("3. Chast Kanalizaciya", 29): 60,        # УО двоен, брой
    ("3. Chast Kanalizaciya", 30): 1,         # Преливна шахта, брой
    ("3. Chast Kanalizaciya", 31): 10,        # Индивидуална монолитна РШ, брой
    ("4. Пътна", 8): 10824,
    ("4. Пътна", 9): 7761,
    ("4. Пътна", 10): 18671,
    ("5. ЕЛ и ТТ", 4): 500,
    ("5. ЕЛ и ТТ", 5): 500,
}


def _quantity_column(row: dict) -> str:
    for column in QTY_COLUMNS:
        if column in row:
            return column
    raise KeyError(f"няма колона за количество: {sorted(row)}")


def restore_quantities(data: dict) -> int:
    """Връща количествата такива, каквито са в търга.  Брой закърпени редове."""
    patched = 0
    for sheet in data["sheets"]:
        for row in sheet["rows"]:
            key = (sheet["name"], row.get("__excel_row__"))
            if key not in REAL_QUANTITIES:
                continue
            quantity = REAL_QUANTITIES[key]
            row[_quantity_column(row)] = quantity
            # Броевете стоят дублирани и в колоната за диаметър; ако се
            # разминат, редът изглежда като две различни твърдения.
            for duplicate in ("Диаметър Ф /mm/", "Диаметър Ф /mm/ (2)"):
                if isinstance(row.get(duplicate), (int, float)):
                    row[duplicate] = quantity
            patched += 1
    return patched


def strip_prices(data: dict) -> int:
    """Изчиства ценовите колони.  Броят на занулените клетки."""
    stripped = 0
    for sheet in data["sheets"]:
        for row in sheet["rows"]:
            for column in PRICE_COLUMNS:
                if row.get(column) is not None:
                    row[column] = None
                    stripped += 1
    return stripped


def strip_client_name(raw: str) -> tuple[str, int]:
    """Маха името на обекта отвсякъде — и от хедърите, не само от клетките.

    Регистърът има значение: първата версия на проверката гледаше само
    изписването с главна буква и пропусна срещанията с ГЛАВНИ букви в хедъра
    на един от листовете.  Затова заместването е case-insensitive.
    """
    names = load_client_names()
    if not names:
        raise SystemExit(
            f"няма имена за заличаване: попълни {CLIENT_NAMES_PATH.name} "
            "или задай CLIENT_NAMES.  Празен списък би дал файл, който "
            "ИЗГЛЕЖДА обезличен."
        )
    total = 0
    for name in names:
        raw, count = re.subn(re.escape(name), PLACEHOLDER, raw,
                             flags=re.IGNORECASE)
        total += count
    return raw, total


def anonymize(raw: str) -> tuple[str, dict[str, int]]:
    data = json.loads(raw)
    stats = {
        "quantities": restore_quantities(data),
        "prices": strip_prices(data),
    }
    out = json.dumps(data, ensure_ascii=False, indent=1)
    out, stats["names"] = strip_client_name(out)
    return out + "\n", stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="само проверява дали fixture-ът вече е анонимизиран (без запис)",
    )
    args = parser.parse_args()

    before = FIXTURE.read_text(encoding="utf-8")
    after, stats = anonymize(before)

    if stats["quantities"] != len(REAL_QUANTITIES):
        print(
            f"ГРЕШКА: очаквани {len(REAL_QUANTITIES)} реда с количество, "
            f"намерени {stats['quantities']}"
        )
        return 2

    if args.check:
        if before == after:
            print("fixture-ът е анонимизиран и количествата са автентични")
            return 0
        print("fixture-ът се разминава с правилото — пусни без --check")
        return 1

    FIXTURE.write_text(after, encoding="utf-8")
    print(
        f"количества: {stats['quantities']} реда, "
        f"цени: {stats['prices']} клетки, "
        f"име на обекта: {stats['names']} места"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
