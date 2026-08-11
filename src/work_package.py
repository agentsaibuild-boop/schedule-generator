"""Пространствен работен пакет — междинният слой между КСС и задачите.

ЗАЩО (съпоставка с еталонен човешки график, 2026-08-06): програмата групираше
задачите по ДИАМЕТЪР и по условен „Фронт 1/2", а не по реални трасета.  Двата
дефекта от това са структурни, не козметични:

  1. Фронтовете КЛОНИРАХА количествата.  „Фронт 1" и „Фронт 2" получаваха
     ЦЕЛИТЕ 3880,5 м бордюри всеки — сборът беше двойно по-голям от КСС.
     Фронтът не беше разпределение на работата, а копие на реда.

  2. Зависимостите бяха вътре в дисциплината, но не МЕЖДУ дисциплините за
     един и същ участък.  Затова бордюрите тръгваха в ден 1, преди изкопа под
     тях — логически коректен мрежов график, физически невъзможен обект.

Еталонът (610 задачи) е организиран точно обратното: 23 водопроводни и 46
канализационни ПАКЕТА, всеки от които е реално трасе между два възела
(„кл. 48 от РШ 36 до Пр. Ш 1"), с технологична верига от 6-9 дейности.

Затова тук пакетът е НОСИТЕЛЯТ на количеството, а задачите се извеждат от
него.  Следствието е, че запазването на количеството става структурно:
фронтовете разпределят ПАКЕТИ, не преписват позиции, така че сборът не може
да надхвърли КСС по конструкция — а `check_conservation` го доказва.

Модулът е ДЕТЕРМИНИСТИЧЕН: никакъв AI, никаква аритметика на продължителности.
Продължителностите остават на `duration_calculator` / productivities.json —
тук се слага само стойността от шаблона като запълване, което следващият
детерминистичен проход има право да замени.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "tech_chains.json"

# Един и същ допуск като в provenance — сборът е „равен" на КСС до 2%.
QUANTITY_TOLERANCE = 0.02

# Възлите в имената на участъците издават мрежата: РШ → канализация,
# ОТ/Т → водопровод.  Извлечено от еталона, не предположено.
# Буквеният суфикс на възела („ОТ 27 А") е ЕДНА буква, която НЕ е начало на
# следваща дума — иначе „до" в „от РШ 25 до РШ 22" се залепва към номера.
_NODE_RE = re.compile(
    r"(?:^|[\s,])(?P<kind>РШ|СРШ|Пр\.?\s?Ш|ОТ|Т|СК)\s*\.?\s*"
    r"(?P<num>[\dIVX]+(?:\s*[А-Яа-я](?![А-Яа-я]))?)",
    re.IGNORECASE | re.UNICODE,
)
_BRANCH_RE = re.compile(
    r"(?:^|[\s,])(?:ГЛ\.)?(?:КЛ|кл)\s*\.?\s*(?P<num>[\dIVX]+\s*[-–]?\s*[А-Яа-я]?)",
    re.UNICODE,
)


# ---------------------------------------------------------------------------
# Модел
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageItem:
    """Едно количество от КСС, попадащо в този пакет.

    `source_ref` е цитатът към реда (`КСС.xlsx!Водопровод!4`).  `quantity` е
    ЧАСТТА от реда, паднала се на този пакет — сборът по всички пакети за
    един ref трябва да е равен на количеството в реда.
    """

    source_ref: str
    activity_class: str
    quantity: float
    unit: str = ""
    description: str = ""
    #: Неизменният ключ на реда (`QuantityRow.record_id`).  `source_ref` казва
    #: КЪДЕ пише количеството, това — КОЕ количество е.  Разминат ли се двата,
    #: значи индексът е от друга версия на документа (одит 10.08.2026, P0.1).
    source_record_id: str = ""
    # DN и материал идват от САМИЯ КСС ред, не от модела (виж `_row_pipe_spec`).
    dn: int | None = None
    material: str = ""

    def __post_init__(self) -> None:
        if not str(self.source_ref).strip():
            raise ValueError("PackageItem без source_ref — цитатът е задължителен")


@dataclass(frozen=True)
class SpatialWorkPackage:
    """Физически участък: конкретно трасе, конкретни количества, конкретен екип."""

    id: str
    network: str                      # "В" | "К" | "П" | "ЕЛ"
    chain: str                        # ключ в tech_chains.chains
    name: str = ""
    branch: str = ""                  # „кл. 48"
    street: str = ""
    start_node: str = ""              # „РШ 36"
    end_node: str = ""                # „Пр. Ш 1"
    chainage_from: float | None = None
    chainage_to: float | None = None
    dn: int | None = None
    material: str = ""
    front: str = ""                   # реален фронт/екип, не етикет
    items: tuple[PackageItem, ...] = ()
    #: Дали улицата и възлите са ПОТВЪРДЕНИ от ситуационния чертеж.
    #
    # Пакет за одитора 10.08.2026: при прогон БЕЗ чертеж моделът пак попълва
    # възли — „от ОТ 1 до ОТ 2", „от П1 до П2", „от К1 до К2" — поредни
    # номера с измислени конвенции, и една и съща улица за
    # всичките 16 пакета.  Изнесени в MS Project, те изглеждат като прочетена
    # геометрия.  Непотвърдената идентичност не напуска програмата.
    spatial_verified: bool = False

    @property
    def label(self) -> str:
        """Име за WBS реда — както в еталона: клон + от възел + до възел."""
        if self.name:
            return self.name
        # Както в еталона: водещ е КЛОНЪТ („кл. 48 от РШ 36 до Пр. Ш 1").
        # Улицата е резервният идентификатор, не добавка към клона.
        head = self.branch or self.street or f"Участък {self.id}"
        if self.start_node and self.end_node:
            return f"{head} от {self.start_node} до {self.end_node}"
        return head

    @property
    def axis_id(self) -> str:
        """Оста, по която се мери пикетажът на ТОЗИ пакет.

        ЖИВ ПРОГОН 2026-08-07: моделът върна една и съща улица
        за всички 11 пакета и ЛОКАЛЕН пикетаж от 0 за всеки.  При обща ос това
        значи, че всеки пакет се застъпва с всеки — 307 фалшиви „пространствени
        конфликта" и невалиден график.

        Локалните метри на един участък обаче НЕ са същите физически метри като
        на друг: „0÷1182" по кл. DN315 и „0÷260" по кл. DN400 са различни
        трасета.  Затова всеки пакет мери по СВОЯ ос.  Групирането по улица за
        междудисциплинните връзки остава отделно (`link_cross_discipline`
        работи с улицата, не с тази ос).

        Идентификаторът е ЧАСТ от оста, а не украса: при второто изпитание
        моделът върна ЕДНО И СЪЩО име за шест канализационни пакета и осите
        пак съвпаднаха.  Уникалността не бива да зависи от това колко
        старателно моделът е кръстил участъците.
        """
        parts = [p for p in (self.street.strip(), self.label.strip()) if p]
        head = " · ".join(parts)
        return f"{head} [{self.id}]" if head else self.id

    @property
    def length_m(self) -> float | None:
        if self.chainage_from is None or self.chainage_to is None:
            return None
        return abs(self.chainage_to - self.chainage_from)


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------


def load_chains(path: str | Path | None = None) -> dict[str, Any]:
    """Зареди технологичните вериги (извлечени от еталонния график)."""
    target = Path(path) if path else _CONFIG_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("tech_chains.json не може да се зареди (%s): %s", target, exc)
        return {"chains": {}, "wbs_roots": [], "cross_discipline": {"rules": []}}


def parse_package_name(name: str) -> dict[str, str]:
    """Извлечи клон и възли от име като „кл. 48 от РШ 36 до Пр. Ш 1".

    Форматът е този на еталона.  Мрежата се извежда от ТИПА на възела, не се
    гадае по думи: РШ/СРШ/Пр.Ш → канализация, ОТ/Т/СК → водопровод.

    Returns:
        {branch, start_node, end_node, network} — липсващите са празен низ.
    """
    text = str(name or "").strip()
    out = {"branch": "", "start_node": "", "end_node": "", "network": ""}
    if not text:
        return out

    branch = _BRANCH_RE.search(text)
    if branch:
        out["branch"] = f"кл. {branch.group('num').strip()}"

    nodes = [
        (m.group("kind").upper().replace(" ", "").replace(".", ""),
         f"{m.group('kind').strip()} {m.group('num').strip()}")
        for m in _NODE_RE.finditer(text)
    ]
    # „от X до Y" — първите два възела след ключовите думи са краищата.
    if len(nodes) >= 2:
        out["start_node"], out["end_node"] = nodes[0][1], nodes[1][1]
    elif len(nodes) == 1:
        out["start_node"] = nodes[0][1]

    if nodes:
        kinds = {k for k, _ in nodes}
        if kinds & {"РШ", "СРШ", "ПРШ", "ШАХТА"}:
            out["network"] = "К"
        elif kinds & {"ОТ", "Т", "СК"}:
            out["network"] = "В"
    return out


# ---------------------------------------------------------------------------
# От отговора на модела към пакети
# ---------------------------------------------------------------------------

# Коя верига обслужва коя мрежа, когато моделът не е казал изрично.
_CHAIN_BY_NETWORK = {
    "К": "sewer_section",
    "В": "water_section",
    "П": "pavement_section",
    "ЕЛ": "cable_section",
}

# Как моделът пише мрежата в реалния живот.  ЖИВ ПРОГОН 2026-08-07: върна
# „ЕЛ/ТТ" — стойност, която не беше в таблицата, и два кабелни пакета отпаднаха
# като „неопределима верига".  Нормализацията е по-евтина от загубена работа.
_NETWORK_ALIASES = {
    "ЕЛ": "ЕЛ", "ТТ": "ЕЛ", "ЕЛ/ТТ": "ЕЛ", "ЕЛ И ТТ": "ЕЛ", "ЕЛИТТ": "ЕЛ",
    "EL": "ЕЛ", "TT": "ЕЛ", "CABLE": "ЕЛ", "КАБЕЛ": "ЕЛ",
    "К": "К", "КАНАЛИЗАЦИЯ": "К", "KAN": "К",
    "В": "В", "ВОДОПРОВОД": "В", "VIK": "В",
    "П": "П", "ПЪТНА": "П", "ПЪТНИ": "П", "НАСТИЛКИ": "П",
}


def _normalize_network(raw: str) -> str:
    key = str(raw or "").strip().upper().replace(".", "")
    return _NETWORK_ALIASES.get(key, key)


def _normalize_node(node: str) -> str:
    """Възел за сравнение: „РШ  36" и „рш36" са един и същ възел."""
    return re.sub(r"[\s.]+", "", str(node or "")).lower()


def _node_pair_on_drawing(start: str, end: str, drawn: set[tuple[str, str]]) -> bool:
    """Дали двойката възли наистина стои на ситуационния чертеж.

    Без чертеж (`drawn` е празно) отговорът е НЕ за всички — тогава възлите са
    съчинени от модела и не бива да излизат от програмата като геометрия.
    Посоката не значи нищо: участък от РШ 36 до РШ 37 е същият като обратното.
    """
    if not drawn or not start or not end:
        return False
    a, b = _normalize_node(start), _normalize_node(end)
    return (a, b) in drawn or (b, a) in drawn


def packages_from_ai(
    data: Any,
    *,
    boq_index: Iterable[Any] | None = None,
    chains: dict[str, Any] | None = None,
    segments: Iterable[dict] | None = None,
    spatial_source: Any = None,
) -> tuple[list[SpatialWorkPackage], list[str]]:
    """Превърни отговора на модела в пакети — с проверка, не с доверие.

    ТРУСТ ГРАНИЦА: моделът казва САМО кой пакет съществува и КОЛКО от даден
    ред от КСС му се пада.  Класът на дейността НЕ е негов избор — той се
    извежда детерминистично от ОПИСАНИЕТО на цитирания ред
    (`provenance._coverer_class`).  Причината е същата, поради която
    provenance статусите са server-owned: ако моделът може да обяви „това е
    полагане", той може и да накара грешна работа да покрие ред.

    Невалиден пакет НЕ се поправя мълчаливо — изхвърля се и се докладва.
    Количествата му после липсват в `check_conservation`, тоест графикът пада
    fail-closed, вместо да изглежда пълен.

    Args:
        data: dict от модела с ключ `packages` (или самият списък).
        boq_index: Редовете от КСС — източникът на класа и на проверката на
            цитата.  Без него класът може да дойде само от `activity_class`
            в отговора, което е по-слабо и се отбелязва в предупрежденията.
        chains: Зареденият tech_chains (по подразбиране от файла).

    Returns:
        (пакети, предупреждения).
    """
    from src.provenance import _coverer_class  # локален внос: избягва цикъл

    cfg = chains if chains is not None else load_chains()
    known_chains = set(cfg.get("chains") or {})
    rows = {str(getattr(r, "ref", "")): r for r in (boq_index or [])}

    raw_packages = data.get("packages") if isinstance(data, dict) else data
    if not isinstance(raw_packages, list):
        return [], ["отговорът няма списък `packages`"]

    packages: list[SpatialWorkPackage] = []
    errors: list[str] = []
    seen_ids: set[str] = set()

    # ДВЕ УСЛОВИЯ, НЕ ЕДНО (одит 10.08.2026, P1.1).  За да напуснат програмата
    # като геометрия, възлите трябва и да са потвърдени от подадените участъци,
    # И източникът им да е авторитетен.  Прочетеното от PDF чертеж върши работа
    # за КРЪЩАВАНЕ на участъците, но не е доказателство за топология — затова
    # там `spatial_verified` остава False, а имената пак се ползват.
    from src.spatial_source import SpatialSource, is_authoritative

    source = SpatialSource(spatial_source) if spatial_source is not None else (
        SpatialSource.PDF_SUGGESTIONS_ONLY if segments else SpatialSource.NONE)
    authoritative = is_authoritative(source)

    # Двойките възли, които НАИСТИНА стоят в подадените участъци.
    drawn = {
        (_normalize_node(s.get("start_node")), _normalize_node(s.get("end_node")))
        for s in (segments or [])
        if isinstance(s, dict) and s.get("start_node") and s.get("end_node")
    }

    for position, raw in enumerate(raw_packages, 1):
        if not isinstance(raw, dict):
            errors.append(f"пакет #{position}: не е обект")
            continue

        pkg_id = str(raw.get("id") or "").strip() or f"PKG{position}"
        if pkg_id in seen_ids:
            errors.append(f"пакет {pkg_id}: повторен идентификатор — пропуснат")
            continue

        name = str(raw.get("name") or "").strip()
        parsed = parse_package_name(name)
        network = _normalize_network(raw.get("network")) or parsed["network"]
        # Веригата е НАШЕ понятие, не на модела.  ЖИВ ПРОГОН 2026-08-07: при
        # допитването моделът върна измислени ключове („water_supply",
        # „sewage") и 8 пакета с реална работа отпаднаха.  Затова непознат
        # ключ не отхвърля пакета — извежда се от МРЕЖАТА, която моделът и
        # без това посочва по-надеждно.  Отпада само пакет, чиято мрежа също
        # не значи нищо: тогава наистина няма как да се разгъне.
        chain = str(raw.get("chain") or "").strip()
        if chain not in known_chains:
            fallback = _CHAIN_BY_NETWORK.get(network, "")
            if chain:
                errors.append(
                    f"пакет {pkg_id}: непозната верига {chain!r} → "
                    f"{fallback or 'няма'} по мрежа {network!r}")
            chain = fallback

        if chain not in known_chains:
            errors.append(
                f"пакет {pkg_id}: неопределима верига (мрежа {network!r}) — пропуснат")
            continue

        items: list[PackageItem] = []
        for raw_item in raw.get("items") or []:
            if not isinstance(raw_item, dict):
                continue
            ref = str(raw_item.get("source_ref") or "").strip()
            if not ref:
                errors.append(f"пакет {pkg_id}: количество без цитат — пропуснато")
                continue

            quantity = _as_number(raw_item.get("quantity"))
            if quantity is None or quantity <= 0:
                errors.append(
                    f"пакет {pkg_id}: количество {quantity!r} за {ref} — пропуснато")
                continue

            row = rows.get(ref)
            if rows and row is None:
                errors.append(f"пакет {pkg_id}: цитат {ref} не е ред от КСС — пропуснат")
                continue

            item_dn: int | None = None
            item_material = ""
            if row is not None:
                activity = _coverer_class(row)
                description = str(getattr(row, "description", "") or "")
                unit = str(getattr(row, "unit", "") or "")
                item_dn, item_material = _row_pipe_spec(row)
                conflict = diameter_conflict(row)
                if conflict is not None:
                    errors.append(
                        f"DIAMETER_CONFLICT {ref}: описанието казва DN{conflict[0]}, "
                        f"колоната за диаметър — DN{conflict[1]}.  Диаметърът НЕ се "
                        "приема и продължителността остава недоказана, докато човек "
                        "не реши кой е верният.")
            else:
                activity = str(raw_item.get("activity_class") or "").strip() or None
                description = str(raw_item.get("description") or "")
                unit = str(raw_item.get("unit") or "")
                errors.append(
                    f"пакет {pkg_id}: класът за {ref} идва от модела (няма КСС индекс)")

            if not activity:
                errors.append(
                    f"пакет {pkg_id}: класът на {ref} е неопределим по описанието "
                    "— нужен е човешки преглед")
                continue

            items.append(PackageItem(source_ref=ref, activity_class=activity,
                                     quantity=quantity, unit=unit,
                                     description=description,
                                     dn=item_dn, material=item_material,
                                     source_record_id=str(
                                         getattr(row, "record_id", "") or "")))

        seen_ids.add(pkg_id)
        start_node = str(raw.get("start_node") or "").strip() or parsed["start_node"]
        end_node = str(raw.get("end_node") or "").strip() or parsed["end_node"]
        packages.append(SpatialWorkPackage(
            id=pkg_id,
            network=network or "К",
            # Методът на полагане се чете от количествата, не от шаблона —
            # виж `trenchless_chain`.
            chain=trenchless_chain(chain, items),
            name=name,
            branch=str(raw.get("branch") or "").strip() or parsed["branch"],
            street=str(raw.get("street") or "").strip(),
            start_node=start_node,
            end_node=end_node,
            spatial_verified=(
                authoritative
                and _node_pair_on_drawing(start_node, end_node, drawn)),
            chainage_from=_as_number(raw.get("chainage_from")),
            chainage_to=_as_number(raw.get("chainage_to")),
            dn=_as_int(raw.get("dn")),
            material=str(raw.get("material") or "").strip(),
            items=tuple(items),
        ))

    return packages, errors


def _row_pipe_spec(row: Any) -> tuple[int | None, str]:
    """DN и материал от КЛЕТКИТЕ на КСС реда, а не от модела.

    ЖИВ ПРОГОН 2026-08-07: продължителностите не се смятаха — `MISSING_DN` за
    12 задачи.  Причината: описанието на реда е само „Изграждане на смесена
    канализационна мрежа", а диаметърът стои в СЪСЕДНА КОЛОНА:
    `"Диаметър Ф /mm/": "Ф500, РP"`.  Моделът понякога го препише в името на
    пакета, понякога не — а това е документ, не преценка.

    Кирилското „РP" в реалния файл се нормализира от `detect_material`
    (урок за омоглифи), затова тук просто подаваме целия ред като текст.
    """
    from src.duration_calculator import detect_dn, detect_material

    raw = getattr(row, "raw", None) or {}
    cells = " ".join(
        str(v) for k, v in raw.items()
        if v not in (None, "") and not str(k).startswith("__")
    )
    probe = {"name": f"{getattr(row, 'description', '')} {cells}"}

    # МАТЕРИАЛЪТ СЪЩО МОЖЕ ДА Е ЧОВЕШКО РЕШЕНИЕ.  Досега този канал важеше само
    # за диаметъра, а редът „Реконструкция на Главни водопроводни клонове
    # (Ф200 E)" носи материала като едно-единствено „Е" — низ, който не е
    # никой от разпознаваните шаблони и НЕ бива да се отгатва (урок #35: CI и
    # PE имат различни норми).  Проба 10.08.2026: точно този ред остави
    # 881,45 m главен водопровод без доказана продължителност.
    #
    # Решението е инженерно, не програмно — затова се чете от
    # `config/boq_resolutions.json` с `field: "material"`, автор и дата, точно
    # както решението за диаметъра.
    decided_material = resolved_value(row, "material")
    material = (str(decided_material).strip() if decided_material
                else detect_material(probe) or "")

    # РАЗМИНАВАНЕ МЕЖДУ ОПИСАНИЕТО И КОЛОНАТА (одит 10.08.2026, P1.4).
    #
    # „Реконструкция на Главни водопроводни клонове (Ф200 E)" носи Ф225 в
    # колоната за диаметър.  Досега двете се слепваха в един низ и `detect_dn`
    # взимаше което намери първо — тоест документът противоречи сам на себе си,
    # а програмата избира мълчаливо и продължава да смята продължителност по
    # избраното.  Това е точно „уверено сгрешено" вместо „недоказано".
    decided = resolved_value(row, "dn")
    if decided is not None:
        return int(decided), material

    if diameter_conflict(row) is not None:
        return None, material

    return detect_dn(probe), material


_RESOLUTIONS_PATH = Path(__file__).resolve().parent.parent / "config" / "boq_resolutions.json"


def load_boq_resolutions() -> list[dict]:
    """Човешките решения по противоречия в КСС.  Празен списък при липса."""
    try:
        data = json.loads(_RESOLUTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in (data.get("resolutions") or []) if isinstance(r, dict)]


def resolved_value(row: Any, field: str) -> Any:
    """Стойността, която ЧОВЕК е определил за този ред, или `None`.

    Ключът е `record_id` — съдържанието на реда, не мястото му.  Точно за
    това служи: решение, взето днес, оцелява разместване на редове утре, но
    отпада, ако количеството се смени, защото тогава редът е друг.
    """
    record_id = str(getattr(row, "record_id", "") or "")
    if not record_id:
        return None
    for resolution in load_boq_resolutions():
        if resolution.get("field") != field:
            continue
        if record_id in (resolution.get("record_ids") or []):
            return resolution.get("value")
    return None


def diameter_conflict(row: Any) -> tuple[int, int] | None:
    """(DN от описанието, DN от колоната), ако се разминават.

    Връща `None`, когато няма конфликт — тоест когато поне единият източник
    мълчи или двата казват едно и също.  Разминаването не се решава тук:
    кой е верният е инженерен въпрос, не програмен.
    """
    from src.duration_calculator import detect_dn

    # Решен от човек конфликт вече не е конфликт.  Записът стои в
    # `config/boq_resolutions.json` с автор и дата, тоест графикът може да
    # каже не само какъв диаметър е взел, а и кой го е решил.
    if resolved_value(row, "dn") is not None:
        return None

    raw = getattr(row, "raw", None) or {}
    description = str(getattr(row, "description", "") or "")

    columns = " ".join(
        str(v) for k, v in raw.items()
        if v not in (None, "") and not str(k).startswith("__")
        and "диаметър" in str(k).lower()
    )
    if not columns:
        return None

    from_description = detect_dn({"name": description})
    from_column = detect_dn({"name": columns})
    if from_description is None or from_column is None:
        return None
    if from_description == from_column:
        return None
    return from_description, from_column


# Мерки, при които количеството Е дължина.  „m3/m'" (бетонов кожух) НЕ е —
# подаването му като `length_m` би сметнало полагане по обемно число.
_LENGTH_UNITS = {"m", "м", "m'", "м'", "метър", "метра", "lm", "лм"}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None else None


# Кой клас в коя верига принадлежи, когато е попаднал не на място.
# Само класовете, които НЕ се покриват от тръбните вериги — останалите
# (изкоп, засипка, шахти, изпитване) ги има и в двете и не се местят.
_HOME_CHAIN_BY_CLASS = {
    "pavement": "pavement_section",
    "cable": "cable_section",
    "disinfection": "water_section",
}


#: Думите, с които българският КСС описва БЕЗИЗКОПНО полагане.  Същият речник
#: като `duration_calculator._HDD_RE` — методът трябва да значи едно и също
#: нещо за веригата и за нормата, иначе графикът описва сондаж, а го смята по
#: тарифа за изкоп.
_TRENCHLESS_RE = re.compile(
    r"безизкоп|\bHDD\b|сондаж|сондир|хоризонтал\w*\s+сонд|pipe\s*burst|микротунел",
    re.IGNORECASE,
)

#: Веригата за водопровод по открит изкоп → нейният безизкопен вариант.
_TRENCHLESS_CHAIN = {"water_section": "water_section_hdd"}


def trenchless_chain(chain: str, items: Iterable[PackageItem]) -> str:
    """Открит изкоп или сондаж — решава ТЪРГЪТ, не шаблонът и не моделът.

    ПРОБА 10.08.2026: `water_section` носеше `method: "HDD"` за всеки
    водопроводен участък, защото еталонният график е правен с
    хоризонтален сондаж.  Един обект така ставаше правило за всички: в MS
    Project излизаше „стациониране на сондажната машина" и екип със сондьор за
    търг, който може изобщо да не иска сондаж.  Освен това HDD норми има само
    за DN90/110/125 — DN160 и нагоре оставаха без продължителност
    (NO_PRODUCTIVITY_RULE), при положение че открити норми за тях съществуват.

    Затова веригите са две, а изборът е ДЕТЕРМИНИСТИЧЕН и се чете от
    описанието на количествата.  Мълчи ли КСС за метода, важи откритият изкоп:
    той е обичайният, и нормите му покриват целия диапазон диаметри.

    Решението НЕ се дава на модела — `water_section_hdd` съзнателно липсва от
    речника, който му се предлага.
    """
    target = _TRENCHLESS_CHAIN.get(chain)
    if not target:
        return chain
    for item in items:
        if item.activity_class == "laying" and _TRENCHLESS_RE.search(
                item.description or ""):
            return target
    return chain


def _chain_from_description(item: PackageItem, covers: dict[str, set]) -> str:
    """Коя верига е домът на позиция, чийто клас се среща в НЯКОЛКО вериги.

    `laying` и `manhole` ги има и в канализационната, и във водопроводната
    верига, затова първата версия отказваше да ги мести — и позиция за тръби,
    попаднала в пакет за настилка, оставаше без стъпка (10 прогона, 2026-08-07:
    `no_matching_step` за laying и manhole).

    Отказът беше излишен: ОПИСАНИЕТО на реда казва коя мрежа е.  Това е
    документ, не преценка.  Ако и то мълчи — не гадаем.
    """
    desc = f"{item.description}".lower()
    if any(word in desc for word in ("канализац", "дъждовн", "ско", "отток", "уо")):
        chain = "sewer_section"
    elif any(word in desc for word in ("водопровод", "сво", "водомер", "хидрант")):
        chain = "water_section"
    else:
        return ""
    return chain if item.activity_class in covers.get(chain, set()) else ""


def normalize_over_allocation(
    packages: list[SpatialWorkPackage],
    boq_index: Iterable[Any],
    *,
    tolerance: float = QUANTITY_TOLERANCE,
    max_excess: float = 0.25,
    max_shortfall: float = 0.25,
) -> tuple[list[SpatialWorkPackage], list[str]]:
    """Изравни към КСС сбор, който леко се разминава с количеството в реда.

    ЖИВ ПРОГОН (10 прогона с отсечки, 2026-08-07): 6 от 10 отпаднаха с
    „превишено количество".  Причината не е дублиране, а РАЗПРЕДЕЛИТЕЛЕН
    ДРЕЙФ: моделът дели един ред между няколко участъка на око и сборът излиза
    105-115%.

    Разделението КОЙ участък колко поема е преценка на модела; ОБЩОТО
    количество е факт от документа.  Затова тук общото се налага, а
    пропорцията се запазва — същата логика като „продължителностите идват от
    нормите, параметрите от модела".

    ВАЖНО: изравнява се САМО дрейф.  Превишение над `max_excess` (по
    подразбиране 25%) остава БЛОКИРАЩО — двоен сбор означава клонирана работа,
    а не закръгляне, и мълчаливото ѝ намаляване би скрило реален дефект.

    И В ДВЕТЕ ПОСОКИ (проба 10.08.2026).  Дотук се свиваше само превишението, а
    НЕДОСТИГЪТ не се поправяше от нищо: ред, разпределен на 92%, блокираше
    прогона мълчаливо.  Нито един от трите пътя не го хващаше — допитването
    пита само за редове с НУЛЕВО разпределение (`conservation["missing"]`),
    пренасочването сменя носителя без да мени количеството, а свиването гледа
    само нагоре.  Доводът обаче е един и същ в двете посоки: общото количество
    е ФАКТ ОТ ДОКУМЕНТА, а пропорцията между участъците е преценка на модела.
    Затова недостиг до `max_shortfall` се разтяга пропорционално, а по-голям
    остава блокиращ — той не е дрейф, а пропуснат участък.

    Returns:
        (пакети, бележки за изравненото).
    """
    required: dict[str, float] = {}
    for row in boq_index:
        qty = getattr(row, "quantity", None)
        ref = getattr(row, "ref", None)
        if ref and isinstance(qty, (int, float)) and not isinstance(qty, bool):
            required[str(ref)] = float(qty)

    planned: dict[str, float] = {}
    for pkg in packages:
        for item in pkg.items:
            planned[item.source_ref] = planned.get(item.source_ref, 0.0) + item.quantity

    factors: dict[str, float] = {}
    notes: list[str] = []
    for ref, want in required.items():
        got = planned.get(ref, 0.0)
        if got <= 0 or want <= 0:
            # Нула разпределено е ЛИПСВАЩ ред, не дрейф — него го поема
            # допитването, не пропорцията (няма какво да се мащабира).
            continue
        if got > want * (1 + tolerance):
            if got > want * (1 + max_excess):
                continue                 # твърде много — остава блокиращо
            factors[ref] = want / got
            notes.append(
                f"{ref}: сборът {got:.2f} надхвърля {want:.2f} с "
                f"{(got / want - 1) * 100:.1f}% — свит пропорционално")
        elif got < want * (1 - tolerance):
            if got < want * (1 - max_shortfall):
                continue                 # твърде малко — остава блокиращо
            factors[ref] = want / got
            notes.append(
                f"{ref}: сборът {got:.2f} не достига {want:.2f} с "
                f"{(1 - got / want) * 100:.1f}% — разтегнат пропорционално")

    if not factors:
        return packages, []

    adjusted = []
    for pkg in packages:
        items = tuple(
            replace(item, quantity=item.quantity * factors[item.source_ref])
            if item.source_ref in factors else item
            for item in pkg.items
        )
        adjusted.append(replace(pkg, items=items) if items != pkg.items else pkg)
    return adjusted, notes


def reroute_uncoverable_items(
    packages: list[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
) -> tuple[list[SpatialWorkPackage], list[str]]:
    """Премести количествата, попаднали в пакет, който не може да ги изпълни.

    ЖИВ ПРОГОН (10 прогона, 2026-08-07): 6 от 10 отпадаха с „непокрити редове",
    при това в част от тях количеството беше разпределено ПРАВИЛНО (Σ=КСС
    минаваше).  Причината: моделът закача ред за настилка към канализационен
    пакет.  `sewer_section` не покрива клас `pavement`, тоест количеството
    остава без стъпка и работата изчезва от графика.

    Статичната проверка показа, че дупка в конфигурацията НЯМА — всеки клас се
    покрива някъде.  Значи въпросът не е КОЙ може, а КЪДЕ е попаднал редът.

    Затова тук позицията се мести при пакет-близнак по СЪЩОТО трасе с
    подходящата верига — точно както еталонът държи настилките в отделен пакет
    по същата улица.  Количеството не се променя, само сменя носителя, тоест
    `check_conservation` остава изпълнен по конструкция.

    Returns:
        (пакети, бележки за преместеното).
    """
    cfg = chains if chains is not None else load_chains()
    chain_defs = cfg.get("chains") or {}
    covers = {
        key: {c for step in chain.get("steps") or [] for c in step.get("covers") or []}
        for key, chain in chain_defs.items()
    }

    result = {p.id: p for p in packages}
    order = [p.id for p in packages]
    notes: list[str] = []

    for pkg_id in list(order):
        pkg = result[pkg_id]
        stays, moves = [], []
        for item in pkg.items:
            if item.activity_class in covers.get(pkg.chain, set()):
                stays.append(item)
            else:
                moves.append(item)
        if not moves:
            continue

        for item in moves:
            target_chain = (_HOME_CHAIN_BY_CLASS.get(item.activity_class)
                            or _chain_from_description(item, covers))
            if target_chain not in chain_defs:
                stays.append(item)          # няма къде — остава да се докладва
                continue

            twin_id = f"{pkg.id}·{target_chain.split('_')[0]}"
            twin = result.get(twin_id)
            if twin is None:
                twin = SpatialWorkPackage(
                    id=twin_id,
                    network=str(chain_defs[target_chain].get("network") or ""),
                    chain=target_chain,
                    # СЪЩАТА улица — иначе междудисциплинната връзка (настилка
                    # след засипка) няма по какво да ги свърже.
                    street=pkg.street,
                    branch=pkg.branch,
                    name=f"{chain_defs[target_chain].get('label', target_chain)} — {pkg.label}",
                    front=pkg.front,
                )
                result[twin_id] = twin
                order.append(twin_id)
            result[twin_id] = replace(twin, items=twin.items + (item,))
            notes.append(
                f"{item.source_ref}: клас {item.activity_class!r} не се изпълнява от "
                f"{pkg.chain} → преместен в {twin_id}")

        result[pkg_id] = replace(pkg, items=tuple(stays))

    return [result[pid] for pid in order], notes


# ---------------------------------------------------------------------------
# ИНВАРИАНТЪТ: Σ количества по пакети == количеството в КСС
# ---------------------------------------------------------------------------


def check_conservation(
    packages: Iterable[SpatialWorkPackage],
    boq_index: Iterable[Any],
    *,
    tolerance: float = QUANTITY_TOLERANCE,
) -> dict[str, Any]:
    """Докажи, че всяко количество от КСС е разпределено ТОЧНО ВЕДНЪЖ.

    Това е проверката, която липсваше и заради която дублирането по фронтове
    минаваше: `analyze_boq_coverage` сумира по ЗАДАЧИ, а задача без цитат не
    участва в сбора — тоест клонинг без `source_ref` е невидим.  Тук се сумира
    по ПАКЕТИ, където цитатът е задължителен (`PackageItem.__post_init__`), и
    затова няма как една част от работата да остане извън сметката.

    Args:
        packages: Пакетите на проекта.
        boq_index: Редовете от КСС (`provenance.QuantityRow` или съвместими —
            ползват се само `.ref` и `.quantity`).
        tolerance: Относителен допуск (0.02 = 2%).

    Returns:
        {ok, over, short, unknown_ref, missing, totals} — `ok=False` при
        ЛИПСВАЩО или ПРЕВИШЕНО количество.  Превишението е блокиращо: то
        означава дублирана работа, тоест по-дълъг и по-скъп график.
    """
    required: dict[str, float] = {}
    for row in boq_index:
        qty = getattr(row, "quantity", None)
        ref = getattr(row, "ref", None)
        if ref and isinstance(qty, (int, float)) and not isinstance(qty, bool):
            required[str(ref)] = float(qty)

    planned: dict[str, float] = {}
    holders: dict[str, list[str]] = {}
    for pkg in packages:
        for item in pkg.items:
            ref = str(item.source_ref)
            planned[ref] = planned.get(ref, 0.0) + float(item.quantity)
            holders.setdefault(ref, []).append(pkg.id)

    over: dict[str, dict] = {}
    short: dict[str, dict] = {}
    for ref, want in required.items():
        got = planned.get(ref, 0.0)
        slack = abs(want) * tolerance
        if got > want + slack:
            over[ref] = {"required": want, "planned": got, "packages": holders.get(ref, [])}
        elif got < want - slack:
            short[ref] = {"required": want, "planned": got, "packages": holders.get(ref, [])}

    unknown = sorted(set(planned) - set(required))
    missing = sorted(ref for ref in required if ref not in planned)

    return {
        "ok": not over and not short and not unknown,
        "over": over,
        "short": short,
        "unknown_ref": unknown,
        "missing": missing,
        "totals": {ref: {"required": required.get(ref), "planned": planned.get(ref, 0.0)}
                   for ref in sorted(set(required) | set(planned))},
    }


def allocation_ledger(
    packages: Iterable[SpatialWorkPackage],
    boq_index: Iterable[Any],
    tasks: list[dict] | None = None,
) -> list[dict]:
    """Опис КОЙ РЕД КЪДЕ отиде — ред по ред, пакет по пакет.

    ОДИТ 2026-08-07: „От предоставения пакет не мога независимо да докажа
    Σ allocated = КСС — имам само резултата на техния gate."  Справедливо:
    даваме присъда, а не доказателство.

    Този опис е доказателството.  За всеки ред от КСС показва изискваното
    количество, разпределеното, разликата и кои пакети (и задачи) го носят.
    Който го чете, може да пресметне сбора сам.

    Returns:
        Списък редове, подредени по ref.
    """
    # Само редове С КОЛИЧЕСТВО.  Индексът съдържа и заглавия, междинни сборове
    # и „ОБЩО" — те нямат какво да бъде разпределено.  Влезли в описа, те дават
    # 42 реда „няма ред в КСС" срещу 28 истински и погребват доказателството
    # под шум (пакет за одитора, 10.08.2026).
    required: dict[str, Any] = {}
    for row in boq_index:
        ref = getattr(row, "ref", None)
        if ref and getattr(row, "quantity", None) is not None:
            required[str(ref)] = row

    tasks_by_ref: dict[str, list[str]] = defaultdict(list)
    for task in tasks or []:
        ref = str(task.get("source_ref") or "").strip()
        if ref:
            tasks_by_ref[ref].append(str(task.get("id")))

    holders: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pkg in packages:
        for item in pkg.items:
            holders[item.source_ref].append((pkg.id, float(item.quantity)))

    ledger: list[dict] = []
    for ref in sorted(set(required) | set(holders)):
        row = required.get(ref)
        want = getattr(row, "quantity", None) if row is not None else None
        got = sum(q for _, q in holders.get(ref, []))
        ledger.append({
            "ref": ref,
            "description": str(getattr(row, "description", "") or ""),
            "unit": str(getattr(row, "unit", "") or ""),
            "required": want,
            "allocated": round(got, 4),
            "difference": (round(got - want, 4) if isinstance(want, (int, float))
                           and not isinstance(want, bool) else None),
            "packages": [{"package": pid, "quantity": round(q, 4)}
                         for pid, q in sorted(holders.get(ref, []))],
            "tasks": sorted(tasks_by_ref.get(ref, [])),
            "status": _ledger_status(want, got),
        })
    return ledger


def _ledger_status(want: Any, got: float) -> str:
    if not isinstance(want, (int, float)) or isinstance(want, bool):
        return "няма ред в КСС"
    if got == 0:
        return "НЕРАЗПРЕДЕЛЕН"
    slack = abs(want) * QUANTITY_TOLERANCE
    if got > want + slack:
        return "ПРЕВИШЕН"
    if got < want - slack:
        return "НЕДОСТИГ"
    return "ок"


def format_allocation_ledger(ledger: list[dict]) -> str:
    """Описът като таблица за четене от човек."""
    lines = ["# Опис на разпределението (allocation ledger)", "",
             "Всеки ред от КСС: колко се иска, колко е разпределено и къде.",
             "Сборът може да бъде пресметнат независимо от този документ.", "",
             "| Ред от КСС | Мярка | В КСС | Разпределено | Разлика | Пакети | Статус |",
             "|---|---|---|---|---|---|---|"]
    for row in ledger:
        pkgs = ", ".join(f"{p['package']}: {p['quantity']:g}"
                         for p in row["packages"]) or "—"
        want = f"{row['required']:g}" if row["required"] is not None else "—"
        diff = f"{row['difference']:+g}" if row["difference"] is not None else "—"
        desc = (row["description"] or row["ref"])[:46]
        lines.append(f"| {desc} | {row['unit']} | {want} | "
                     f"{row['allocated']:g} | {diff} | {pkgs} | {row['status']} |")

    total_rows = len(ledger)
    clean = sum(1 for r in ledger if r["status"] == "ок")
    lines += ["", f"**{clean} от {total_rows} реда са разпределени точно.**"]
    return "\n".join(lines)


def conservation_messages(report: dict[str, Any]) -> list[str]:
    """Човешки формулировки на нарушенията — за UI и за gate съобщенията."""
    msgs: list[str] = []
    for ref, d in sorted(report.get("over", {}).items()):
        msgs.append(
            f"ПРЕВИШЕНО количество за {ref}: планирани {d['planned']:.2f} срещу "
            f"{d['required']:.2f} в КСС (пакети: {', '.join(d['packages'][:4])}) — "
            "дублирана работа")
    for ref, d in sorted(report.get("short", {}).items()):
        msgs.append(
            f"НЕДОСТИГ за {ref}: планирани {d['planned']:.2f} срещу "
            f"{d['required']:.2f} в КСС — част от работата липсва")
    for ref in report.get("missing", []):
        msgs.append(f"НЕРАЗПРЕДЕЛЕН ред от КСС: {ref} — няма нито един пакет")
    for ref in report.get("unknown_ref", []):
        msgs.append(f"НЕВАЛИДЕН цитат: {ref} — пакет сочи ред, който не е в КСС")
    return msgs


# ---------------------------------------------------------------------------
# Фронтове: разпределят ПАКЕТИ, не преписват позиции
# ---------------------------------------------------------------------------


def assign_fronts(
    packages: list[SpatialWorkPackage], num_fronts: int
) -> list[SpatialWorkPackage]:
    """Разпредели пакетите между фронтовете, БЕЗ да дублира количества.

    Тук е поправката на коренния дефект.  Досега „2 фронта" означаваше две
    копия на едни и същи позиции; сега означава, че ПАКЕТИТЕ се делят на две
    групи.  Всяко количество остава в точно един пакет, тоест сборът не може
    да се промени — запазването е структурно, не проверявано после.

    Балансът е по обем работа (сбор от количествата), не по брой пакети:
    greedy „най-натоварен последен" върху сортирани по големина пакети.
    """
    if num_fronts < 1:
        num_fronts = 1
    if num_fronts == 1:
        return [_with_front(p, "Фронт 1") for p in packages]

    load = [0.0] * num_fronts
    buckets: list[list[SpatialWorkPackage]] = [[] for _ in range(num_fronts)]

    def weight(pkg: SpatialWorkPackage) -> float:
        return sum(abs(float(i.quantity)) for i in pkg.items) or 1.0

    # Мрежите се балансират ПООТДЕЛНО — иначе един фронт може да получи цялата
    # канализация, а другият целия водопровод, и rolling wave-ът се обезсмисля.
    for network in sorted({p.network for p in packages}):
        group = sorted(
            (p for p in packages if p.network == network),
            key=lambda p: (-weight(p), p.id),
        )
        for pkg in group:
            idx = min(range(num_fronts), key=lambda i: (load[i], i))
            buckets[idx].append(pkg)
            load[idx] += weight(pkg)

    out: list[SpatialWorkPackage] = []
    for i, bucket in enumerate(buckets, 1):
        out.extend(_with_front(p, f"Фронт {i}") for p in bucket)
    return sorted(out, key=lambda p: p.id)


def _with_front(pkg: SpatialWorkPackage, front: str) -> SpatialWorkPackage:
    if pkg.front == front:
        return pkg
    return replace(pkg, front=front)


# ---------------------------------------------------------------------------
# Зона за възстановяване: настилките са МЯСТО, не ред от КСС
# ---------------------------------------------------------------------------


#: Веригите, чиито пакети описват ВЪЗСТАНОВЯВАНЕ на терена, а не полагане.
_RESTORATION_CHAIN = "pavement_section"


def merge_restoration_zones(
    packages: list[SpatialWorkPackage],
    *,
    spatial_authoritative: bool = True,
) -> tuple[list[SpatialWorkPackage], list[str]]:
    """Слей пътните пакети, които възстановяват едно и също място.

    ОДИТ 07.08.2026: „quantity conservation може да е 100% вярно, а execution
    scope пак да е дублиран."

    Механиката на дефекта е сглобка от две поотделно правилни решения.
    Моделът връща по ЕДИН пакет на КСС ред — асфалт, бордюри, унипаваж.  След
    миналия одит стъпките станаха ЗАДЪЛЖИТЕЛНИ: стъпка без количество пак ражда
    задача, защото изкопът и засипката се извършват независимо дали КСС ги
    остойностява отделно.  Заедно двете дават пакет „асфалт", който съдържа
    основен пласт + бордюри + асфалт; същото за другите два.  Сборът по редове
    остава точен, а обектът се асфалтира три пъти.

    Затова настилката се пакетира по ЗОНА, не по ред: една улица/трасе →
    един пакет → една верига → трите количества влизат в СВОИТЕ стъпки.

    Зоната е трасето (`street`/`branch`).  Когато моделът не е дал такова,
    зоната е една за целия обект — грубо, но честно: една верига вместо три,
    и точно толкова пространствена разделителна способност, колкото има.

    Returns:
        (пакети, бележки) — бележките описват всяко сливане за журнала.
    """
    zones: dict[str, list[SpatialWorkPackage]] = {}
    out: list[SpatialWorkPackage] = []

    for pkg in packages:
        if pkg.chain != _RESTORATION_CHAIN:
            out.append(pkg)
            continue
        # ДОГОВОРЪТ ЗА `suggested` (одит 10.08.2026): улица, прочетена от PDF
        # чертеж, е ЕТИКЕТ.  Тя не бива да дели обекта на зони, защото това е
        # твърдение за покритие — „тази работа е тук и никъде другаде".  Без
        # авторитетна геометрия зоната е една: по-грубо, но не измислено.
        key = ((pkg.street or pkg.branch or "").strip().lower()
               if spatial_authoritative else "")
        zones.setdefault(key, []).append(pkg)

    notes: list[str] = []
    for key, group in zones.items():
        if len(group) == 1:
            out.append(group[0])
            continue

        head = group[0]
        merged = replace(
            head,
            name=_zone_name(head),
            # Първата непразна пространствена стойност от групата: пакетите в
            # една зона описват едно място, но не всички носят всяко поле.
            street=next((p.street for p in group if p.street), head.street),
            branch=next((p.branch for p in group if p.branch), head.branch),
            start_node=next((p.start_node for p in group if p.start_node), head.start_node),
            end_node=next((p.end_node for p in group if p.end_node), head.end_node),
            # Слятата зона е потвърдена само ако ВСИЧКИ ѝ части са били.
            spatial_verified=all(p.spatial_verified for p in group),
            items=tuple(item for p in group for item in p.items),
        )
        out.append(merged)
        notes.append(
            f"Зона за възстановяване {merged.label!r}: слети {len(group)} пътни "
            f"пакета ({', '.join(p.id for p in group)}) — веригата се изпълнява "
            "веднъж, а не по веднъж на ред от КСС"
        )

    return out, notes


def enforce_construction_span(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Разтегли задачите, които по договор траят колкото обекта.

    ОДИТ 10.08.2026, P0.3: в изнесения файл СТРОИТЕЛСТВО върви
    25.05.2027 → 29.12.2029, а АВТОРСКИ НАДЗОР свършва 14.03.2029 — девет
    месеца и половина по-рано.

    Причината е, че надзорът получаваше медианата от еталона (660 дни) като
    всяка друга стъпка.  Но авторският надзор не е дейност с продължителност;
    той е задължение, което трае толкова, колкото трае строителството.  Същото
    важи за всяка стъпка с `spans_construction` във веригата.

    Инвариантът е двустранен: началото не по-късно от началото на
    строителството, краят не по-рано от края му.

    Returns:
        (задачи, бележки) — нов списък; входният не се мутира.
    """
    out = [dict(t) for t in tasks]
    spanning = [t for t in out if t.get("spans_construction")]
    if not spanning:
        return out, []

    building = [t for t in out
                if t.get("wbs_root") == "construction"
                and not t.get("is_summary")
                and t.get("start_day") is not None]
    if not building:
        return out, ["няма строителни задачи — надзорът остава както е"]

    start = min(int(t["start_day"]) for t in building)
    finish = max(int(t.get("end_day") or t["start_day"]) for t in building)

    notes: list[str] = []
    for task in spanning:
        was = (task.get("start_day"), task.get("end_day"))
        task["start_day"] = start
        task["end_day"] = finish
        task["duration"] = finish - start + 1
        task["duration_source"] = "construction_span"
        notes.append(
            f"{task.get('name', task.get('id'))}: разтеглена до строителството "
            f"({start}–{finish} вместо {was[0]}–{was[1]})")
    return out, notes


def _zone_name(pkg: SpatialWorkPackage) -> str:
    """Името на зоната е МЯСТОТО, а не първият ред от КСС, попаднал в нея."""
    where = (pkg.street or pkg.branch or "").strip()
    return f"Възстановяване на настилките — {where}" if where else \
        "Възстановяване на настилките"


# ---------------------------------------------------------------------------
# Разгъване: пакет → технологична верига от задачи + WBS
# ---------------------------------------------------------------------------


@dataclass
class ExpansionResult:
    tasks: list[dict] = field(default_factory=list)
    unplaced: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def expand_packages(
    packages: list[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
    *,
    wbs_root_name: str = "СТРОИТЕЛСТВО",
) -> ExpansionResult:
    """Превърни пакетите в задачи с WBS йерархия и технологични зависимости.

    Структурата повтаря еталона:
        ниво 1  СТРОИТЕЛСТВО
        ниво 2    кл. 48 от РШ 36 до Пр. Ш 1        (пакет)
        ниво 3      стъпките от веригата             (задачи)

    Всяка стъпка ражда по една задача за ВСЯКО количество от пакета, чийто
    клас попада в `covers` на стъпката — така редът е на човека, а всяка
    задача пак цитира точно един ред от КСС.  Стъпка без съответни количества
    (геодезия, изпитване, CCTV) ражда една задача без цитат.

    Количество, което не попада в НИТО ЕДНА стъпка, НЕ се изпуска мълчаливо —
    връща се в `unplaced`.  Тихо изпуснато количество е точно дефектът, който
    този модул съществува да предотврати.
    """
    cfg = chains if chains is not None else load_chains()
    chain_defs = cfg.get("chains", {})
    actions = {k: v for k, v in (cfg.get("class_actions") or {}).items()
               if not k.startswith("_")}
    result = ExpansionResult()

    # Корените на WBS-а идват от конфигурацията, но се създават САМО ако има
    # пакет, който стъпва в тях — иначе всеки график би носил празни клонове
    # „ПРОЕКТИРАНЕ" и „ПРИЕМАНЕ", каквито договорът може изобщо да не включва.
    root_names = {r["key"]: r["name"] for r in cfg.get("wbs_roots") or []}
    root_names.setdefault("construction", wbs_root_name)
    if wbs_root_name != "СТРОИТЕЛСТВО":
        root_names["construction"] = wbs_root_name
    roots_used: dict[str, str] = {}

    def root_for(chain_def: dict) -> str:
        key = chain_def.get("wbs_root", "construction")
        if key not in roots_used:
            root_id = f"WBS_{key.upper()}"
            roots_used[key] = root_id
            result.tasks.append({
                "id": root_id, "name": root_names.get(key, key.upper()),
                "type": "summary", "duration": 0, "dependencies": [],
                "is_summary": True,
            })
        return roots_used[key]

    for pkg in packages:
        chain = chain_defs.get(pkg.chain)
        if not chain:
            result.warnings.append(
                f"Пакет {pkg.id}: непозната верига {pkg.chain!r} — пропуснат")
            result.unplaced.extend(
                {"package": pkg.id, "source_ref": i.source_ref,
                 "activity_class": i.activity_class, "quantity": i.quantity,
                 "reason": "unknown_chain"} for i in pkg.items)
            continue

        pkg_task_id = f"{pkg.id}"
        result.tasks.append({
            "id": pkg_task_id, "name": pkg.label, "type": "summary",
            "parent_id": root_for(chain), "duration": 0, "dependencies": [],
            "is_summary": True, "network": pkg.network, "team": pkg.front,
            # БЕЗ пикетаж: обобщаващата задача е СБОР, не работа.  Ако носи
            # същите метри като децата си, пространствената проверка вижда
            # родителя и детето като два екипа на едно място (жив прогон
            # 2026-08-07: 307 фалшиви конфликта).
        })

        placed: set[int] = set()
        prev_ids: list[str] = []
        # Кои задачи е родила всяка стъпка — за връзките по ключ на стъпка.
        by_step_key: dict[str, list[str]] = {}

        for step in chain.get("steps", []):
            covers = set(step.get("covers") or [])
            # ЕДНО количество → ТОЧНО ЕДНА задача.  Без `placed` проверката
            # тук се възпроизвежда точно дефектът, който модулът премахва:
            # ред за настилка пасва на „основен пласт", „бордюри" И „асфалт",
            # тоест 900 m² биха станали 2700 m² работа.  Първата пасваща
            # стъпка взима количеството; `covers_keywords` насочва реда към
            # правилната стъпка, когато класът сам не различава (в КСС
            # трошеният камък, бордюрите и асфалтът са отделни редове, но
            # всички са клас `pavement`).
            matching = [
                (idx, item) for idx, item in enumerate(pkg.items)
                if idx not in placed
                and item.activity_class in covers
                and _keywords_match(step, item)
            ]
            step_ids: list[str] = []

            # ТОПОЛОГИЯТА НА ВЕРИГАТА, а не просто редът ѝ (одит 10.08.2026,
            # P0.2).  Стъпка със свой `predecessor` се закача за НЕГО и с
            # обявената връзка; без такъв — за предходната стъпка с FS, както
            # досега.  Оттам идват застъпванията: в човешкия еталон 23-те
            # проектантски стъпки дават 245 дни последователно и 120 със
            # своята топология.
            link_to, relation, lag = prev_ids, "FS", 0
            declared = str(step.get("predecessor") or "").strip()
            if declared:
                link_to = by_step_key.get(declared, prev_ids)
                relation = str(step.get("relation") or "FS").upper()
                lag = int(step.get("lag_days") or 0)

            if matching:
                for n, (idx, item) in enumerate(matching, 1):
                    placed.add(idx)
                    suffix = f"_{n}" if len(matching) > 1 else ""
                    tid = f"{pkg.id}_{step['key']}{suffix}"
                    step_ids.append(tid)
                    result.tasks.append(_step_task(
                        tid, pkg, step, link_to, item=item, actions=actions,
                        relation=relation, lag_days=lag))
            else:
                # СТЪПКИТЕ СА ЗАДЪЛЖИТЕЛНИ (одит 2026-08-07).
                #
                # Дотук стъпка с `covers`, за която пакетът няма количество, се
                # ПРОПУСКАШЕ — за да не се раждат празни задачи.  Предпоставката
                # е грешна: изкопът, изпитването за непропускливост и обратната
                # засипка се извършват независимо дали КСС ги остойностява
                # отделно.  В реалния търг те са вътре в тръбния ред.
                #
                # Следствието беше, че от 6-степенната канализационна верига в
                # готовия файл оставаха три задачи (геодезия → полагане → CCTV)
                # — оцеляваха точно стъпките БЕЗ `covers`.  Проверено в
                # експортирания XML: 1 до 4 стъпки на участък, никога 6 или 9.
                #
                # `covers` разпределя КОЛИЧЕСТВА; то не решава дали дейността
                # съществува.  Задача без цитат просто не доказва BOQ ред.
                tid = f"{pkg.id}_{step['key']}"
                step_ids.append(tid)
                result.tasks.append(_step_task(
                    tid, pkg, step, link_to, actions=actions,
                    relation=relation, lag_days=lag))

            if step_ids:
                by_step_key[str(step.get("key"))] = step_ids
                prev_ids = step_ids

        # Коя договорна фаза изпълнява тази задача — нужно на инварианта за
        # надзора и на диагностиката по фази (одит 10.08.2026, P0.3/P0.4).
        root_key = chain.get("wbs_root", "construction")
        for task in result.tasks:
            if task.get("parent_id") == pkg.id or task.get("id") == pkg_task_id:
                task.setdefault("wbs_root", root_key)

        for idx, item in enumerate(pkg.items):
            if idx not in placed:
                result.unplaced.append({
                    "package": pkg.id, "source_ref": item.source_ref,
                    "activity_class": item.activity_class,
                    "quantity": item.quantity, "reason": "no_matching_step"})

    if result.unplaced:
        result.warnings.append(
            f"{len(result.unplaced)} количества не попадат в нито една стъпка "
            "от веригата — работата не е планирана")
    return result


def _keywords_match(step: dict, item: PackageItem) -> bool:
    """Дали редът принадлежи на ТАЗИ стъпка, когато класът не различава.

    Без ключови думи стъпката приема всеки ред от своя клас.  С ключови думи
    приема само ред, чието описание ги съдържа — така „бордюри" не отива в
    стъпката за асфалт само защото и двете са клас `pavement`.

    Ключовите думи са РАЗГРАНИЧИТЕЛ, не гейт: ред БЕЗ описание не може да бъде
    разграничен, затова пада към първата стъпка от своя клас.  Обратното би
    изпускало реална работа заради липсващо текстово поле — по-лошо от това да
    я сложим в съседната стъпка, защото количеството просто изчезва от графика.
    """
    keywords = step.get("covers_keywords") or []
    if not keywords:
        return True
    haystack = f"{item.description} {item.unit}".strip().lower()
    if not item.description.strip():
        return True
    return any(str(k).lower() in haystack for k in keywords)


def _step_task(
    task_id: str,
    pkg: SpatialWorkPackage,
    step: dict,
    prev_ids: list[str],
    item: PackageItem | None = None,
    actions: dict[str, str] | None = None,
    relation: str = "FS",
    lag_days: int = 0,
) -> dict:
    """Една задача от веригата, закачена FS за предходната стъпка."""
    # ИМЕТО на задача с количество идва от КСС РЕДА, не от стъпката.
    #
    # Стъпките в еталона са съставни: „Изграждане на СКО и УО, обратно
    # засипване с уплътняване…" покрива и шахти, и засипка.  Ние разделяме на
    # по една задача за ред (един `source_ref`), но ако всички наследят
    # съставното име, класификаторът чете само първата дейност в него —
    # задачата за ЗАСИПКА се класифицира като `manhole` и не покрива своя ред.
    # Проверено: 3 от 9 реда оставаха непокрити при напълно покрит обект.
    #
    # Затова задачата се именува по реда, който доказва; пълната формулировка
    # на стъпката остава в `chain_step_name` за справка и за UI.
    #
    # Отпред застава ДЕЙСТВИЕТО за класа: КСС редовете често са съществителни
    # („Тръби PP DN300"), а класификаторът разпознава дейност по глагол —
    # без него дори вярно насочената задача не покрива реда си.
    label = step["name"]
    if item is not None and item.description.strip():
        action = (actions or {}).get(item.activity_class, "")
        label = (f"{action} — {item.description.strip()}" if action
                 else item.description.strip())
    task: dict[str, Any] = {
        "id": task_id,
        "name": f"{label} — {pkg.label}",
        "chain_step_name": step["name"],
        "parent_id": pkg.id,
        "type": "task",
        # Продължителността от шаблона е ЗАПЪЛВАНЕ: детерминистичният проход
        # (productivities.json) има право да я замени.  Маркира се като такава,
        # за да не изглежда като изчислена норма.
        "duration": float(step.get("median_days") or 1.0),
        "duration_source": "chain_template",
        "dependencies": [
            {"predecessor_id": pid, "type": relation, "lag_days": lag_days}
            for pid in prev_ids
        ],
        "team": pkg.front,
        "crew_id": pkg.front,
        "resources": list(step.get("crew") or []),
        "chain_step": step.get("key"),
        "network": pkg.network,
        "alignment_id": pkg.axis_id,
        "start_chainage": pkg.chainage_from,
        "end_chainage": pkg.chainage_to,
        # ПРОСТРАНСТВЕНАТА ИДЕНТИЧНОСТ ДО ИЗХОДА (одит 07.08.2026).
        #
        # „Липсват street, from_node, to_node, chainage_from, chainage_to,
        # spatial_segment_id."  Пакетът ги носеше от самото начало — те просто
        # спираха тук и не влизаха нито в задачата, нито в експорта.  Пак
        # същото: способността я има, в готовия файл я няма.
        #
        # Празните стойности се отсяват при експорта, не тук: задачата описва
        # какво знае пакетът, дори когато то е нищо.
        #
        # `spatial_segment_id` е НАШЕ id и винаги е вярно.  Улицата и възлите
        # излизат само когато чертежът ги потвърждава — иначе са съчинени от
        # модела и в MS Project биха изглеждали като прочетена геометрия.
        "spatial_segment_id": pkg.id,
        "street": pkg.street if pkg.spatial_verified else "",
        "from_node": pkg.start_node if pkg.spatial_verified else "",
        "to_node": pkg.end_node if pkg.spatial_verified else "",
    }
    if step.get("milestone"):
        # Съгласувания и разрешения са ТОЧКИ, не работа с продължителност.
        task["milestone"] = True
        task["duration"] = 0
    if step.get("spans_construction"):
        # Не е дейност с продължителност, а задължение за целия обект.
        task["spans_construction"] = True
    if step.get("contractual"):
        # Само договорните точки получават краен срок в MS Project
        # (`export_xml._apply_constraint`) — останалите се планират свободно.
        task["contractual"] = True
    if step.get("method"):
        task["method"] = step["method"]
    # DN/материал: първо от РЕДА (документа), после от пакета (модела).
    dn = (item.dn if item is not None and item.dn else None) or pkg.dn
    material = (item.material if item is not None and item.material else "") or pkg.material
    if dn is not None:
        task["dn"] = dn
    if material:
        task["material"] = material
    if item is not None:
        task["source_ref"] = item.source_ref
        task["source_record_id"] = item.source_record_id
        task["quantity"] = item.quantity
        task["unit"] = item.unit
        task["activity_class_hint"] = item.activity_class
        # `length_m` САМО когато количеството наистина е дължина.  Бетоновият
        # кожух е „m3/m'" — подаден като дължина, той би дал продължителност за
        # полагане, сметната по обемно число.  Недоказана продължителност е
        # по-добра от уверено сгрешена.
        if (item.activity_class == "laying"
                and str(item.unit).strip().lower() in _LENGTH_UNITS):
            task["length_m"] = item.quantity
    return task


def contract_packages(
    chains: dict[str, Any] | None = None,
    *,
    with_design: bool = False,
) -> list[SpatialWorkPackage]:
    """Фазите, които НЕ идват от КСС, а от договора.

    ОДИТ 2026-08-07: проектиране, мобилизация, авторски надзор и приемане ги
    имаше в конфигурацията, но НИЩО не ги създаваше — моделът връща само
    пространствени участъци, а те не следват от количествена сметка.  В
    готовия файл нямаше нито една от тези фази и нула milestone-и, докато
    брийфът ги отбелязваше като направени.  Отбелязвал съм наличие на
    способност, не присъствие в изхода.

    Затова тук се създават ДЕТЕРМИНИСТИЧНО, независимо от КСС:

        with_design=False  →  мобилизация, приемане
        with_design=True   →  + проектиране и авторски надзор (инженеринг)

    Нямат количества и затова не участват в Σ=КСС — те са обхват, не работа
    по сметка.
    """
    cfg = chains if chains is not None else load_chains()
    defined = cfg.get("chains") or {}

    keys = ["mobilization", "acceptance"]
    if with_design:
        keys = ["design", "mobilization", "supervision", "acceptance"]

    out: list[SpatialWorkPackage] = []
    for key in keys:
        chain = defined.get(key)
        if not chain:
            continue
        out.append(SpatialWorkPackage(
            id=f"ФАЗА_{key.upper()}",
            network=str(chain.get("network") or "ПР"),
            chain=key,
            name=str(chain.get("label") or key),
        ))
    return out


def link_contract_phases(
    tasks: list[dict],
    packages: list[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Подреди договорните фази около строителството и затвори графика.

    Две неща, които одитът намери в готовия файл:

      1. 12 задачи без наследник — графикът има дузина висящи краища, тоест
         „кога свършва обектът" няма еднозначен отговор и критичният път е
         ненадежден.
      2. Нула milestone-и — липсва договорната точка, към която всичко води.

    Тук всички крайни задачи се вливат във ФИНАЛНАТА точка на приемането, а
    строителството тръгва след откриването на площадката.  Авторският надзор
    се разпъва по строителството (SS от началото, FF към края), както е в
    еталона.

    Returns:
        (задачи, бележки).
    """
    cfg = chains if chains is not None else load_chains()
    chain_defs = cfg.get("chains") or {}
    out = [dict(t) for t in tasks]
    by_id = {str(t.get("id")): t for t in out}
    notes: list[str] = []

    pkg_chain = {p.id: p.chain for p in packages}

    def tasks_of(chain_key: str) -> list[dict]:
        ids = {pid for pid, ck in pkg_chain.items() if ck == chain_key}
        return [t for t in out
                if str(t.get("parent_id") or "") in ids and not t.get("is_summary")]

    def add(pred: str, succ: str, link: str = "FS", lag: int = 0, why: str = "") -> bool:
        task = by_id.get(succ)
        if task is None or pred == succ or pred not in by_id:
            return False
        deps = list(task.get("dependencies") or [])
        if any(_dep_id(d) == pred for d in deps):
            return False
        deps.append({"predecessor_id": pred, "type": link, "lag_days": lag,
                     "reason": why or "contract_phase"})
        task["dependencies"] = deps
        return True

    spatial = {"sewer_section", "water_section", "pavement_section",
               "cable_section", "structure"}
    build = [t for t in out
             if pkg_chain.get(str(t.get("parent_id") or "")) in spatial
             and not t.get("is_summary")]

    # --- проектиране → мобилизация ---
    design, mob = tasks_of("design"), tasks_of("mobilization")
    if design and mob:
        add(str(design[-1]["id"]), str(mob[0]["id"]), why="design_before_mobilization")

    # --- мобилизация → строителство (само НАЧАЛАТА, без предшественик) ---
    if mob and build:
        gate = str(mob[-1]["id"])           # „Откриване на строителна площадка"
        roots = [t for t in build if not (t.get("dependencies") or [])]
        added = sum(add(gate, str(t["id"]), why="site_opened") for t in roots)
        if added:
            notes.append(f"строителството тръгва след откриване на площадката "
                         f"({added} начални задачи)")

    # --- авторски надзор се разпъва по строителството ---
    sup = tasks_of("supervision")
    if sup and build:
        add(str(build[0]["id"]), str(sup[0]["id"]), "SS", 0, "supervision_spans")
        add(str(build[-1]["id"]), str(sup[0]["id"]), "FF", 0, "supervision_spans")

    # --- ВСИЧКИ висящи краища → приемането ---
    acceptance = tasks_of("acceptance")
    if acceptance:
        first_acceptance = str(acceptance[0]["id"])
        acceptance_ids = {str(t["id"]) for t in acceptance}
        successors = {
            _dep_id(d) for t in out for d in (t.get("dependencies") or [])
        }
        # Надзорът също влиза: той свършва с края на строителството (FF) и
        # трябва да предхожда приемането, иначе остава втори висящ край.
        loose = [t for t in out
                 if not t.get("is_summary")
                 and str(t["id"]) not in successors
                 and str(t["id"]) not in acceptance_ids]
        added = sum(add(str(t["id"]), first_acceptance, why="all_work_before_handover")
                    for t in loose)
        if added:
            notes.append(f"{added} висящи края вързани към приемането")

    return out, notes


def link_cross_discipline(
    tasks: list[dict],
    packages: list[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
    *,
    spatial_authoritative: bool = True,
) -> list[dict]:
    """Свържи дисциплините В РАМКИТЕ на един и същ участък.

    Това липсваше: бордюрите тръгваха в ден 1, защото зависимостите бяха само
    вътре в дисциплината.  Правилата идват от `tech_chains.cross_discipline` и
    се прилагат само между пакети, които делят едно трасе (една улица/клон).

    Връща НОВ списък; входният не се мутира.  Добавени са само липсващи връзки.
    """
    cfg = chains if chains is not None else load_chains()
    rules = (cfg.get("cross_discipline") or {}).get("rules") or []
    if not rules:
        return list(tasks)

    out = [dict(t) for t in tasks]
    by_id = {str(t.get("id")): t for t in out}
    pkg_by_id = {p.id: p for p in packages}

    def alignment_of(pkg: SpatialWorkPackage) -> str:
        # Пак договорът за `suggested`: улица от PDF не бива да РАЗДЕЛЯ
        # зависимостите, защото твърдението „настилката тук чака само канала
        # тук" е точно топология.  Без авторитетна геометрия трасето е едно и
        # настилката чака всички подземни работи — консервативно и вярно.
        if not spatial_authoritative:
            return ""
        return (pkg.street or pkg.branch or pkg.label).strip().lower()

    # трасе → пакети по верига
    by_alignment: dict[str, dict[str, list[str]]] = {}
    for pkg in packages:
        by_alignment.setdefault(alignment_of(pkg), {}).setdefault(
            pkg.chain, []).append(pkg.id)

    # Задачите на пакета, в реда на веригата — нужно за резервния избор.
    ordered: dict[str, list[str]] = {}
    for task in out:
        pid = str(task.get("parent_id") or "").strip()
        if pid in pkg_by_id:
            ordered.setdefault(pid, []).append(str(task.get("id")))

    def step_tasks(pkg_id: str, step_key: str, *, fallback: str = "") -> list[str]:
        """Задачите за дадена стъпка; при липса — краят/началото на пакета.

        Стъпка може да не се материализира: тя ражда задача само ако пакетът
        има количество от нейния клас.  Ако правилото сочи именно такава
        стъпка, връзката би изчезнала МЪЛЧАЛИВО и настилката пак би тръгнала в
        ден 1 — точно дефектът, който правилото премахва.  Затова се пада към
        последната задача на предшественика и първата на наследника.
        """
        prefix = f"{pkg_id}_{step_key}"
        found = [tid for tid in ordered.get(pkg_id, [])
                 if tid == prefix or tid.startswith(prefix + "_")]
        if found or not fallback:
            return found
        chain_tasks = ordered.get(pkg_id, [])
        if not chain_tasks:
            return []
        return [chain_tasks[-1]] if fallback == "last" else [chain_tasks[0]]

    for _, chains_here in by_alignment.items():
        for rule in rules:
            preds = chains_here.get(rule.get("predecessor_chain"), [])
            succs = chains_here.get(rule.get("successor_chain"), [])
            for pkg_p in preds:
                for pkg_s in succs:
                    if pkg_p == pkg_s and rule["predecessor_chain"] == rule["successor_chain"]:
                        pass  # вътре в същия пакет — позволено (изпитване → засипка)
                    # Между РАЗЛИЧНИ вериги връзката е задължителна и затова
                    # има резервен избор; вътре в една и съща верига редът вече
                    # е гарантиран от самата верига — там не се налага.
                    cross = rule["predecessor_chain"] != rule["successor_chain"]
                    for src in step_tasks(pkg_p, rule.get("predecessor_step", ""),
                                          fallback="last" if cross else ""):
                        for dst in step_tasks(pkg_s, rule.get("successor_step", ""),
                                              fallback="first" if cross else ""):
                            if src == dst:
                                continue
                            task = by_id[dst]
                            deps = list(task.get("dependencies") or [])
                            if any(_dep_id(d) == src for d in deps):
                                continue
                            deps.append({
                                "predecessor_id": src,
                                "type": rule.get("type", "FS"),
                                "lag_days": int(rule.get("lag_days", 0)),
                                "reason": rule.get("why", "cross_discipline"),
                            })
                            task["dependencies"] = deps
    return out


def _dep_id(dep: Any) -> str:
    if isinstance(dep, dict):
        return str(dep.get("predecessor_id") or dep.get("id") or "").strip()
    return str(dep or "").strip().split()[0] if dep else ""
