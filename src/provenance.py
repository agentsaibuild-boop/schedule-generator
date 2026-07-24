"""Произход на стойностите — откъде идва всяко число (BACKLOG т.3).

ЗАЩО: количество 420 м влизаше в графика без никаква връзка към документа,
листа и реда, от които е взето.  На въпроса „откъде е това число" нямаше
отговор — нито за човек, нито за одитор.  По-лошо: нямаше разлика между

    измерено от документ | предположено от AI | изчислено от код | въведено от човек

а тези четири имат съвсем различна тежест при спор с възложител.

Този модул е ПЪРВИЯТ ЕТАП: индексира количествените редове от конвертираните
документи и позволява стойност в графика да бъде сверена срещу тях.

Съзнателно НЕ прави: не пренаписва извличането и не гарантира произход за
всяка стойност.  Каквото не може да се свери, се маркира като несверено —
това е по-полезно от фалшива увереност.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Състояния на стойност — подредени по тежест на доказателството.
STATUS_EXTRACTED = "extracted"        # сверено срещу ред в документ
STATUS_AI_REPORTED = "ai_reported"    # AI го е казал, несверено
STATUS_CALCULATED = "calculated"      # изведено от код по норма
STATUS_ASSUMED = "assumed"            # предположение
STATUS_HUMAN = "human_override"       # въведено от човек

# Колко трябва да съвпадат две количества, за да се приемат за едно и също.
_QUANTITY_TOLERANCE = 0.02            # 2%

# Минимално сходство на описанието, за да се приеме съответствие.
_MIN_NAME_SIMILARITY = 0.35

_WORD_RE = re.compile(r"[\wА-Яа-я]+", re.UNICODE)
_STOPWORDS = frozenset({
    "на", "за", "от", "до", "и", "или", "по", "със", "с", "в", "при",
    "доставка", "монтаж",
})


class SourceRef(NamedTuple):
    """Точно място в документ."""

    document: str
    sheet: str = ""
    row: int | None = None
    column: str = ""

    def describe(self) -> str:
        parts = [self.document]
        if self.sheet:
            parts.append(f"лист '{self.sheet}'")
        if self.row is not None:
            parts.append(f"ред {self.row}")
        if self.column:
            parts.append(f"колона {self.column}")
        return ", ".join(parts)


class QuantityRow(NamedTuple):
    """Индексиран ред от количествена сметка."""

    description: str
    quantity: float | None
    unit: str
    source: SourceRef
    raw: dict

    @property
    def ref(self) -> str:
        """Устойчив идентификатор за цитиране: `КСС.xlsx!Водопровод!4`.

        Етап 2: вместо да търсим стойността назад по сходство, даваме на
        модела как да СОЧИ реда, от който взима числото.  После кодът
        проверява дали цитатът е верен.  Цитат + проверка е далеч по-силно
        от обратно сравнение по думи.
        """
        return f"{self.source.document}!{self.source.sheet}!{self.source.row}"


def _tokens(text: str) -> set[str]:
    return {
        w.lower() for w in _WORD_RE.findall(text or "")
        if len(w) > 2 and w.lower() not in _STOPWORDS
    }


def similarity(a: str, b: str) -> float:
    """Дял на общите значещи думи (Jaccard).  0..1."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _number(value: Any) -> float | None:
    """Число от 420, '420', '1 240', '1,240.5'."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    # Български формат: 1.240,5 → 1240.5 ; английски: 1,240.5 → 1240.5
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") \
            else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(re.sub(r"[^\d.\-]", "", text) or "nan")
    except ValueError:
        return None


# Заглавия на колони, които носят описание / количество / мярка.
_DESC_KEYS = ("наименование", "описание", "дейност", "позиция", "description", "item")
_QTY_KEYS = ("количество", "к-во", "кол-во", "quantity", "qty")
_UNIT_KEYS = ("мярка", "ед. мярка", "ед.мярка", "мерна", "unit", "uom")


def _pick(row: dict, keys: tuple[str, ...]) -> tuple[str, Any]:
    """Върни (име на колона, стойност) за първата колона, чието заглавие пасва."""
    for column, value in row.items():
        lowered = str(column).lower()
        if any(key in lowered for key in keys):
            return str(column), value
    return "", None


def build_quantity_index(base_path: str | Path) -> list[QuantityRow]:
    """Индексирай количествените редове от конвертираните документи.

    Работи върху `converted/*.json`.  Табличните файлове (Excel/CSV) дават
    точен произход — документ, лист и ред.  Текстовите документи се пропускат:
    от свободен текст не може да се посочи клетка, а фалшив произход е
    по-лош от липсващ.

    Args:
        base_path: Папката на проекта.

    Returns:
        Списък от `QuantityRow`.
    """
    converted = Path(base_path) / "converted"
    if not converted.exists():
        return []

    index: list[QuantityRow] = []

    for jf in sorted(converted.glob("*.json")):
        if jf.name == "_manifest.json":
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        document = data.get("source_file", jf.stem)
        for sheet in data.get("sheets") or []:
            sheet_name = sheet.get("name", "")
            for offset, row in enumerate(sheet.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                desc_col, description = _pick(row, _DESC_KEYS)
                qty_col, quantity = _pick(row, _QTY_KEYS)
                _, unit = _pick(row, _UNIT_KEYS)

                description = str(description or "").strip()
                if not description:
                    continue

                index.append(QuantityRow(
                    description=description,
                    quantity=_number(quantity),
                    unit=str(unit or "").strip(),
                    # +2: ред 1 е заглавията, а Excel брои от 1.
                    source=SourceRef(document, sheet_name, offset + 2, qty_col or desc_col),
                    raw=row,
                ))

    logger.info("Индексирани %d количествени реда от %s", len(index), base_path)
    return index


def format_boq_for_prompt(index: list[QuantityRow], max_rows: int = 400) -> str:
    """Изобрази количествените редове като таблица с ЦИТИРУЕМИ идентификатори.

    Дава на модела структурирани количества вместо да ги търси в слепен текст,
    и — по-важното — начин да посочи откъде взима всяко число.

    Args:
        index: Индексът от `build_quantity_index`.
        max_rows: Таван, за да не изяде промпта при огромна КСС.

    Returns:
        Готов за промпта блок, или празен низ при празен индекс.
    """
    if not index:
        return ""

    lines = [
        "КОЛИЧЕСТВА ОТ ДОКУМЕНТИТЕ (всеки ред има идентификатор за цитиране):",
        "ref | описание | мярка | количество",
    ]
    for row in index[:max_rows]:
        quantity = "" if row.quantity is None else f"{row.quantity:g}"
        lines.append(
            f"{row.ref} | {row.description} | {row.unit} | {quantity}"
        )
    if len(index) > max_rows:
        lines.append(f"[... още {len(index) - max_rows} реда не са показани]")
    return "\n".join(lines)


class CitationCheck(NamedTuple):
    """Резултат от проверка на един цитат."""

    status: str          # verified | mismatch | unknown_ref | uncited
    ref: str
    expected: float | None = None
    actual: float | None = None
    note: str = ""


CITE_VERIFIED = "verified"
CITE_MISMATCH = "mismatch"
CITE_UNKNOWN = "unknown_ref"
CITE_UNCITED = "uncited"


def _norm_unit(unit: str) -> str:
    """Нормализирай мярка за сравнение: 'м2' == 'M2' == 'кв.м'."""
    u = str(unit or "").strip().lower().replace(" ", "").replace(".", "")
    return {"кв.м": "м2", "кв.м.": "м2", "куб.м": "м3", "квм": "м2",
            "кубм": "м3", "m2": "м2", "m3": "м3", "m": "м", "бр": "бр"}.get(u, u)


def _cross_check(task: dict, row: QuantityRow) -> str:
    """Провери дали задачата и цитираният ред са за ЕДНА И СЪЩА позиция.

    Числото вече съвпада; тук се лови случаят, в който то съвпада случайно
    между различни позиции.  Проверяват се мярка и материал — но само
    когато и двете страни ги имат (липсваща стойност не обвинява).

    Returns:
        Празен низ ако всичко пасва; иначе обяснение защо е несъответствие.
    """
    from src.duration_calculator import detect_material

    task_unit = _norm_unit(task.get("unit", ""))
    row_unit = _norm_unit(row.unit)
    if task_unit and row_unit and task_unit != row_unit:
        return f"мярката не съвпада: задача '{task_unit}' vs ред '{row_unit}'"

    task_mat = detect_material(task)
    row_mat = detect_material({"name": row.description})
    if task_mat and row_mat and task_mat != row_mat:
        return f"материалът не съвпада: задача '{task_mat}' vs ред '{row_mat}'"

    return ""


def verify_citations(schedule: list[dict], index: list[QuantityRow]) -> dict:
    """Провери цитатите, които моделът е дал за количествата.

    Четири изхода, всеки със своя тежест:
      verified    — цитираният ред съществува и количеството съвпада
      mismatch    — редът съществува, но числото е различно  ← най-опасното
      unknown_ref — цитиран е несъществуващ ред (измислен цитат)
      uncited     — няма цитат

    `mismatch` е по-лош от липсващ цитат: изглежда като доказателство, а не е.

    Args:
        schedule: Списък задачи (МУТИРА се — добавя се `quantity_provenance`).
        index: Индексът от `build_quantity_index`.

    Returns:
        Обобщение с брой по статус и подробности за проблемните.
    """
    by_ref = {row.ref: row for row in index}
    counts = {CITE_VERIFIED: 0, CITE_MISMATCH: 0, CITE_UNKNOWN: 0, CITE_UNCITED: 0}
    problems: list[dict] = []
    human = 0

    for task in schedule:
        if not isinstance(task, dict):
            continue
        quantity = _number(task.get("length_m") or task.get("quantity"))
        if quantity is None:
            continue

        # Ръчно въведена стойност не се сверява срещу документ — тя ГО
        # заменя.  Иначе човешката корекция би изглеждала като несъответствие.
        if (task.get("quantity_provenance") or {}).get("status") == STATUS_HUMAN:
            human += 1
            continue

        ref = str(task.get("source_ref") or "").strip()
        if not ref:
            check = CitationCheck(CITE_UNCITED, "", quantity, None,
                                  "моделът не е посочил източник")
        elif ref not in by_ref:
            check = CitationCheck(CITE_UNKNOWN, ref, quantity, None,
                                  "цитираният ред не съществува")
        else:
            row = by_ref[ref]
            actual = row.quantity
            if actual is None:
                check = CitationCheck(CITE_MISMATCH, ref, quantity, None,
                                      "редът няма количество")
            elif abs(actual - quantity) / max(abs(actual), 1e-9) > _QUANTITY_TOLERANCE:
                check = CitationCheck(CITE_MISMATCH, ref, quantity, actual,
                                      "числото не съвпада с цитирания ред")
            else:
                # Одит 2026-07-24: числото съвпадаше → verified, БЕЗ да се
                # проверява мярка/материал.  Възпроизведено: „Асфалт 420 м2"
                # цитира „PE DN110, 420 м" и получаваше verified само защото
                # 420=420.  Това е ФАЛШИВО доказателство за произход.  Сега
                # съвпадащото число, но различна МЯРКА или МАТЕРИАЛ, е mismatch.
                mismatch_note = _cross_check(task, row)
                if mismatch_note:
                    check = CitationCheck(CITE_MISMATCH, ref, quantity, actual,
                                          mismatch_note)
                else:
                    check = CitationCheck(CITE_VERIFIED, ref, quantity, actual)

        counts[check.status] += 1
        task["quantity_provenance"] = {
            "status": (STATUS_EXTRACTED if check.status == CITE_VERIFIED
                       else STATUS_AI_REPORTED),
            "citation": check.status,
            "ref": check.ref or None,
            "source": by_ref[ref].source.describe() if check.status == CITE_VERIFIED else None,
            "expected": check.expected,
            "actual": check.actual,
        }
        if check.status != CITE_VERIFIED:
            problems.append({
                "id": task.get("id"),
                "name": task.get("name"),
                "status": check.status,
                "ref": check.ref,
                "quantity": check.expected,
                "actual": check.actual,
                "note": check.note,
            })

    if counts[CITE_MISMATCH] or counts[CITE_UNKNOWN]:
        logger.warning(
            "Цитати за количества: %d невалидни (%d несъвпадащи, %d несъществуващи реда).",
            counts[CITE_MISMATCH] + counts[CITE_UNKNOWN],
            counts[CITE_MISMATCH], counts[CITE_UNKNOWN],
        )

    total = sum(counts.values()) + human
    return {
        "total": total,
        "verified": counts[CITE_VERIFIED],
        "mismatch": counts[CITE_MISMATCH],
        "unknown_ref": counts[CITE_UNKNOWN],
        "uncited": counts[CITE_UNCITED],
        "human": human,
        "problems": problems,
    }


class Match(NamedTuple):
    """Съответствие между стойност в графика и ред в документ."""

    row: QuantityRow
    score: float
    quantity_matches: bool


def find_source(
    description: str,
    quantity: float | None,
    index: list[QuantityRow],
    *,
    unit: str = "",
) -> Match | None:
    """Намери реда, от който най-вероятно идва тази стойност.

    Съвпадението по КОЛИЧЕСТВО тежи повече от съвпадението по описание —
    числото е по-специфично от думите.  Задача без съвпадащо количество не
    се сверява, дори името да съвпада.

    Returns:
        `Match`, или None ако нищо не отговаря достатъчно.
    """
    if not index:
        return None

    best: Match | None = None

    for row in index:
        name_score = similarity(description, row.description)
        qty_ok = False

        if quantity is not None and row.quantity:
            delta = abs(row.quantity - quantity) / max(abs(row.quantity), 1e-9)
            qty_ok = delta <= _QUANTITY_TOLERANCE

        if unit and row.unit and unit.lower() != row.unit.lower():
            # Различна мярка — м3 изкоп не е м тръба, дори имената да си приличат.
            continue

        score = name_score + (0.5 if qty_ok else 0.0)
        if name_score < _MIN_NAME_SIMILARITY and not qty_ok:
            continue

        if best is None or score > best.score:
            best = Match(row, round(score, 3), qty_ok)

    return best


def _quantity_of(task: dict) -> float | None:
    return _number(task.get("length_m") or task.get("quantity"))


def requested_task_ids(message: str, known_ids: set[str]) -> set[str]:
    """Кои от СЪЩЕСТВУВАЩИТЕ task ID-та човекът е споменал в съобщението.

    Търсят се известните ID-та като цели думи в текста — така се хващат и
    „T5", и „В01", и голо „A", без да се разчита на строг шаблон, и без
    случаен низ да мине за задача.
    """
    text = f" {(message or '').upper()} "
    hits: set[str] = set()
    for tid in known_ids:
        token = str(tid).upper()
        if not token:
            continue
        # Цяла дума: обградена от неалфанумерични граници.
        pattern = r"(?<![A-ZА-Я0-9])" + re.escape(token) + r"(?![A-ZА-Я0-9])"
        if re.search(pattern, text):
            hits.add(tid)
    return hits


def mark_human_overrides(
    before: list[dict], after: list[dict], message: str = "",
) -> int:
    """Бележи количествата, които ЧОВЕКЪТ изрично е поискал да промени.

    BACKLOG т.3 етап 3: когато човек каже „промени количеството на T5 на 450"
    и промяната мине през gate-а, новата стойност идва от ЧОВЕК, не от AI.

    Одит 2026-07-24: досега се маркираше ВСЯКА променена задача.  Но AI връща
    целия график и може да пипне и задачи, които човекът не е поискал —
    те получаваха погрешно `human_override`.  Възпроизведено: човек променя
    A, AI променя и B; и двете ставаха human_override.

    Сега: ако в съобщението има конкретни task ID-та, се маркират САМО те.
    Промени по други задачи остават `ai_modified` — човекът не отговаря за тях.
    Ако съобщението няма ID-та (напр. „намали всички с 10%"), се пада към
    старото поведение — всяка промяна е човешка, защото е поискана общо.

    Args:
        before: Графикът ПРЕДИ модификацията.
        after: Графикът СЛЕД нея (МУТИРА се).
        message: Заявката на човека — за да се разбере какво е поискал.

    Returns:
        Брой маркирани като human_override.
    """
    before_qty = {
        str(t.get("id")): _quantity_of(t)
        for t in before if isinstance(t, dict) and t.get("id")
    }
    known_ids = set(before_qty)
    requested = requested_task_ids(message, known_ids)

    marked = 0
    for task in after:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id"))
        new_qty = _quantity_of(task)
        if new_qty is None:
            continue

        old_qty = before_qty.get(tid)
        if old_qty is None or abs((old_qty or 0) - new_qty) <= 1e-9:
            continue  # непроменена задача

        # Ако човекът е посочил конкретни задачи, промяна по ДРУГА задача не е
        # негова — не е поискана.  Маркира се като AI намеса.
        if requested and tid not in requested:
            task["quantity_provenance"] = {
                "status": STATUS_AI_REPORTED,
                "citation": None,
                "source": None,
                "note": "AI промени тази задача без изрична заявка от човека",
            }
            continue

        task["quantity_provenance"] = {
            "status": STATUS_HUMAN,
            "citation": None,
            "source": "ръчно въведено през чата",
            "expected": new_qty,
            "actual": None,
        }
        marked += 1

    if marked:
        logger.info("Маркирани %d ръчно променени количества (human_override).", marked)
    return marked


def annotate_schedule(schedule: list[dict], index: list[QuantityRow]) -> dict:
    """Свери количествата в графика срещу индекса и запиши произхода.

    Всяка задача получава `quantity_provenance`:
        {status, source, matched_description, score}

    Args:
        schedule: Списък задачи (МУТИРА се на място).
        index: Индексът от `build_quantity_index`.

    Returns:
        {verified, unverified, total, details}
    """
    verified = 0
    unverified = 0
    details: list[dict] = []

    for task in schedule:
        if not isinstance(task, dict):
            continue

        quantity = _number(task.get("length_m") or task.get("quantity"))
        if quantity is None:
            continue

        match = find_source(
            task.get("name", ""), quantity, index, unit=str(task.get("unit", ""))
        )

        if match and match.quantity_matches:
            task["quantity_provenance"] = {
                "status": STATUS_EXTRACTED,
                "source": match.row.source.describe(),
                "matched_description": match.row.description,
                "score": match.score,
            }
            verified += 1
        else:
            task["quantity_provenance"] = {
                "status": STATUS_AI_REPORTED,
                "source": None,
                "matched_description": match.row.description if match else None,
                "score": match.score if match else 0.0,
            }
            unverified += 1
            details.append({
                "id": task.get("id"),
                "name": task.get("name"),
                "quantity": quantity,
                "closest": match.row.description if match else None,
            })

    return {
        "verified": verified,
        "unverified": unverified,
        "total": verified + unverified,
        "details": details,
    }
