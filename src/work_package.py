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
import os
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

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
    #: Готовото име за показване, когато геометрията НЕ е потвърдена.
    #
    # Без възли „кл. 48 от РШ 36 до РШ 37" и „кл. 48 от РШ 37 до РШ 38" се
    # свиват до едно и също „кл. 48" — и това е вярно, защото двете
    # подразделения са измислени.  Но два реда с едно име не са два участъка
    # за никого, който чете графика.  Затова `number_execution_batches`
    # различава пакетите по това, което НАИСТИНА ги дели — мрежата и редът на
    # изпълнение — и оставя готовия резултат тук.
    batch_label: str = ""
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
        """Име за WBS реда — както в еталона: клон + от възел + до възел.

        ВЪЗЛИТЕ ИЗЛИЗАТ САМО КОГАТО СА ПОТВЪРДЕНИ (одит 10.08.2026, P1.1;
        проверено и поправено 18.08.2026).  Дотогава този метод лепеше
        `start_node`/`end_node`, без изобщо да гледа `spatial_verified` — тоест
        пакет със съчинени възли се изписваше ДОСЛОВНО като пакет с прочетена
        геометрия: „кл. 1 от КШ 1 до КШ 2".  Тези имена стигаха до задачите, до
        WBS-а и до изнесения MS Project файл, където никой отвън не може да ги
        различи от документ.

        Свободното `name` също идва право от модела, затова и то минава през
        същата проверка — иначе твърдението просто сменя полето, през което
        излиза.
        """
        from src.spatial_source import strip_node_claim

        if self.spatial_verified:
            if self.name:
                return self.name
        elif self.batch_label:
            return self.batch_label
        elif self.name:
            return strip_node_claim(self.name) or f"Участък {self.id}"
        # Както в еталона: водещ е КЛОНЪТ („кл. 48 от РШ 36 до Пр. Ш 1").
        # Улицата е резервният идентификатор, не добавка към клона.
        head = self.branch or self.street or f"Участък {self.id}"
        if not self.spatial_verified:
            # Клонът и улицата остават: при `suggested` те са законно име,
            # прочетено от чертеж.  Двойката възли — не.
            return strip_node_claim(head) or f"Участък {self.id}"
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

#: Как се чете мрежата в име на участък.  „кл. 1" се повтаря между мрежите на
#: всеки обект — без това двата реда не се различават в графика.
_NETWORK_LABEL = {
    "В": "водопровод",
    "К": "канализация",
    "П": "настилки",
    "ЕЛ": "кабели",
}

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
            chain=structure_chain(trenchless_chain(chain, items), items),
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

    # Етапите се номерират ТУК, преди `partition_diagnosis` да ги преброи:
    # без възли пакетите по един клон се изписват еднакво, а еднаквите имена
    # са един от трите ѝ признака за изродено разделяне.
    return number_execution_batches(packages), errors


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

    # КОГАТО КОЛОНАТА КАЗВА, ЧЕ Е ДИАМЕТЪР, СТОЙНОСТТА ѝ Е ДИАМЕТЪРЪТ.
    #
    # Слепването на всички клетки в един низ не стига, когато диаметърът е
    # ГОЛО число: „Главни водопроводни клонове m 300 673,09" не дава нищо, а
    # само „300" дава 300 — измерено 24.08.2026 върху техническата
    # спецификация на Харманли, чиято таблица е:
    #
    #     Водопроводна мрежа | Ед. мярка | Диаметър Ф /mm/ | Дължина L /m/
    #     Главни водопроводни клонове | m | 300 | 673,09
    #
    # Маркерът „Ф" стои в ЗАГЛАВИЕТО на колоната, а то се изхвърляше.  Без това
    # единственият ред на целия търг оставаше `MISSING_DN` и нула
    # продължителности по норма.
    for ключ, стойност in raw.items():
        име = str(ключ).lower()
        if str(ключ).startswith("__") or стойност in (None, ""):
            continue
        if not re.search(r"диамет|\bdn\b|ф\s*/?\s*mm", име):
            continue
        от_колоната = detect_dn({"name": str(стойност).strip()})
        if от_колоната:
            probe = {"name": f"{probe['name']} Ф{от_колоната}"}
            break

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


def _diameter_candidates(row: Any) -> tuple[int | None, int | None]:
    """(DN от описанието, DN от колоната) — без оглед дали има решение."""
    from src.duration_calculator import detect_dn

    raw = getattr(row, "raw", None) or {}
    description = str(getattr(row, "description", "") or "")
    columns = " ".join(
        str(v) for k, v in raw.items()
        if v not in (None, "") and not str(k).startswith("__")
        and "диаметър" in str(k).lower()
    )
    if not columns:
        return None, None
    return detect_dn({"name": description}), detect_dn({"name": columns})


def applied_resolutions(rows: list) -> list[dict]:
    """Човешките решения, ПРИЛОЖЕНИ в този график — като артефакт.

    ОДИТ 13.08.2026: „никога не избирай мълчаливо".  Проверката беше права по
    последствие и грешна по причина: конфликтът Ф200/Ф225 се засича коректно и
    е РЕШЕН от възложителя на 10.08 (`config/boq_resolutions.json`).  Но в
    изнесения пакет нямаше и следа от това решение — отвън мълчаливото
    приемане и решението изглеждат еднакво.  Оттам и изводът, че конфликтът се
    подминава.

    Затова решението пътува с графика: кой ред, кои са били кандидатите, коя
    стойност е приета, от кого и кога.
    """
    записи: list[dict] = []
    for row in rows or []:
        record_id = str(getattr(row, "record_id", "") or "")
        if not record_id:
            continue
        for resolution in load_boq_resolutions():
            if record_id not in (resolution.get("record_ids") or []):
                continue
            кандидати = []
            if resolution.get("field") == "dn":
                кандидати = [c for c in _diameter_candidates(row) if c is not None]
            записи.append({
                "conflict_ref": str(getattr(row, "source_ref", "") or ""),
                "record_id": record_id,
                "field": resolution.get("field"),
                "candidates": кандидати,
                "chosen_value": resolution.get("value"),
                "resolution_source": "human",
                "decided_by": resolution.get("decided_by"),
                "resolved_at": resolution.get("decided_on"),
                "note": resolution.get("note"),
                "conflict": resolution.get("conflict"),
            })
    return записи


def diameter_conflict(row: Any) -> tuple[int, int] | None:
    """(DN от описанието, DN от колоната), ако се разминават.

    Връща `None`, когато няма конфликт — тоест когато поне единият източник
    мълчи или двата казват едно и също.  Разминаването не се решава тук:
    кой е верният е инженерен въпрос, не програмен.
    """
    # Решен от човек конфликт вече не е конфликт.  Записът стои в
    # `config/boq_resolutions.json` с автор и дата, а самото решение пътува с
    # графика през `applied_resolutions` — иначе решено и подминато изглеждат
    # еднакво отвън (одит 13.08.2026).
    if resolved_value(row, "dn") is not None:
        return None

    from_description, from_column = _diameter_candidates(row)
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


#: Класове, които описват ТОЧКА или съоръжение, а не трасе.
_POINT_CLASSES = frozenset({"manhole"})
#: Класове, които доказват, че пакетът е трасе.
_LINEAR_CLASSES = frozenset({"laying", "cable"})


def structure_chain(chain: str, items: Iterable[PackageItem]) -> str:
    """Пакет само от точкови количества върви по веригата за СЪОРЪЖЕНИЕ.

    НЕЗАВИСИМ ОДИТ 18.08.2026: „водомерна шахта не може да получи пълната
    тръбна верига; монолитна РШ ползва structure/node семантика."  Флагът
    `template_applicability_ok` го хваща — но само да го хване не стига: този
    търг има СЕДЕМ точкови реда (СВО, водомерна шахта, СКО, УО единичен и
    двоен, преливна шахта, монолитна РШ) и когато моделът им даде собствени
    пакети, всеки получаваше изкоп, полагане, изпитване на налягане и
    дезинфекция за нещо, което не е трасе.

    Измерено: така НИТО ЕДИН жив прогон не можеше да излезе чист.

    Веригата `structure` съществува точно за това (покрива excavation, manhole,
    transport).  Пакет с поне едно линейно количество си остава трасе — тук се
    мести само онова, което няма нито метър.
    """
    класове = {item.activity_class for item in items}
    if not класове or (класове & _LINEAR_CLASSES):
        return chain
    if класове <= _POINT_CLASSES | {"excavation", "transport", "backfill"}:
        return "structure"
    return chain


def declared_laying_method() -> str:
    """Методът, ОБЯВЕН за този търг: „hdd" или „open" (празно = не е обявен).

    Изборът между открит изкоп и сондаж е решение на процедурата, не находка в
    описанието на количествата.  Измерено 19.08.2026: човешкият еталон за
    Илиянци съдържа 23 безизкопни задачи — точно колкото са водопроводните
    участъци, тоест ЦЕЛИЯТ водопровод е сондиран, за 36 екипо-дни.  Ние го
    моделирахме с открит изкоп: 247 задача-дни, седем пъти повече, и това беше
    цялата разлика при водопровода (407 наши дни срещу 190 негови).

    А КСС мълчи за метода — редовете казват само „Реконструкция на
    разпределителната мрежа".  Значи не може да се извади от текста; трябва да
    бъде КАЗАН, както се казват сроковете за проектиране и строителство.

    ПИТА СЕ ОТ 19.08.2026: въпросникът има стъпка `q_laying`, а отговорът
    минава през `tender_parameters.for_this_run`.  Средата остава като изход за
    мерене и за прогони без въпросник.
    """
    from src.tender_parameters import laying_method

    return laying_method()


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
    обявен = declared_laying_method()
    if обявен in ("hdd", "сондаж", "безизкопно", "trenchless"):
        return target
    if обявен in ("open", "открит", "изкоп"):
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


#: Стъпката, до която се закръгляват количествата в описа.
_QUANTITY_STEP = Decimal("0.01")


#: ПРИКАЧЕНА работа: СВО, СКО, УО и бетоновият кожух не са самостоятелни
#: участъци — те са операции ВЪРХУ участък и вече присъстват като стъпка в
#: неговата верига.  Ключът е дума от името на пакета, стойността — думи от
#: стъпката, която тази работа наистина изпълнява.
_ATTACHMENT_STEPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("сво",), ("реконструкция на сво", "връзки към съществува")),
    (("ско", "уо", "оттичане"), ("изграждане на ско",)),
    (("кожух", "кожуси"), ("монтаж на тръби", "полагане")),
)


def effective_chain_steps(pkg: Any, chain: dict) -> list[dict]:
    """Стъпките, които ТОЗИ пакет наистина трябва да роди.

    Един източник за разгъването и за гейта: иначе гейтът иска цялата верига
    от прикачена работа, която нарочно ражда само своите стъпки — и пада точно
    защото дублирането е премахнато.
    """
    стъпки = list(_attachment_scope(pkg, chain or {}).get("steps") or [])
    return _само_изисканите_части(pkg, стъпки)


#: Частите на РАБОТНИЯ ПРОЕКТ, които ТОЗИ търг изисква — `DESIGN_PARTS`.
#:
#: ЗАЩО (Тръстеник, 25.08.2026).  Проектната верига е извлечена от градски
#: обект с пълен обхват: геология, електро, конструктивна, отводняване,
#: паркоустройство.  Реалният търг изброява своите части поименно (А–З) и
#: изрично КАЗВА кои да НЕ се правят: „За нуждите на работното проектиране да
#: се ползва разработената… част Инженерна геология и хидрология" — тоест 30
#: дни, които не са наши.  Без филтър графикът обещава работа, за която никой
#: не плаща, и срокът се дели на грешен брой части.
#:
#: Празно значи „цялата верига" — старото поведение.
def _само_изисканите_части(pkg: Any, стъпки: list[dict]) -> list[dict]:
    import os

    верига = (pkg.get("chain") if isinstance(pkg, dict)
              else getattr(pkg, "chain", "")) or ""
    if str(верига) != "design":
        return стъпки
    обявени = str(os.getenv("DESIGN_PARTS", "") or "").strip()
    if not обявени:
        return стъпки
    искани = {ч.strip() for ч in обявени.split(",") if ч.strip()}
    подбрани = [с for с in стъпки if str(с.get("key")) in искани]
    return подбрани or стъпки


def _attachment_scope(pkg: Any, chain: dict) -> dict:
    """Прикачената работа получава СВОИТЕ стъпки, не цялата верига.

    ОДИТ 13.08.2026, P0.2: „SVO/SKO/УО/кожуси се разгъват като пълни
    pipe/structure chains и дублират операции, които вече съществуват в
    основните templates... quantity gate може да е зелен, а execution scope да
    е повторен."

    Прав е и това е най-тежкият моделен дефект в изнесеното.  `water_section`
    стъпка 8 вече съдържа „реконструкция на СВО", а `sewer_section` стъпка 5 —
    „Изграждане на СКО и УО".  Въпреки това седем СВО пакета се разгъваха
    отново по цялата 9-степенна водопроводна верига: ВОБД, разкъртване, изкоп,
    заваряване, изпитване, дезинфекция... за втори път, върху същия обхват.

    Количеството се разпределя веднъж, затова гейтът за количествата мълчи —
    той пази СБОРА, не ИЗПЪЛНЕНИЕТО.  Тук се пази изпълнението: пакет, който е
    прикачена работа, ражда само стъпките, които наистина е той.
    """
    ключови = _attachment_keywords(pkg)
    if not ключови:
        return chain

    стъпки = [
        step for step in chain.get("steps", [])
        if any(дума in str(step.get("name", "")).lower() for дума in ключови)
    ]
    if not стъпки:                       # непозната прикачена работа — не режем
        return chain
    return {**chain, "steps": стъпки}


def _attachment_keywords(pkg: Any) -> tuple[str, ...]:
    """Думите на стъпката, която този пакет изпълнява, или празно."""
    име = f"{getattr(pkg, 'name', '')} {getattr(pkg, 'label', '')}".lower()
    for маркери, стъпки in _ATTACHMENT_STEPS:
        if any(маркер in име for маркер in маркери):
            return стъпки
    return ()


def execution_scope_duplicates(packages: Iterable[Any], chains: dict,
                               tasks: Iterable[dict] | None = None) -> list[dict]:
    """Пакети, които ИЗПЪЛНЯВАТ работа, вече съдържаща се в родителска верига.

    Гейтът за количествата пази СБОРА; този — ОБХВАТА.  Двете се провалят
    поотделно: седем СВО пакета с коректно разпределени 174 бр. пак изпълняват
    изкопа и изпитването втори път.

    Мери се ПОРОДЕНОТО, не конфигурацията.  Първата версия сравняваше стъпките
    на веригата със стъпките на прикачената работа — тоест щеше да сочи всеки
    прикачен пакет и след като дублирането е премахнато.  Детектор, който
    свети червено винаги, не мери нищо.
    """
    defs = (chains or {}).get("chains", chains or {})
    породени: dict[str, int] = {}
    for task in tasks or []:
        if task.get("is_summary"):
            continue
        родител = str(task.get("parent_id") or "")
        if родител:
            породени[родител] = породени.get(родител, 0) + 1

    дубликати = []
    for pkg in packages or []:
        ключови = _attachment_keywords(pkg)
        if not ключови:
            continue
        chain = defs.get(getattr(pkg, "chain", "")) or {}
        всички = chain.get("steps") or []
        свои = [s for s in всички
                if any(д in str(s.get("name", "")).lower() for д in ключови)]
        if not свои:
            continue
        име = str(getattr(pkg, "id", ""))
        излезли = породени.get(име)
        if излезли is None:              # без задачи съдим по конфигурацията
            излезли = len(всички)
        if излезли > len(свои):
            дубликати.append({
                "package": име,
                "chain": getattr(pkg, "chain", ""),
                "emitted_tasks": излезли,
                "steps_that_are_its_own": len(свои),
            })
    return дубликати


def _exact_shares(packages: list, factors: dict[str, float],
                  required: dict[str, float]) -> dict[tuple[int, int], float]:
    """Дяловете, чийто сбор е ТОЧНО количеството от КСС.

    Пропорцията си остава на модела — тук се сменя само аритметиката: първите
    n-1 дяла се закръглят до стотна, а последният поема остатъка, за да няма
    закръгляне, което да не се сумира до нула.
    """
    места: dict[str, list[tuple[int, int, float]]] = {}
    for индекс, pkg in enumerate(packages):
        for позиция, item in enumerate(pkg.items):
            if item.source_ref in factors:
                места.setdefault(item.source_ref, []).append(
                    (индекс, позиция, item.quantity))

    резултат: dict[tuple[int, int], float] = {}
    for ref, записи in места.items():
        цел = Decimal(str(required[ref]))
        сбор = sum(Decimal(str(q)) for *_, q in записи)
        if сбор <= 0:
            continue
        раздадено = Decimal("0")
        for ред, (индекс, позиция, количество) in enumerate(записи):
            ако_последен = ред == len(записи) - 1
            if ако_последен:
                дял = цел - раздадено
            else:
                дял = (цел * Decimal(str(количество)) / сбор).quantize(
                    _QUANTITY_STEP, rounding=ROUND_HALF_UP)
                раздадено += дял
            резултат[(индекс, позиция)] = float(дял)
    return резултат


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
    from src.provenance import is_duration_row

    required: dict[str, float] = {}
    for row in boq_index:
        qty = getattr(row, "quantity", None)
        ref = getattr(row, "ref", None)
        if not ref or not isinstance(qty, (int, float)) or isinstance(qty, bool):
            continue
        if is_duration_row(row):
            # Ред с мярка „Календарни Дни" обявява СРОК на договорна фаза, а
            # не работа за разпределяне.  Да го искаме в участък значи да
            # обявим графика за непълен заради нещо, което няма къде да отиде
            # — и точно това накара пакетния път да пита модела (19.08.2026).
            continue
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

    # ТОЧНОСТТА НЕ ЗАВИСИ ОТ ТОВА ДАЛИ ИМА ДРЕЙФ.  Проба 14.08.2026: описът
    # пак показа -0.05 m² при унипаважа, защото сборът беше В допустимото и
    # редът изобщо не минаваше през точното разделяне — оставаше си със
    # закръгляването от модела.  Изравняване с коефициент и изравняване на
    # остатъка са две различни неща: първото е за дрейф, второто важи винаги.
    # НО САМО ОСТАТЪК ОТ ЗАКРЪГЛЯНЕ, не дрейф.  Първата версия на тази поправка
    # изравняваше всичко в допуска и счупи правилото „2% допуск си остава
    # допуск": 995 от 1000 е преценка на модела, не грешка в аритметиката.
    # Признакът е размерът: закръгляне до стотна върху n позиции не може да
    # надхвърли n стотни.
    брой_позиции: dict[str, int] = {}
    for pkg in packages:
        for item in pkg.items:
            брой_позиции[item.source_ref] = брой_позиции.get(item.source_ref, 0) + 1

    за_изравняване = dict(factors)
    for ref, want in required.items():
        got = planned.get(ref, 0.0)
        if got <= 0 or ref in за_изравняване:
            continue
        праг = float(_QUANTITY_STEP) * брой_позиции.get(ref, 1)
        if 0 < abs(got - want) <= праг:
            за_изравняване[ref] = want / got

    if not за_изравняване:
        return packages, []
    factors = за_изравняване

    # ТОЧНО В ДЕСЕТИЧНА ОБЛАСТ, не „почти".  ОДИТ 13.08.2026: описът показва
    # 1758.86 искани срещу 1758.88 разпределени (+0.02 m) и 18671 срещу
    # 18670.98 (-0.02 m²).  Причината: всяко количество се умножаваше по
    # коефициент в плаваща запетая независимо от другите, тоест закръгленията
    # не се сумират до нула.  Инженерно е дребно, но „28 от 28 точно" тогава
    # не е буквално вярно, а описът е ДОКАЗАТЕЛСТВОТО за Σ=КСС.
    #
    # Затова: първите n-1 дяла се закръглят, последният поема остатъка.
    exact = _exact_shares(packages, factors, required)

    adjusted = []
    for индекс, pkg in enumerate(packages):
        items = tuple(
            replace(item, quantity=exact[(индекс, позиция)])
            if (индекс, позиция) in exact else item
            for позиция, item in enumerate(pkg.items)
        )
        adjusted.append(replace(pkg, items=items) if items != pkg.items else pkg)
    return adjusted, notes


#: „DN 500", „DN500", „ф500" — диаметърът, изписан в описанието на реда.
_DN_IN_TEXT_RE = re.compile(r"(?:DN|ДН|ф)\s*(\d{2,4})", re.IGNORECASE)


def assign_orphan_rows(
    packages: list[SpatialWorkPackage],
    boq_index: Iterable[Any],
    chains: dict[str, Any] | None = None,
) -> tuple[list[SpatialWorkPackage], list[str]]:
    """Разпредели редовете, които моделът НЕ е поел и след повторните питания.

    ИЗМЕРЕНО 17.08.2026 върху 18 живи прогона: водещата причина график да не е
    чист са непокрити редове, а начело са трите „Бетонов кожух за тръба DN
    500/700/1000" — липсват в 7 от 18.  Същите три бяха отбелязани и на
    06.08.2026 с думите „моделът прави работата, но не цитира реда".

    Досега след двата опита кодът се отказваше и ги отчиташе като непокрити.
    Но кой участък може да поеме такъв ред НЕ е преценка — то е следствие от
    класа на реда и от диаметъра, изписан в самото описание, а и двете са наши,
    детерминистични данни.  Затова тук редът се разделя между участъците, които
    МОГАТ да го изпълнят, пропорционално на големината им.

    Пропорцията е по количество, не по брой: участък с 300 м тръба поема повече
    кожух от участък с 30 м.  Когато описанието сочи диаметър, кандидатите се
    стесняват до участъците с този диаметър — иначе кожух за DN 1000 би паднал
    и върху участък DN 160.

    Разпределението тук е на КОДА, не на модела, и всяка бележка го казва —
    така описът на произхода остава честен.

    Returns:
        (пакети, бележки) — по една бележка на разпределен ред.
    """
    from src.provenance import _coverer_class

    cfg = chains if chains is not None else load_chains()
    chain_defs = cfg.get("chains") or {}
    covers = {
        key: {c for step in chain.get("steps") or [] for c in step.get("covers") or []}
        for key, chain in chain_defs.items()
    }

    поети = {str(item.source_ref) for p in packages for item in p.items}
    result = list(packages)
    notes: list[str] = []

    for row in boq_index:
        ref = str(getattr(row, "ref", "") or "")
        want = getattr(row, "quantity", None)
        if not ref or ref in поети:
            continue
        if not isinstance(want, (int, float)) or isinstance(want, bool) or not want:
            continue

        клас = _coverer_class(row)
        if not клас:
            continue

        кандидати = [p for p in result if клас in covers.get(p.chain, set())]
        if not кандидати:
            continue

        описание = str(getattr(row, "description", "") or "")
        съвпадение = _DN_IN_TEXT_RE.search(описание)
        по_диаметър = ""
        if съвпадение:
            dn = int(съвпадение.group(1))
            стеснени = [p for p in кандидати if p.dn == dn]
            if стеснени:
                кандидати = стеснени
                по_диаметър = f" с DN {dn}"

        тегла = [sum(abs(float(i.quantity)) for i in p.items) or 1.0
                 for p in кандидати]
        общо = sum(тегла)
        дялове = [round(float(want) * т / общо, 6) for т in тегла]
        # Остатъкът отива в последния: сборът трябва да е ТОЧНО количеството
        # от реда, инак гейтът за Σ=КСС пада заради закръгление.
        дялове[-1] = round(float(want) - sum(дялове[:-1]), 6)

        by_id = {p.id: i for i, p in enumerate(result)}
        for pkg, дял in zip(кандидати, дялове):
            if дял <= 0:
                continue
            позиция = by_id[pkg.id]
            текущ = result[позиция]
            нов = PackageItem(
                source_ref=ref, activity_class=клас, quantity=дял,
                unit=str(getattr(row, "unit", "") or ""),
                description=описание,
                source_record_id=str(getattr(row, "record_id", "") or ""),
            )
            result[позиция] = replace(текущ, items=текущ.items + (нов,))

        notes.append(
            f"РАЗПРЕДЕЛЕНО ОТ КОДА: {ref} ({want} "
            f"{str(getattr(row, 'unit', '') or '')}) — моделът не го пое и след "
            f"повторните питания; разделен между {len(кандидати)} участъка"
            f"{по_диаметър}, пропорционално на количествата им")

    return result, notes


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
    from src.provenance import is_duration_row

    required: dict[str, float] = {}
    for row in boq_index:
        qty = getattr(row, "quantity", None)
        ref = getattr(row, "ref", None)
        if not ref or not isinstance(qty, (int, float)) or isinstance(qty, bool):
            continue
        if is_duration_row(row):
            # Ред с мярка „Календарни Дни" обявява СРОК на договорна фаза, а
            # не работа за разпределяне.  Да го искаме в участък значи да
            # обявим графика за непълен заради нещо, което няма къде да отиде
            # — и точно това накара пакетния път да пита модела (19.08.2026).
            continue
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


# ---------------------------------------------------------------------------
# ГОДНО ЛИ Е РАЗДЕЛЯНЕТО НА ОБЕКТА — детерминистична присъда, не преценка
# ---------------------------------------------------------------------------

#: Мрежите, чиито пакети са ТРАСЕ между два възела.  Пътните („П") се
#: пакетират по ЗОНА (`merge_restoration_zones`), затова не се броят тук.
_LINEAR_NETWORKS = ("К", "В", "ЕЛ")

#: Под този размер редът може почтено да е цял в един участък (шест крана,
#: една шахта).  Над него един пакет, поел ЦЕЛИЯ ред, значи, че разделянето не
#: е по трасе, а по ред от КСС.
_SPLITTABLE_MIN_QUANTITY = 300.0

#: Мерките, по които редът е „дълъг" — тръби, изкопи, настилки.
_LINEAR_UNITS = ("м", "m", "м2", "m2", "м3", "m3", "мл", "ml")


def _is_linear_row(row: Any) -> bool:
    unit = str(getattr(row, "unit", "") or "").strip().lower().replace("'", "")
    unit = unit.replace("²", "2").replace("³", "3").rstrip(".")
    return unit in _LINEAR_UNITS



#: Веригите, за които гравитацията решава реда.  Водопроводът е под налягане —
#: там „надолу" не значи нищо за реда на изпълнение.
_ГРАВИТАЦИОННИ = frozenset({"sewer_section"})


def _пакетен_dn(pkg: SpatialWorkPackage) -> int:
    """Диаметърът на пакета — от самия пакет или от най-едрия му ред."""
    if isinstance(pkg.dn, int) and pkg.dn > 0:
        return pkg.dn
    диаметри = [i.dn for i in pkg.items if isinstance(i.dn, int) and i.dn > 0]
    return max(диаметри) if диаметри else 0


def order_sewer_by_flow(
    packages: Sequence[SpatialWorkPackage],
) -> tuple[list[SpatialWorkPackage], list[str]]:
    """Каналът тръгва от заустването, и едрите тръби вървят преди дребните.

    ОСНОВНО ЗНАНИЕ, не настройка по проект (изпълнителят, 24.08.2026):
    „каналът трябва да тръгне от заустването и да се правят първо най-големите
    размери тръби, след това по-малките".  Причината е гравитацията — долният
    участък приема водата на горния, изкопът му е най-дълбок, а отводняването
    по време на строителството върви надолу.  Затова главните колектори са
    първи, а второстепенните клонове след тях.

    ИЗРАЗЕНО КАТО ПРИОРИТЕТ, НЕ КАТО ЗАВИСИМОСТ (решение на изпълнителя,
    24.08.2026).  Твърда връзка „горният чака долния" би сериализирала обекта
    и би излъгала: клон, който още не е свързан, спокойно се копае успоредно.
    Истинското ограничение е раздаването на екипи и машини — при две готови
    задачи първа трябва да се обслужи по-долната и по-едрата.  Точно този вход
    уважава `ScheduleBuilder._topological_order`: при равни други условия
    печели онзи, който е по-напред в списъка.

    ДВА РЕЖИМА, защото документите не винаги казват едно и също:

      * когато е известна свързаността между шахтите (оразмерителна таблица с
        начална и крайна шахта), ЗАУСТВАНЕТО е възелът, който е само КРАЙ и
        никога начало — всеки участък получава разстоянието си до него в брой
        шахти и се нарежда по него;
      * когато свързаност няма — ситуационният чертеж днес дава клон, диаметър
        и дължина, но НЕ и възли — остава диаметърът, низходящо.

    Не пипа реда на другите вериги: те си стоят по местата, а канализационните
    пакети се пренареждат само помежду си.

    Returns:
        (пакети в реда на изпълнение, бележки).
    """
    пакети = list(packages)
    места = [i for i, p in enumerate(пакети) if p.chain in _ГРАВИТАЦИОННИ]
    if len(места) < 2:
        return пакети, []

    канал = [пакети[i] for i in места]

    # Свързаност: от началния към крайния възел, по посока на течението.
    ребра = [(_normalize_node(p.start_node), _normalize_node(p.end_node))
             for p in канал]
    начала = {a for a, b in ребра if a and b}
    краища = {b for a, b in ребра if a and b}
    заустване = sorted(краища - начала)

    дълбочина: dict[str, int] = {}
    if начала and заустване:
        нагоре: dict[str, list[str]] = defaultdict(list)
        for a, b in ребра:
            if a and b:
                нагоре[b].append(a)
        опашка = [(възел, 0) for възел in заустване]
        for възел, _ in опашка:
            дълбочина[възел] = 0
        while опашка:
            възел, d = опашка.pop(0)
            for предходен in нагоре.get(възел, []):
                if предходен not in дълбочина:
                    дълбочина[предходен] = d + 1
                    опашка.append((предходен, d + 1))

    #: Участък, чийто възел не се връзва с графа, отива НАКРАЯ на канала, а не
    #: на случайно място: непознатото не бива да изпревари доказаното.
    ДАЛЕЧ = 10 ** 6

    def ключ(pkg: SpatialWorkPackage) -> tuple:
        край = _normalize_node(pkg.end_node)
        разстояние = дълбочина.get(край, ДАЛЕЧ) if дълбочина else 0
        return (разстояние, -_пакетен_dn(pkg), str(pkg.id))

    подредени = sorted(канал, key=ключ)
    for място, pkg in zip(места, подредени):
        пакети[място] = pkg

    известни = sum(1 for p in канал
                   if _normalize_node(p.end_node) in дълбочина)
    if дълбочина and известни:
        бележка = (
            f"Ред на канала: от заустването ({', '.join(заустване[:2])}) нагоре"
            f" — {известни} от {len(канал)} участъка подредени по разстояние до"
            " него; при равно разстояние по-едрият диаметър е пръв.")
    else:
        бележка = (
            f"Ред на канала: по диаметър низходящо ({len(канал)} участъка) — "
            "документите не дават свързаност между шахтите, затова заустването "
            "не може да се докаже и посоката се приема по размера на тръбата.")
    logger.info("%s", бележка)
    return пакети, [бележка]


def number_execution_batches(
    packages: Sequence[SpatialWorkPackage],
) -> list[SpatialWorkPackage]:
    """Прави имената на непотвърдените пакети различими — честно.

    ЗАЩО.  Щом възлите не могат да бъдат твърдени, имената се свиват до клона:
    „кл. 48 от РШ 36 до РШ 37" и „кл. 48 от РШ 37 до РШ 38" стават едно и също
    „кл. 48".  А `partition_diagnosis` с право брои еднаквите имена за признак
    на изродено разделяне: „два пакета с едно име не са два различни участъка
    за никого, който чете графика".

    Отговорът НЕ е да върнем съчинените възли, а да различим пакетите по
    това, което НАИСТИНА ги дели, в този ред:

      1. МРЕЖАТА — „кл. 1" на водопровода и „кл. 1" на канализацията са два
         различни клона с еднакъв номер; това се случва на всеки обект;
      2. РЕДЪТ НА ИЗПЪЛНЕНИЕ — когато и мрежата не ги дели, значи са етапи по
         един и същи клон: „кл. 48 — етап 1 от 2".

    Нито едното не твърди геометрия.  Точно това е `ExecutionBatch` на
    одитора, отделен от `PhysicalSegment`.

    Пакетите с потвърдена геометрия НЕ се пипат: те са трасета и се именуват
    като такива.  Пресмята се от нулата при всяко викане, за да може да се
    приложи пак след по-късно разделяне.
    """
    from src.spatial_source import strip_node_claim

    основи: dict[int, str] = {}
    по_основа: dict[str, list[int]] = {}
    for индекс, pkg in enumerate(packages):
        if pkg.spatial_verified:
            continue
        основа = (strip_node_claim(pkg.name or "")
                  or strip_node_claim(pkg.branch or pkg.street or "")
                  or f"Участък {pkg.id}")
        основи[индекс] = основа
        по_основа.setdefault(основа.lower(), []).append(индекс)

    имена: dict[int, str] = {}
    for индекси in по_основа.values():
        if len(индекси) == 1:
            имена[индекси[0]] = основи[индекси[0]]
            continue
        # 1. дели ли ги мрежата
        по_мрежа: dict[str, list[int]] = {}
        for индекс in индекси:
            по_мрежа.setdefault(packages[индекс].network, []).append(индекс)
        много_мрежи = len(по_мрежа) > 1
        for мрежа, група in по_мрежа.items():
            етикет = _NETWORK_LABEL.get(мрежа, мрежа)
            for пореден, индекс in enumerate(група, 1):
                име = основи[индекс]
                if много_мрежи:
                    име = f"{име} ({етикет})"
                # 2. и ако пак не ги дели — редът на изпълнение
                if len(група) > 1:
                    име = f"{име} — етап {пореден} от {len(група)}"
                имена[индекс] = име

    return [
        replace(pkg, batch_label=имена[i]) if i in имена else pkg
        for i, pkg in enumerate(packages)
    ]


def partition_diagnosis(
    packages: Iterable[SpatialWorkPackage],
    boq_index: Iterable[Any],
    segments: Iterable[dict] | None = None,
) -> dict[str, Any]:
    """Дали обектът е разделен на ТРАСЕТА, или само прегрупиран по редове.

    ЖИВ ПРОГОН 14.08.2026: четири последователни опита дадоха 36, 28, 11 и 33
    участъка за един и същ обект.  Броят сам по себе си не е дефект — обектът
    си е един и същ, но моделът всеки път решава наново колко едро да го
    нареже.  11 е познатото израждане: по един пакет на ДИАМЕТЪР, тоест
    старото групиране, опаковано като „участъци".

    Затова тук се проверява не броят, а СВОЙСТВОТО: участък е трасе между два
    възела, а не купчина от цял ред на КСС.  Три детерминистични признака:

      1. цели редове в един пакет — голям линеен ред (тръби, изкоп, настилка),
         поет 100% от ЕДИН пакет, при положение че мрежата има няколко пакета;
      2. по-малко пакети от отсечките, прочетени от чертежа — чертежът е
         долната граница на това, което СЪЩЕСТВУВА на обекта;
      3. еднакви имена — два пакета с едно име не са два различни участъка за
         никого, който чете графика.

    Присъдата е СЪВЕТ, не гейт: при съмнение се пита още веднъж, а не се
    отказва график.  Затова и прагът е „повечето", не „поне един".

    Returns:
        {ok, signals, packages, linear_packages, drawn_segments,
         undivided_rows, prompt_note} — `prompt_note` е готов текст за
        следващото питане към модела (празен, когато всичко е наред).
    """
    пакети = list(packages)
    сигнали: list[str] = []

    линейни = [p for p in пакети if p.network in _LINEAR_NETWORKS]
    отсечки = {
        (_normalize_node(s.get("start_node")), _normalize_node(s.get("end_node")))
        for s in (segments or [])
        if isinstance(s, dict) and s.get("start_node") and s.get("end_node")
    }

    # 1. Цели редове в един пакет.
    носители: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pkg in пакети:
        for item in pkg.items:
            носители[str(item.source_ref)].append((pkg.id, float(item.quantity)))

    делими = 0
    неразделени: list[str] = []
    for row in boq_index:
        ref = str(getattr(row, "ref", "") or "")
        want = getattr(row, "quantity", None)
        if not ref or not isinstance(want, (int, float)) or isinstance(want, bool):
            continue
        if not _is_linear_row(row) or abs(want) < _SPLITTABLE_MIN_QUANTITY:
            continue
        части = носители.get(ref) or []
        if not части:
            continue
        делими += 1
        най_голямата = max(q for _, q in части)
        if len(части) == 1 or най_голямата >= abs(want) * 0.95:
            неразделени.append(ref)

    if делими and len(неразделени) > делими / 2 and len(пакети) > 1:
        сигнали.append(
            f"{len(неразделени)} от {делими} големи линейни реда стоят цели в "
            "един участък — разделянето е по редове/диаметри, не по трасета")

    # 2. По-малко пакети от прочетените отсечки.
    if отсечки and len(линейни) < len(отсечки):
        сигнали.append(
            f"{len(линейни)} мрежови участъка при {len(отсечки)} отсечки, "
            "прочетени от чертежа — участъци от обекта липсват")

    # 3. Еднакви имена.
    имена = [p.label.strip().lower() for p in пакети]
    повторени = len(имена) - len(set(имена))
    if повторени:
        сигнали.append(f"{повторени} участъка носят вече заето име — "
                       "в графика не се различават")

    бележка = ""
    if сигнали:
        бележка = (
            "ПРЕДИШНОТО РАЗДЕЛЯНЕ НЕ Е ГОДНО:\n"
            + "\n".join(f"  • {s}" for s in сигнали)
            + "\nУчастък е ТРАСЕ МЕЖДУ ДВА ВЪЗЕЛА, не купчина от цял ред на КСС.\n"
              "Раздели количествата на големите редове между участъците, през\n"
              "които минава трасето.")

    return {
        "ok": not сигнали,
        "signals": сигнали,
        "packages": len(пакети),
        "linear_packages": len(линейни),
        "drawn_segments": len(отсечки),
        "splittable_rows": делими,
        "undivided_rows": sorted(неразделени),
        "prompt_note": бележка,
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
        # Обединената непрекъсната дейност цитира НЯКОЛКО реда (19.08.2026).
        # Ако тук се четеше само `source_ref`, трите пътни реда щяха да стоят в
        # описа без нито една задача срещу тях — количеството разпределено,
        # работата невидима.  Виж `road_works`.
        цитати = task.get("source_refs")
        refs = ([str(c.get("ref") or "").strip()
                 for c in цитати if isinstance(c, dict)]
                if isinstance(цитати, list) and цитати
                else [str(task.get("source_ref") or "").strip()])
        for ref in refs:
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


def format_resolutions(resolutions: list[dict]) -> str:
    """Приложените човешки решения — като част от описа.

    Без този раздел графикът показва диаметър от описанието в КСС, смята с
    друг (решения), и никъде не казва защо.  Одиторът прочете това като
    мълчалив избор — и беше прав, че отвън не се различава.
    """
    if not resolutions:
        return ""
    lines = ["", "## Приложени решения по противоречия в КСС", "",
             "Тези редове на КСС си противоречат сами. Програмата НЕ избира —",
             "стойността идва от човешко решение, записано с автор и дата.", "",
             "| Ред | Поле | Кандидати | Прието | Решил | Дата |",
             "|---|---|---|---|---|---|"]
    for r in resolutions:
        кандидати = " / ".join(str(c) for c in r.get("candidates") or []) or "—"
        lines.append(
            f"| {r.get('conflict_ref') or r.get('record_id')} | {r.get('field')} "
            f"| {кандидати} | **{r.get('chosen_value')}** "
            f"| {r.get('decided_by') or '—'} | {r.get('resolved_at') or '—'} |")
    забележки = [r.get("note") for r in resolutions if r.get("note")]
    if забележки:
        lines += ["", "Мотиви: " + "; ".join(забележки)]
    lines += ["", "**Имената на задачите носят описанието от КСС, а сметките — "
              "приетата стойност.** Където двете се разминават, вярна е "
              "приетата стойност от таблицата по-горе."]
    return "\n".join(lines)


def format_allocation_ledger(ledger: list[dict], resolutions: list[dict] | None = None) -> str:
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
    раздел = format_resolutions(resolutions or [])
    if раздел:
        lines.append(раздел)
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
    packages: list[SpatialWorkPackage],
    num_fronts: int | dict[str, int],
    *,
    темпа: dict[str, float] | None = None,
    chains: dict[str, Any] | None = None,
) -> list[SpatialWorkPackage]:
    """Разпредели пакетите между фронтовете, БЕЗ да дублира количества.

    Тук е поправката на коренния дефект.  Досега „2 фронта" означаваше две
    копия на едни и същи позиции; сега означава, че ПАКЕТИТЕ се делят на две
    групи.  Всяко количество остава в точно един пакет, тоест сборът не може
    да се промени — запазването е структурно, не проверявано после.

    Балансът е по обем работа (сбор от количествата), не по брой пакети:
    greedy „най-натоварен последен" върху сортирани по големина пакети.
    """
    # БРОЯТ МОЖЕ ДА Е ПО ВЕРИГА (19.08.2026).  Срокът е зададен от процедурата,
    # затова екипите се ИЗЧИСЛЯВАТ от него (`crew_sizing`) и се различават:
    # канализацията иска два, водопроводът при сондаж — един.  Едно общо число
    # за всички вериги значи или излишни екипи, или задавен водопровод.
    по_верига = num_fronts if isinstance(num_fronts, dict) else {}
    подразбиране = 1 if по_верига else max(int(num_fronts), 1)

    def колко(pkg: SpatialWorkPackage) -> int:
        return max(int(по_верига.get(pkg.chain, подразбиране)), 1)

    if not по_верига and подразбиране == 1:
        return [_with_front(p, _име_на_екип(p.network, 1)) for p in packages]

    def _количество(pkg: SpatialWorkPackage) -> float:
        return sum(abs(float(i.quantity)) for i in pkg.items) or 1.0

    #: Колко ДНИ ще вземе този участък — по същата сметка, по която после ще му
    #: бъдат сметнати продължителностите (`calibrate_to_declared_pace`).
    #
    # ЗАЩО НЕ ПО МЕТРИ (26.08.2026).  Балансът по метри дава на всеки екип
    # еднакви метри, но не еднакви ДНИ: клон от 30 м пак иска девет стъпки по
    # поне един ден.  Мерено на Тръстеник — осем екипа с равни метри излязоха с
    # 81 до 276 дни работа.  Тук се брои това, което наистина заема екипа.
    _стъпки_на_верига: dict[str, int] = {}
    if chains:
        for ключ, верига in (chains.get("chains") or {}).items():
            _стъпки_на_верига[ключ] = len(верига.get("steps") or [])

    def _дни(pkg: SpatialWorkPackage) -> float:
        под = float(_стъпки_на_верига.get(pkg.chain, 1) or 1)
        темпо = float((темпа or {}).get(pkg.chain, 0.0) or 0.0)
        метри = sum(abs(float(i.quantity)) for i in pkg.items
                    if str(getattr(i, "activity_class", "")) == "laying"
                    and str(getattr(i, "unit", "")).strip().lower()
                    in ("m", "м", "мл", "ml"))
        if темпо > 0 and метри > 0:
            return max(под, метри / темпо)
        return под

    #: Колко „метра" струват задължителните стъпки на един участък.
    #
    # ЗАЩО НЕ САМО КОЛИЧЕСТВОТО (25.08.2026).  Балансът по метри дава на всеки
    # екип еднакви метри, но НЕ еднакви дни: клон от 30 м пак иска девет
    # стъпки по поне един ден.  Мерено на Тръстеник — четири водопроводни екипа
    # с равни метри излязоха с 374, 368, 320 и 178 дни работа, тоест единият
    # стоеше два пъти по-малко от другия.
    #
    # Затова цената на участъка е ПРОМЕНЛИВА (метрите) плюс ФИКСИРАНА
    # (задължителните стъпки).  Фиксираната се взима от самите данни — медианата
    # на веригата — вместо да се пише наизуст число.
    def _фиксирана_цена(група: list[SpatialWorkPackage]) -> float:
        стойности = sorted(_количество(p) for p in група)
        if not стойности:
            return 0.0
        среда = len(стойности) // 2
        return (стойности[среда] if len(стойности) % 2
                else (стойности[среда - 1] + стойности[среда]) / 2)

    # ЕКИПИТЕ СА ПО ДИСЦИПЛИНА, не общи (19.08.2026).
    #
    # Еталонът ги изброява поименно: ЕК1 и ЕК2 правят САМО канализация, ЕВ1 и
    # ЕВ2 само водопровод, ЕВН настилките.  Дотук фронтовете носеха общи имена
    # („Фронт 1"), тоест един и същ екип получаваше и канализационни, и
    # водопроводни пакети — и в режима „екип на участък" водопроводът чакаше
    # зад канализацията на своя фронт.
    #
    # Сметката, която го извади наяве: 3247 м водопровод за 544 дни е 6 м/ден,
    # а еталонът кара по 17.  Не защото полага по-бавно — а защото при нас
    # водният екип стои, докато същият фронт довърши канала.
    #
    # Мрежите се балансират поотделно и вече имат СВОИ фронтове.
    out: list[SpatialWorkPackage] = []
    for верига in sorted({p.chain for p in packages}):
        от_веригата = [p for p in packages if p.chain == верига]
        фиксирана = _фиксирана_цена(от_веригата)
        # Когато темпото е известно, теглото е в ДНИ — това е истинската мярка
        # за заетостта на екипа.  Без темпо се пада към метри плюс фиксирана
        # цена на участък, което е приблизително същото подреждане.
        по_дни = bool(темпа) and bool(_стъпки_на_верига)

        def weight(pkg: SpatialWorkPackage, _ф: float = фиксирана) -> float:
            if по_дни:
                return _дни(pkg)
            return _количество(pkg) + _ф

        група = sorted(от_веригата, key=lambda p: (-weight(p), p.id))
        n = колко(група[0])
        load = [0.0] * n
        buckets: list[list[SpatialWorkPackage]] = [[] for _ in range(n)]
        for pkg in група:
            idx = min(range(n), key=lambda i: (load[i], i))
            buckets[idx].append(pkg)
            load[idx] += weight(pkg)
        # МРЕЖАТА Е НА ПАКЕТА, НЕ НА ГРУПАТА (25.08.2026).  Веригата
        # `structure` събира съоръженията на ДВЕТЕ мрежи; името се взимаше от
        # първия пакет в групата и канализационната шахта излизаше с „Екип
        # В1" — воден екип на канализационна работа, при това докато същият
        # В1 кара своя клон.
        for i, bucket in enumerate(buckets, 1):
            out.extend(_with_front(p, _име_на_екип(p.network, i)) for p in bucket)
    return sorted(out, key=lambda p: p.id)


def _име_на_екип(мрежа: str, номер: int) -> str:
    """ЕВ1, ЕВ2, ЕК1, ЕК2 — както изпълнителят ги нарича (25.08.2026).

    Същите имена стоят и в човешкия еталон: ЕК1 и ЕК2 правят само канализация,
    ЕВ1 и ЕВ2 само водопровод.  Дотук пишеше „Екип В1" — същото нещо с други
    букви, но графикът отива при хора, които четат своите имена.
    """
    return f"Е{str(мрежа or '').strip()}{номер}"


def _with_front(pkg: SpatialWorkPackage, front: str) -> SpatialWorkPackage:
    if pkg.front == front:
        return pkg
    return replace(pkg, front=front)


# ---------------------------------------------------------------------------
# Зона за възстановяване: настилките са МЯСТО, не ред от КСС
# ---------------------------------------------------------------------------


#: Веригите, чиито пакети описват ВЪЗСТАНОВЯВАНЕ на терена, а не полагане.
_RESTORATION_CHAIN = "pavement_section"


def _split_shared_rows(
    група: list[SpatialWorkPackage],
) -> list[list[SpatialWorkPackage]]:
    """Раздели зоната на толкова места, колкото КСС редовете доказват.

    Един КСС ред, който се среща в N пакета, значи че количеството му е
    разделено между N МЕСТА — не че едно място е описано N пъти.  Затова
    броят места е най-голямата такава честота, а пакетите се разпределят по
    места така, че в едно място никой ред да не се повтаря.

    Несъвпадащи редове (класическият дефект: пакет „асфалт", пакет „бордюри",
    пакет „унипаваж") дават честота 1 → едно място → сливат се, както досега.
    """
    if len(група) < 2:
        return [група]

    места: list[list[SpatialWorkPackage]] = []
    редове: list[set[str]] = []

    # Подредбата държи пакетите с един етикет заедно: тогава първият свободен
    # почерк ги разпределя по места, вместо да ги смесва през улиците.
    for pkg in sorted(група, key=lambda p: ((p.street or p.branch or "").lower(),
                                            str(p.id))):
        мои = {item.source_ref for item in pkg.items}
        for място, заети in zip(места, редове):
            if not (мои & заети):
                място.append(pkg)
                заети |= мои
                break
        else:
            места.append([pkg])
            редове.append(set(мои))

    return места


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

    ИЗМЕРЕНО 17.08.2026: „една зона за целия обект" струва 111 дни (767 срещу
    656 на детерминистичния прогон).  Механиката: слятата зона ражда един
    „основен пласт" с 16 предшественика — цялата тръбна работа по всичките
    участъци.  Тоест нищо не се възстановява, докато последният участък не е
    засипан, а на обекта не е така.

    Затова разграничението вече не е по ЕТИКЕТ, а по доказателство в самите
    данни.  Дефектът, който сливането пази, изглежда така: моделът връща по
    един пакет на КСС РЕД, тоест пакетите са РАЗЛИЧНИ редове — „асфалт",
    „бордюри", „унипаваж" — и всеки минава цялата задължителна верига.
    Истинските участъци изглеждат обратно: всеки носи ДЯЛ от същите редове.

    Проверимо е: пакети с несъвпадащи редове са един и същ обект, разцепен по
    стъпки → сливат се.  Пакети, които делят едни и същи редове помежду си, са
    различни МЕСТА → остават отделни.  Така обектът пак се асфалтира веднъж, а
    възстановяването не чака последния изкоп.

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
        # авторитетна геометрия зоната е една, и чак разделянето по-долу я
        # връща на участъци — но по споделени редове, не по етикет.
        key = ((pkg.street or pkg.branch or "").strip().lower()
               if spatial_authoritative else "")
        zones.setdefault(key, []).append(pkg)

    zones = {f"{ключ}#{номер}": част
             for ключ, група in zones.items()
             for номер, част in enumerate(_split_shared_rows(група))}

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

    # РАЗПЪВАНАТА ЗАДАЧА НЕ МЕРИ САМА СЕБЕ СИ (26.08.2026).  Задълженията по
    # договора също са `spans_construction`; ако влязат в мерилото, началото
    # на строителството става тяхното начало (ден 1) и цялата фаза се дърпа
    # преди проектирането — мерено, 12 грешки във валидацията.
    building = [t for t in out
                if t.get("wbs_root") == "construction"
                and not t.get("is_summary")
                and not t.get("spans_construction")
                and t.get("start_day") is not None]
    if not building:
        return out, ["няма строителни задачи — надзорът остава както е"]

    start = min(int(t["start_day"]) for t in building)
    finish = max(int(t.get("end_day") or t["start_day"]) for t in building)

    # ОТ КРАЯ НА ПРОЕКТИРАНЕТО ДО КРАЯ НА ОБЕКТА (19.08.2026).
    #
    # Дотук надзорът се разтягаше само по СТРОИТЕЛСТВОТО.  Човешкият еталон го
    # води иначе и клиентът го потвърди: проектирането свършва ден 120,
    # надзорът върви 121 → 780, а 780 е краят на целия обект.  Тоест той тръгва
    # веднага щом има проект, за който да се отговаря, и не свършва преди
    # обектът да е приет.
    #
    # При нас това са две отделни разминавания: тръгваше чак с първата
    # строителна задача (след мобилизацията, ден 142 вместо 125) и свършваше с
    # последната строителна, оставяйки приемането без надзор (712 срещу 743).
    проектиране = [t for t in out
                   if t.get("wbs_root") == "design"
                   and not t.get("is_summary")
                   and t.get("end_day") is not None]
    if проектиране:
        start = min(start, max(int(t["end_day"]) for t in проектиране) + 1)

    # Краят се мери по задачите, които НЕ зависят от разпъваната.  Иначе
    # излиза ратчет: надзорът се разтяга до последната задача → финалният
    # milestone, който го следва, се мести → надзорът се разтяга пак.  Веднъж
    # вече го получих така, +41 дни на всяко превъртане (19.08.2026).
    разпъвани = {str(t.get("id")) for t in spanning}
    зависими = {str(t.get("id")) for t in out
                if any(str(_dep_id(d)) in разпъвани
                       for d in (t.get("dependencies") or []))}
    останалите = [t for t in out
                  if not t.get("spans_construction")
                  and not t.get("is_summary")
                  and str(t.get("id")) not in зависими
                  and t.get("end_day") is not None]
    if останалите:
        finish = max(finish, max(int(t["end_day"]) for t in останалите))

    notes: list[str] = []
    for task in spanning:
        was = (task.get("start_day"), task.get("end_day"))
        task["start_day"] = start
        task["end_day"] = finish
        task["duration"] = finish - start + 1
        task["duration_source"] = "construction_span"
        notes.append(
            f"{task.get('name', task.get('id'))}: разтеглена от края на "
            f"проектирането до края на обекта ({start}–{finish} вместо "
            f"{was[0]}–{was[1]})")
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


#: Мрежа → как се казва нейният дял от строителството.  Взето от графика на
#: изпълнителя (25.08.2026): „Изпълнение на водопроводната мрежа" и под него
#: „Водопроводен екип 1", а чак после клоновете.
_ДЯЛ_НА_МРЕЖАТА = {
    "В": "Изпълнение на водопроводната мрежа",
    "К": "Изпълнение на канализационната мрежа",
    "П": "Възстановяване на пътни и тротоарни настилки",
    "ЕЛ": "Кабелни и електро работи",
}

#: Мрежа → как се чете екипът ѝ с думи.  „ЕВ1" е кратко за колоната „ЕКИП";
#: за заглавен ред на цял дял от графика човекът пише пълното име.
_ЕКИП_С_ДУМИ = {"В": "Водопроводен екип", "К": "Канализационен екип",
                "П": "Пътен екип", "ЕЛ": "Електро екип"}


def _име_на_дял(мрежа: str) -> str:
    return _ДЯЛ_НА_МРЕЖАТА.get(str(мрежа or "").strip(), "Изпълнение на мрежата")


def _екип_с_думи(front: str, мрежа: str) -> str:
    """„ЕВ1" → „Водопроводен екип 1"; непознато име остава каквото е."""
    текст = str(front or "").strip()
    водещо = _ЕКИП_С_ДУМИ.get(str(мрежа or "").strip())
    номер = "".join(з for з in текст if з.isdigit())
    if водещо and номер:
        return f"{водещо} {номер}"
    return текст or (водещо or "Екип")


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

    # ПО КЛОНОВЕ, ПОД СВОЯ ЕКИП (изпълнителят, 25.08.2026).  В строителството
    # между корена и участъка стоят още два заглавни реда — дялът на мрежата и
    # екипът, който я кара — точно както в графика, който той даде за еталон.
    # Създават се при първия пакет, който стъпва в тях: празен заглавен ред е
    # по-лош от липсващ.
    групи: dict[tuple[str, str], str] = {}

    def група_за(pkg: SpatialWorkPackage, chain_def: dict, root_id: str) -> str:
        if str(chain_def.get("wbs_root", "construction")) != "construction":
            return root_id
        мрежа = str(pkg.network or "").strip()
        фронт = str(pkg.front or "").strip()
        родител = root_id
        нива: list[tuple[tuple[str, str], str]] = []
        if мрежа:
            нива.append((("мрежа", мрежа), _име_на_дял(мрежа)))
            if фронт:
                нива.append((("екип", f"{мрежа}|{фронт}"),
                             _екип_с_думи(фронт, мрежа)))
        for ключ, име in нива:
            ид = групи.get(ключ)
            if ид is None:
                ид = f"WBS_{ключ[0]}_{len(групи) + 1}"
                групи[ключ] = ид
                result.tasks.append({
                    "id": ид, "name": име, "type": "summary", "duration": 0,
                    "parent_id": родител, "dependencies": [],
                    "is_summary": True, "network": мрежа,
                    "wbs_root": "construction",
                })
            родител = ид
        return родител

    for pkg in packages:
        chain = chain_defs.get(pkg.chain)
        if chain:
            chain = _attachment_scope(pkg, chain)
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
            "parent_id": група_за(pkg, chain, root_for(chain)),
            "duration": 0, "dependencies": [],
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

        for step in effective_chain_steps(pkg, chain):
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
            # ЗАДЪЛЖЕНИЕ ЗА ЦЕЛИЯ СРОК НЯМА ПРЕДШЕСТВЕНИК (26.08.2026).
            # Месечните доклади не чакат документите на изпълнителя — и двете
            # вървят от първия до последния ден.  Навържат ли се FS, всяко
            # следващо трябва да започне след края на предишното, тоест след
            # края на обекта: 1319 дни и невалиден график.
            if step.get("spans_construction"):
                link_to = []
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

    # ЗАДЪЛЖЕНИЯТА ПО ДОГОВОРА вървят през целия строителен срок и ги има при
    # ВСЯКА поръчка — не само при инженеринг.  Взети са от 24-те изпълнени
    # графика на изпълнителя (26.08.2026): документи на изпълнителя, месечни
    # доклади, актове по Наредба № 3, опазване на околната среда, доставка на
    # материали, лабораторни изпитвания.  Всяко от тях стои в 12 от 24-те.
    keys = ["mobilization", "site_duties", "acceptance"]
    if with_design:
        keys = ["design", "mobilization", "site_duties", "supervision",
                "acceptance"]

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

    # `water_section_hdd` ВЛИЗА (поправено 19.08.2026).  Липсваше, и когато
    # търгът обяви сондаж, целият водопровод оставаше извън този списък: не
    # получаваше портата „след откриване на площадката" и тръгваше в ден 1 —
    # преди проектирането.  Надзорът, който се котви за най-ранната строителна
    # задача, го следваше и графикът падаше от собствената си валидация.
    #
    # Веригата съществува от 10.08.2026, но дотогава никой търг не я беше
    # избирал, затова пропускът не се виждаше.
    spatial = {"sewer_section", "water_section", "water_section_hdd",
               "pavement_section", "cable_section", "structure"}
    # ЗАДЪЛЖЕНИЯТА ПО ДОГОВОРА НЕ СА РАБОТА, КОЯТО ПРИЕМАНЕТО ЧАКА
    # (26.08.2026).  Месечните доклади свършват с последния ден на обекта; ако
    # приемането се върже за тях, то не може да започне никога.  Те се
    # разпъват по строителството, а не го определят.
    build = [t for t in out
             if pkg_chain.get(str(t.get("parent_id") or "")) in spatial
             and not t.get("is_summary")
             and not t.get("spans_construction")]

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
    #
    # Котвата е НАЙ-РАННО започващата и НАЙ-КЪСНО свършващата задача, а не
    # първата и последната ПО РЕД В СПИСЪКА.  Измерено 17.08.2026: при подредба,
    # в която първа се пада пътна задача, надзорът получаваше SS връзка към
    # нещо, което тръгва на ден 436, а `enforce_construction_span` после го
    # слагаше на ден 142 — началото на строителството.  Графикът падаше от
    # собствената си валидация с „[SS] започва ден 142, но предшественик
    # започва ден 436".
    #
    # Редът в списъка не значи нищо за обекта; датите значат.  Когато ги няма
    # още (връзките се слагат преди смятането), се пада към позиционното —
    # по-добре приблизителна котва, отколкото никаква.
    sup = tasks_of("supervision")
    if sup and build:
        def _ден(task: dict, поле: str, по_подразбиране: int) -> int:
            стойност = task.get(поле)
            return (int(стойност)
                    if isinstance(стойност, (int, float))
                    and not isinstance(стойност, bool)
                    else по_подразбиране)

        # НАЧАЛОТО Е КРАЯТ НА ПРОЕКТИРАНЕТО, НЕ ПЪРВАТА СТРОИТЕЛНА ЗАДАЧА
        # (19.08.2026).  Човешкият еталон: проектирането свършва ден 120,
        # надзорът върви 121 → 780.  Той тръгва щом има проект, за който да се
        # отговаря — не чака мобилизацията да свърши.  При нас това бяха
        # седемнайсет изгубени дни (125 срещу 142).
        if design:
            add(str(design[-1]["id"]), str(sup[0]["id"]), "FS", 0,
                "supervision_starts_after_design")
        else:
            най_рано = min(build, key=lambda t: _ден(t, "start_day", 10**9))
            add(str(най_рано["id"]), str(sup[0]["id"]), "SS", 0,
                "supervision_spans")

        # КРАЯТ Е КРАЯТ НА ОБЕКТА, НЕ НА СТРОИТЕЛСТВОТО.  Надзорът покрива и
        # приемането: в еталона той свършва ден 780, колкото е целият обект.
        #
        # Котвата е последната РАБОТНА задача от приемането, не финалният
        # milestone: иначе надзорът остава без наследник и графикът излиза с
        # ДВА края, а той трябва да се затваря на един.  Затова после самият
        # надзор води към milestone-а.
        приемане_всички = tasks_of("acceptance")
        работни = [t for t in приемане_всички
                   if not (t.get("milestone") or t.get("is_milestone"))]
        последни = работни or build
        най_късно = max(последни, key=lambda t: _ден(t, "end_day", 0))
        add(str(най_късно["id"]), str(sup[0]["id"]), "FF", 0,
            "supervision_until_handover")
        финал = [t for t in приемане_всички
                 if t.get("milestone") or t.get("is_milestone")]
        if финал:
            # FF, не FS: приемането става в СЪЩИЯ ден, в който свършва
            # надзорът, не на другия.  С FS графикът падаше от собствената си
            # валидация — „започва ден 786, но предшественик завършва 786".
            add(str(sup[0]["id"]), str(финал[-1]["id"]), "FF", 0,
                "supervision_closes_on_handover")

    # --- ВСИЧКИ висящи краища → приемането ---
    acceptance = tasks_of("acceptance")
    if acceptance:
        first_acceptance = str(acceptance[0]["id"])
        acceptance_ids = {str(t["id"]) for t in acceptance}
        successors = {
            _dep_id(d) for t in out for d in (t.get("dependencies") or [])
        }
        # НАДЗОРЪТ НЕ ВЛИЗА (поправено 19.08.2026).  Дотук той се броеше за
        # висящ край и се връзваше ПРЕДИ приемането — а той го покрива, не го
        # предхожда.  Двете правила заедно даваха кръг: разтегли надзора до
        # края на обекта → приемането се мести след него → краят на обекта се
        # мести → надзорът пак се разтяга.  Сега краят му е FF към последната
        # задача от приемането и той не е ничий предшественик.
        sup_ids = {str(t["id"]) for t in tasks_of("supervision")}
        # СЪЩОТО ВАЖИ ЗА ЗАДЪЛЖЕНИЯТА ПО ДОГОВОРА (26.08.2026).  Те не са
        # висящ край, а покривало: месечните доклади и актовете по Наредба № 3
        # свършват с последния ден на обекта.  Вържат ли се ПРЕДИ приемането,
        # то не може да започне никога и графикът пада от валидацията.
        loose = [t for t in out
                 if not t.get("is_summary")
                 and not t.get("spans_construction")
                 and str(t["id"]) not in successors
                 and str(t["id"]) not in acceptance_ids
                 and str(t["id"]) not in sup_ids]
        added = sum(add(str(t["id"]), first_acceptance, why="all_work_before_handover")
                    for t in loose)
        if added:
            notes.append(f"{added} висящи края вързани към приемането")

    return out, notes


def enforce_network_order(
    tasks: list[dict],
    packages: Sequence[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Втората мрежа не тръгва преди първата — отговорът на въпросника, като връзка.

    ЗАЩО ОТДЕЛЕН ПРОХОД (19.08.2026).  „Кое е първо, В или К" се пита от
    приложението, но дотогава отговорът стигаше само до промпта на модела: на
    детерминистичния път — който е по подразбиране — той нямаше НИКАКЪВ ефект.

    ЗАЩО СЛЕД РАЗПИСВАНЕТО.  Котвата трябва да е НАЙ-РАННАТА задача на първата
    мрежа, а коя е тя се знае чак когато има дати.  Опитът да се избере по ред
    в списъка хвана „Етап 4 от 8" за първи и залепи целия канал за него.

    КОЛКО СИЛНО.  Не е ред участък по участък: в еталона от 34 участъка с двете
    мрежи в 15 водата тръгва първа, в 15 ЗАЕДНО и в 4 каналът е пръв.  Затова
    твърдението е под — нито един участък на втората мрежа не започва преди
    първата, — а не последователност.  Връзките са SS с нула дни: редът се
    вижда, чакане няма.

    ВРЪЗКА СЕ СЛАГА САМО КЪДЕТО ПРАВИЛОТО НЕ Е СПАЗЕНО (измерено).  Първата
    версия връзваше и 29-те канализационни участъка към котвата, включително
    онези, които и без това тръгват след нея.  Излишните връзки не менят нищо
    по смисъл, но менят топологичния ред, по който сериалното изравняване
    раздава ресурси — и срокът скочи от 741 на 777 дни без нито един ден
    истинско чакане.  Затова: който вече спазва реда, остава свободен; който
    го нарушава, получава връзка.  Плюс ЕДНА връзка към най-ранния участък на
    втората мрежа, за да се вижда правилото в графиката дори когато е спазено.

    Returns:
        (нов график, обяснения) — входният не се мутира.
    """
    from src.tender_parameters import order_of_networks

    cfg = chains if chains is not None else load_chains()
    правила = [r for r in (cfg.get("cross_discipline") or {}).get("rules") or []
               if r.get("network_order")]
    ред = order_of_networks()
    правила = [r for r in правила if r["network_order"] == ред]
    if not правила or not tasks:
        return list(tasks), []

    out = [dict(t) for t in tasks]
    by_id = {str(t.get("id")): t for t in out}
    по_пакет: dict[str, list[dict]] = {}
    for задача in out:
        pid = str(задача.get("parent_id") or "")
        if pid:
            по_пакет.setdefault(pid, []).append(задача)
    верига_на = {p.id: p.chain for p in packages}

    def _старт(задача: dict) -> int:
        try:
            return int(задача.get("start_day"))
        except (TypeError, ValueError):
            return 10 ** 9

    def задачи_на(верига: str) -> list[dict]:
        събрани: list[dict] = []
        for pid, група in по_пакет.items():
            if верига_на.get(pid) == верига:
                събрани.extend(x for x in група if x.get("chain_step"))
        return събрани

    бележки: list[str] = []
    for правило in правила:
        първи = задачи_на(правило["predecessor_chain"])
        ако_втори = правило["successor_chain"]
        if not първи:
            continue
        котва = min(първи, key=_старт)
        ден = _старт(котва)

        начала: list[tuple[int, dict]] = []
        for pid, група in по_пакет.items():
            if верига_на.get(pid) != ако_втори:
                continue
            стъпкови = [x for x in група if x.get("chain_step")]
            if стъпкови:
                първата = min(стъпкови, key=_старт)
                начала.append((_старт(първата), първата))
        if not начала:
            continue

        нарушители = [з for д, з in начала if д < ден]
        най_ранният = min(начала, key=lambda двойка: двойка[0])[1]
        цели = {str(з.get("id")): з for з in нарушители}
        цели.setdefault(str(най_ранният.get("id")), най_ранният)

        сложени = 0
        for задача in цели.values():
            if str(задача.get("id")) == str(котва.get("id")):
                continue
            deps = list(задача.get("dependencies") or [])
            if any(_dep_id(d) == str(котва.get("id")) for d in deps):
                continue
            deps.append({"predecessor_id": str(котва.get("id")),
                         "type": правило.get("type", "SS"),
                         "lag_days": int(правило.get("lag_days", 0)),
                         "reason": правило.get("why", "ред на мрежите")})
            задача["dependencies"] = deps
            сложени += 1
        if сложени:
            бележки.append(
                f"Ред на мрежите ({ред} първо): {ако_втори} тръгва след "
                f"{правило['predecessor_chain']} (ден {ден}); "
                f"{len(нарушители)} участъка бяха преди него, "
                f"{сложени} връзки.")
    return out, бележки


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
    # РЕДЪТ НА МРЕЖИТЕ е отговор на изпълнителя, не находка в документите.
    # Правилата, които го изразяват, идват по двойки — едно за всяка посока — и
    # тук остава само тази, която е обявена за прогона.  Без този филтър
    # отговорът от въпросника стигаше само до промпта на модела, тоест на
    # детерминистичния път нямаше никакъв ефект (19.08.2026).
    from src.tender_parameters import order_of_networks

    обявен_ред = order_of_networks()
    rules = [r for r in rules
             if not r.get("network_order") or r["network_order"] == обявен_ред]
    if not rules:
        return list(tasks)

    out = [dict(t) for t in tasks]
    by_id = {str(t.get("id")): t for t in out}
    pkg_by_id = {p.id: p for p in packages}

    # ВЪЛНИ вместо едно трасе, когато геометрията не е авторитетна.
    #
    # Пак договорът за `suggested`: улица от PDF не бива да РАЗДЕЛЯ
    # зависимостите, защото твърдението „настилката тук чака само канала тук"
    # е точно топология.  Затова досега трасето беше едно и настилката чакаше
    # ВСИЧКИ подземни работи.
    #
    # ИЗМЕРЕНО В ЕТАЛОНА 17.08.2026 — това не е консервативно, а невярно.
    # Човекът прави възстановяването като ПРОЦЕС: стъпката „обратно засипване
    # с уплътняване на пластове, полагане и уплътняване на трошен камък" се
    # среща 46 пъти, по веднъж на участък, с медиана 2 дни, разхвърляна през
    # целия строеж; а редът „възстановяване извън траншеен изкоп" е една
    # задача, която ТЕЧЕ 595 дни успоредно с всичко.  Никъде няма момент, в
    # който целият обект чака последния изкоп, за да се възстанови.
    #
    # Затова участъците се подреждат на вълни: n-тата настилка чака n-тата
    # вълна подземна работа, а последната — всички.  Твърдението е по-слабо от
    # „тази настилка е на мястото на този канал": не се казва КЪДЕ е работата,
    # а само ЧЕ възстановяването върви след завършените етапи, което е точно
    # каквото еталонът показва и което ръководителят на проекта потвърди.
    вълни: dict[str, int] = {}
    по_верига: dict[str, list[SpatialWorkPackage]] = {}
    for pkg in packages:
        по_верига.setdefault(pkg.chain, []).append(pkg)
    брой_вълни = max((len(група) for група in по_верига.values()), default=1)
    for група in по_верига.values():
        for индекс, pkg in enumerate(група):
            вълни[pkg.id] = индекс * брой_вълни // max(len(група), 1)

    def alignment_of(pkg: SpatialWorkPackage) -> str:
        if not spatial_authoritative:
            return f"#{вълни.get(pkg.id, 0)}"
        return (pkg.street or pkg.branch or pkg.label).strip().lower()

    # трасе → пакети по верига
    by_alignment: dict[str, dict[str, list[str]]] = {}
    for pkg in packages:
        by_alignment.setdefault(alignment_of(pkg), {}).setdefault(
            pkg.chain, []).append(pkg.id)

    # Последната вълна носи ЦЯЛАТА останала подземна работа: инак последното
    # възстановяване би могло да свърши преди последния изкоп, а това вече е
    # твърдение, което обектът опровергава.
    с_настилка = [k for k, v in by_alignment.items() if _RESTORATION_CHAIN in v]
    if not spatial_authoritative and с_настилка:
        последна = max(с_настилка, key=lambda k: int(k.lstrip("#") or 0))
        for pkg in packages:
            if pkg.chain == _RESTORATION_CHAIN:
                continue
            в_последната = by_alignment[последна].setdefault(pkg.chain, [])
            if pkg.id not in в_последната:
                в_последната.append(pkg.id)

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

    # РЕДЪТ НА МРЕЖИТЕ се прилага ОТДЕЛНО, защото значи друго (19.08.2026).
    # Останалите правила са за едно трасе: настилката тук чака канала тук.  А
    # „първо В, после К" е твърдение за целия обект, и еталонът показва точно
    # колко силно е: от 34 участъка с двете мрежи в 15 водата тръгва първа, в
    # 15 ЗАЕДНО и в 4 каналът е пръв.  Тоест не е ред участък по участък.
    #
    # Пуснато през вълните, правилото струваше 47 дни и изкара срока над
    # договорните 780: вълните на двете мрежи почти не съвпадат (30 срещу 32
    # пакета при 95 кофи), затова връзките падаха между случайни двойки.
    #
    # Оставя се твърдението, което еталонът поддържа: втората мрежа не тръгва
    # ПРЕДИ първата.  Една връзка, SS с нула дни — редът се вижда, чакане няма.
    # Правилата за РЕДА НА МРЕЖИТЕ не се прилагат тук: те искат дати, а на
    # този етап ги няма (връзките се пишат преди разписването).  Виж
    # `enforce_network_order`, който се пуска след първия проход.
    rules = [r for r in rules if not r.get("network_order")]

    # КРЪСТОСАНАТА ВРЪЗКА ОТСТЪПВА ПРЕД РЕДИЦАТА НА ЕКИПА (25.08.2026).
    #
    # От днес редицата се навързва ПРЕДИ тези връзки: един екип на два клона
    # наведнъж е физика, а „каналът преди водопровода по същото трасе" е
    # предпочитание.  Затова тук връзка, която би затворила кръг с вече
    # съществуващите, се ПРОПУСКА и се брои — иначе графикът не се подрежда
    # топологично и всичко ляга на ден 1 (мерено: 1015 задачи, срок 0 дни).
    наследници: dict[str, set[str]] = {}
    for t in out:
        ид = str(t.get("id"))
        for dep in t.get("dependencies") or []:
            предшественик = _dep_id(dep)
            if предшественик:
                наследници.setdefault(предшественик, set()).add(ид)

    def _достига(откъде: str, докъде: str) -> bool:
        стек, видени = [откъде], {откъде}
        while стек:
            текущ = стек.pop()
            if текущ == докъде:
                return True
            for следващ in наследници.get(текущ, ()):
                if следващ not in видени:
                    видени.add(следващ)
                    стек.append(следващ)
        return False

    пропуснати_заради_кръг = 0

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
                            if _достига(dst, src):
                                пропуснати_заради_кръг += 1
                                continue
                            deps.append({
                                "predecessor_id": src,
                                "type": rule.get("type", "FS"),
                                "lag_days": int(rule.get("lag_days", 0)),
                                "reason": rule.get("why", "cross_discipline"),
                            })
                            task["dependencies"] = deps
                            наследници.setdefault(src, set()).add(dst)
    if пропуснати_заради_кръг:
        logger.info("Кръстосани връзки, пропуснати заради кръг с редицата на "
                    "екипа: %d", пропуснати_заради_кръг)
    return out


def _dep_id(dep: Any) -> str:
    if isinstance(dep, dict):
        return str(dep.get("predecessor_id") or dep.get("id") or "").strip()
    return str(dep or "").strip().split()[0] if dep else ""


#: Вериги, чиито участъци се нареждат в редица при последователна работа.
#: Настилките, кабелите и съоръженията имат своя логика.
_ЛИНЕЙНИ_ЗА_РЕДИЦА = frozenset({"sewer_section", "water_section",
                                "water_section_hdd"})

#: Вериги, чиито пакети заемат ЕКИП.  Настилките са непрекъсната дейност и се
#: сливат отделно; кабелите ги прави друг изпълнител.
_ЗА_РЕДИЦА_НА_ЕКИПА = _ЛИНЕЙНИ_ЗА_РЕДИЦА | {"structure"}


def chain_sections_sequentially(
    tasks: list[dict],
    packages: Sequence[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Когато екипите НЕ работят паралелно, участъкът чака предишния.

    ВТОРИЯТ ВЪПРОС ТРЯБВА ДА МЕНИ ГРАФИКА, НЕ САМО СМЕТКАТА (24.08.2026).
    Отговорът „паралелно ли ще работят екипите" влизаше само в изчислението на
    темпото (`deadline_pace`), а подредбата оставаше поточна линия: осемте
    етапа тръгваха заедно, всяка операция чакаше реда си на бригадата и
    участък от 40 дни работа се влачеше 140 дни с паузи по две седмици.

    Човешкият график за същата поръчка прави обратното: три участъка по 420 м,
    СТРОГО един след друг (92→122, 123→153, 154→183), всеки непрекъснат, с
    едни и същи ресурси.  Това е моделът на един екип по едно трасе.

    Затова тук първата стъпка на всеки следващ участък се закача за последната
    на предишния (FS, лаг 0).  Вътре във фронта: при няколко екипа всеки си
    има своя редица, а тя е тази, която `assign_fronts` вече е направила.

    Пипа само линейните вериги.  Настилките, кабелите и съоръженията си имат
    своя логика и не се нареждат в редица зад тръбите.

    Returns:
        (задачи, бележки) — непроменени, когато екипите работят паралелно.
    """
    # РЕДИЦАТА ВАЖИ ВИНАГИ, В РАМКИТЕ НА ФРОНТА (25.08.2026).
    #
    # Първата версия връзваше участъците само при отговор „не работят
    # паралелно".  Това смесваше две различни неща: КОЛКО екипа работят
    # едновременно, и дали ЕДИН екип може да кара два участъка наведнъж.
    # Второто е физика, не организация — не може.
    #
    # Мерено на Тръстеник: при „паралелно" с два екипа участъците се
    # разтягаха на 123–149 дни с празнини по две седмици, вместо два екипа да
    # карат по своя редица от непрекъснати участъци.  Сега броят фронтове идва
    # от отговора, а вътре във всеки фронт участъците вървят един след друг.
    от_пакет: dict[str, list[dict]] = {}
    for t in tasks or []:
        if t.get("chain_step") and not t.get("is_summary"):
            от_пакет.setdefault(str(t.get("parent_id") or ""), []).append(t)

    # РЕДИЦАТА Е НА ЕКИПА, НЕ НА ВЕРИГАТА (изпълнителят, 25.08.2026:
    # „различните екипи В работят по различни клонове").  Един екип не може да
    # е на два обекта наведнъж — независимо дали вторият е клон, или шахта.
    # Ключът е (мрежа, фронт): всичко, което ЕВ1 държи, върви едно след
    # друго; ЕВ2 кара своя клон паралелно.
    # КОЙ КЛОН КОГА СТАВА ГОТОВ ЗА ВОДОПРОВОДА (26.08.2026).
    #
    # Каналът е пръв по същото трасе, значи водният екип може да влезе в клон
    # чак след като канализационният е излязъл.  Ако редицата на водния екип е
    # подредена по друг признак, той чака: мерено на Тръстеник — ЕВ6 работеше
    # 91 дни, а стоеше на обекта 276, от които 185 в чакане.
    #
    # Затова редицата на водния екип върви по реда, в който КАНАЛЪТ освобождава
    # улиците.  Клон без канал влиза по своя ред.
    ред_на_канала: dict[str, int] = {}
    for ред, pkg in enumerate(packages or []):
        if str(getattr(pkg, "chain", "")) != "sewer_section":
            continue
        улица = str(getattr(pkg, "street", "") or "").strip().lower()
        if улица and улица not in ред_на_канала:
            ред_на_канала[улица] = ред

    редици: dict[tuple[str, str], list[str]] = {}
    верига_на: dict[str, str] = {}
    ред_на_пакета: dict[str, int] = {}
    готовност: dict[str, int] = {}
    for ред, pkg in enumerate(packages or []):
        ред_на_пакета[str(pkg.id)] = ред
        улица = str(getattr(pkg, "street", "") or "").strip().lower()
        готовност[str(pkg.id)] = ред_на_канала.get(улица, -1)
        if pkg.chain not in _ЗА_РЕДИЦА_НА_ЕКИПА:
            continue
        if str(pkg.id) in от_пакет:
            верига_на[str(pkg.id)] = pkg.chain
            редици.setdefault((str(getattr(pkg, "network", "") or ""),
                               str(getattr(pkg, "front", "") or "")),
                              []).append(str(pkg.id))

    # РЕДЪТ Е НА ВЕРИГАТА, НЕ НА ДАТИТЕ.  Тук дати още няма — разписването е
    # по-надолу — и подреждането по `start_day` връзваше произволни стъпки:
    # участъците пак тръгваха заедно.  Стъпките си имат обявен ред в
    # `tech_chains`, и той е верният.
    chain_defs = (chains or {}).get("chains", chains) or {}

    def _ред_на_стъпките(верига: str) -> dict[str, int]:
        стъпки = (chain_defs.get(верига) or {}).get("steps") or []
        return {str(с.get("key")): i for i, с in enumerate(стъпки)}

    by_id = {str(t.get("id")): t for t in tasks or []}

    # ГРАФЪТ, ПРЕДИ ДА ГО ПИПНЕМ.  Редицата на екипа не е единствената връзка:
    # `link_cross_discipline` вече е вързал водопровода за канала по вълни.
    # Затова „последната стъпка на А → първата на Б" понякога затваря КРЪГ, а
    # график с кръг не се подрежда топологично и всичко ляга на ден 1.
    # Мерено на контролния прогон (Илиянци): 780 дни ставаха 56, статус
    # invalid.  Такава връзка се ПРОПУСКА и се обявява.
    наследници: dict[str, set[str]] = {}
    for t in tasks or []:
        ид = str(t.get("id"))
        for dep in t.get("dependencies") or []:
            предшественик = _dep_id(dep)
            if предшественик:
                наследници.setdefault(предшественик, set()).add(ид)

    def _достига(откъде: str, докъде: str) -> bool:
        стек = [откъде]
        видени = {откъде}
        while стек:
            текущ = стек.pop()
            if текущ == докъде:
                return True
            for следващ in наследници.get(текущ, ()):
                if следващ not in видени:
                    видени.add(следващ)
                    стек.append(следващ)
        return False

    вързани = 0
    пропуснати = 0

    def _краен(pid: str) -> dict:
        ред = _ред_на_стъпките(верига_на[pid])
        return max(от_пакет[pid],
                   key=lambda t: (ред.get(str(t.get("chain_step") or ""), 10 ** 6),
                                  str(t.get("id"))))

    def _начален(pid: str) -> dict:
        ред = _ред_на_стъпките(верига_на[pid])
        return min(от_пакет[pid],
                   key=lambda t: (ред.get(str(t.get("chain_step") or ""), 10 ** 6),
                                  str(t.get("id"))))

    for (_мрежа, фронт), pids in sorted(редици.items()):
        if len(pids) < 2:
            continue
        # Тръбите първо, съоръженията след тях — екипът копае трасето и чак
        # после прави шахтите по него.
        # РЕДЪТ Е НА ИЗПЪЛНЕНИЕТО, НЕ АЗБУЧЕН.  Сортирането по низ подрежда
        # „В100, В101, … В11, В110" — тоест редицата на екипа се навързваше в
        # случаен ред и участъци оставаха без предшественик.
        pids.sort(key=lambda pid: (0 if верига_на[pid] in _ЛИНЕЙНИ_ЗА_РЕДИЦА
                                   else 1,
                                   готовност.get(pid, -1),
                                   ред_на_пакета.get(pid, 0)))
        for предишен, следващ in zip(pids, pids[1:]):
            край = _краен(предишен)
            начало = _начален(следващ)
            задача = by_id.get(str(начало.get("id")))
            if задача is None:
                continue
            deps = list(задача.get("dependencies") or [])
            ид_на_края = str(край.get("id"))
            ид_на_началото = str(задача.get("id"))
            if any(_dep_id(d) == ид_на_края for d in deps):
                continue
            if _достига(ид_на_началото, ид_на_края):
                пропуснати += 1        # би затворило кръг — виж по-горе
                continue
            deps.append({"predecessor_id": ид_на_края, "type": "FS",
                         "lag_days": 0,
                         "reason": "последователна работа: участъкът чака "
                                   "предишния (един екип, един участък)"})
            задача["dependencies"] = deps
            наследници.setdefault(ид_на_края, set()).add(ид_на_началото)
            вързани += 1

    бележки = []
    if вързани:
        бележки.append(
            f"Последователна работа: {вързани} участъка чакат предишния — "
            "един екип кара един участък наведнъж, както в човешкия график.")
        logger.info("%s", бележки[0])
    if пропуснати:
        бележки.append(
            f"{пропуснати} връзки в редицата НЕ са сложени: вече има обратен "
            "път по кръстосаните зависимости и връзката би затворила кръг.")
        logger.info("%s", бележки[-1])
    return tasks, бележки


#: Коя проектна част следва коя мрежа.  Само тези две — останалите части
#: (геодезия, пътна, ПБЗ) обслужват целия обект, а не една мрежа.
_ЧАСТ_НА_МРЕЖА = {"water": ("water_section", "water_section_hdd"),
                  "sewer": ("sewer_section",)}


def scale_design_parts_to_networks(
    tasks: list[dict],
    packages: Sequence[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Проектната част следва РАЗМЕРА на своята мрежа.

    ЗАЩО (изпълнителят, 25.08.2026): „обърни внимание, че водопроводът е над
    11 км, а каналът е 4 км… съобрази го и с проектирането, разликата не е
    малка В/К."

    Веригата за проектиране е извлечена от Илиянци, където двете мрежи са
    съизмерими (3247 м водопровод, 4075 м канал) и частите излизат по 50 дни
    всяка.  При Тръстеник съотношението е 2.8 : 1 и равните дни са просто
    невярни — час проектант не се харчи еднакво за 11 664 и за 4 183 метра.

    Сборът на двете части се ЗАПАЗВА: преразпределя се, не се раздува.  Така
    договорният срок за проектиране остава ненарушен, а тежестта вътре в него
    отразява обекта.

    Returns:
        (задачи, бележки).
    """
    метри: dict[str, float] = {}
    for pkg in packages or []:
        верига = str(getattr(pkg, "chain", "") or "")
        if верига:
            метри[верига] = метри.get(верига, 0.0) + _линейни_метри_на_пакет(pkg)

    по_част: dict[str, float] = {}
    for част, вериги in _ЧАСТ_НА_МРЕЖА.items():
        по_част[част] = sum(метри.get(в, 0.0) for в in вериги)
    ако = sum(по_част.values())
    if ако <= 0 or min(по_част.values()) <= 0:
        return tasks, []

    задачи_на_част = {}
    for t in tasks or []:
        ключ = str(t.get("chain_step") or "")
        if ключ in по_част and not t.get("is_summary"):
            задачи_на_част.setdefault(ключ, []).append(t)
    if len(задачи_на_част) < 2:
        return tasks, []

    сбор = sum(_as_number(t.get("duration")) or 0
               for листа in задачи_на_част.values() for t in листа)
    if сбор <= 0:
        return tasks, []

    бележки: list[str] = []
    for част, листа in задачи_на_част.items():
        дял = по_част[част] / ако
        нови = max(1, int(round(сбор * дял / max(len(листа), 1))))
        for t in листа:
            беше = int(_as_number(t.get("duration")) or 0)
            if нови != беше:
                t.setdefault("computed_duration", беше)
                t["duration"] = нови
                t["network_share"] = round(дял, 3)
        бележки.append(
            f"Проектна част „{част}“: {по_част[част]:.0f} м от {ако:.0f} "
            f"({100 * дял:.0f} %) → {нови} дни.")
    for бележка in бележки:
        logger.info("%s", бележка)
    return tasks, бележки


def _линейни_метри_на_пакет(pkg: Any) -> float:
    from src.segment_scale import _линейни_метри
    return _линейни_метри(pkg)


#: Затварящите стъпки на проектирането: те обобщават ВСИЧКИ части и не могат
#: да започнат, преди частите да са готови.
_ЗАТВАРЯЩИ_ПРОЕКТНИ = ("boq", "general_notes", "internal_review", "handover")


def close_design_after_all_parts(
    tasks: list[dict],
    packages: Sequence[SpatialWorkPackage],
    chains: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Сметната документация и записката чакат ВСИЧКИ проектни части.

    ЗАЩО (Тръстеник, 25.08.2026).  Топологията е извлечена от Илиянци, където
    частите са съизмерими и вървят със застъпване.  Щом една част порасне —
    „Водоснабдяване" стана 32 дни срещу 11 за канала, защото мрежата е 2.8
    пъти по-голяма — застъпването се обръща в безсмислица: сметната
    документация свършваше на 27.06, а водоснабдяването продължаваше до 15.07.

    Не можеш да остойностиш проект, чиито части не са готови, нито да напишеш
    обща записка за решения, които още не съществуват.  Затова затварящите
    стъпки получават FS връзка към ВСЯКА проектна част.

    Returns:
        (задачи, бележки).
    """
    проектни = {str(p.id) for p in (packages or [])
                if str(getattr(p, "chain", "")) == "design"}
    if not проектни:
        return tasks, []

    листа = [t for t in tasks or []
             if str(t.get("parent_id") or "") in проектни
             and t.get("chain_step") and not t.get("is_summary")]
    части = [t for t in листа
             if str(t.get("chain_step")) not in _ЗАТВАРЯЩИ_ПРОЕКТНИ
             and not (t.get("milestone") or t.get("is_milestone"))]
    затварящи = [t for t in листа
                 if str(t.get("chain_step")) in _ЗАТВАРЯЩИ_ПРОЕКТНИ]
    if not части or not затварящи:
        return tasks, []

    # САМО НАЗАД ПО ВЕРИГАТА.  Съгласуванията и разрешенията стоят СЛЕД
    # предаването и вече зависят от него; връзка към тях затваря цикъл и
    # графикът спира да се подрежда топологично (падаше `test_minimal_input`).
    речник = (chains or {})
    речник = речник.get("chains") or речник        # и двете форми на входа
    ред = {str(стъпка.get("key")): i for i, стъпка
           in enumerate((речник.get("design") or {}).get("steps") or [])}

    добавени = 0
    for t in затварящи:
        deps = list(t.get("dependencies") or [])
        има = {_dep_id(d) for d in deps}
        мой_ред = ред.get(str(t.get("chain_step")))
        for част in части:
            иден = str(част.get("id"))
            неин_ред = ред.get(str(част.get("chain_step")))
            if (мой_ред is not None and неин_ред is not None
                    and неин_ред > мой_ред):
                continue
            if иден in има or иден == str(t.get("id")):
                continue
            deps.append({"predecessor_id": иден, "type": "FS", "lag_days": 0,
                         "reason": "затварящата стъпка чака всички части"})
            добавени += 1
        t["dependencies"] = deps

    бележки = []
    if добавени:
        бележки.append(
            f"Проектиране: {len(затварящи)} затварящи стъпки вързани към "
            f"{len(части)} части ({добавени} връзки) — сметната документация и "
            "записката не могат да предхождат частите, които обобщават.")
        logger.info("%s", бележки[0])
    return tasks, бележки


def order_chronologically(tasks: list[dict]) -> list[dict]:
    """Подрежда ФАЗИТЕ по деня, в който започват, без да пипа съдържанието им.

    MS Project показва задачите в реда на файла.  Пакетният път дописва
    договорните фази НАКРАЯ, затова „ПРОЕКТИРАНЕ" (ден 1–45) лягаше под
    „СТРОИТЕЛСТВО" (ден 58 нататък) и човекът, който отваря графика, просто
    не го виждаше — Тръстеник, 25.08.2026.

    ПОДРЕЖДА СЕ ВСЯКО НИВО (изпълнителят, 25.08.2026: „дейностите в един клон
    да са последователни — НЕ трябва в същия клон дейност да е на по-долен ред,
    а да стартира по-рано от по-горен ред").  Първата версия пипаше само върха,
    защото технологичната верига невинаги е хронологична; на листа обаче това
    се чете като грешка — окото следи редовете отгоре надолу и очаква стълба.
    Зависимостите НЕ се пипат: мени се само редът на показване, а при равен
    старт остава редът на веригата.
    """
    по_ид = {str(t.get("id", "")).strip(): t for t in tasks if t.get("id")}
    деца: dict[str, list[dict]] = {}
    корени: list[dict] = []
    for задача in tasks:
        родител = str(задача.get("parent_id") or "").strip()
        ид = str(задача.get("id", "")).strip()
        if родител and родител != ид and родител in по_ид:
            деца.setdefault(родител, []).append(задача)
        else:
            корени.append(задача)

    ред = {id(t): i for i, t in enumerate(tasks)}

    def по_начало(задача: dict) -> tuple[int, int]:
        return (int(задача.get("start_day", 1) or 1), ред[id(задача)])

    корени.sort(key=по_начало)
    for списък in деца.values():
        списък.sort(key=по_начало)

    изход: list[dict] = []
    видени: set[int] = set()

    def обходи(задача: dict) -> None:
        if id(задача) in видени:            # счупена йерархия (цикъл)
            return
        видени.add(id(задача))
        изход.append(задача)
        for дете in деца.get(str(задача.get("id", "")).strip(), []):
            обходи(дете)

    for корен in корени:
        обходи(корен)
    # Каквото е останало извън дървото, върви накрая — нищо не се губи.
    изход.extend(t for t in tasks if id(t) not in видени)
    return изход


def sync_durations_to_span(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Продължителността на задачата = дните, които тя заема.  Без изключения.

    ЗАЩО (изпълнителят, 25.08.2026): „226 Монтаж на фасонни части… и преди е
    свършило да е започнало Промиване… всяка дейност е след предходната".

    Прав е, и причината е разминаване В НАШИТЕ СОБСТВЕНИ ЧИСЛА: задачата
    заемаше дни 54–57 (четири дни), а полето `duration` беше останало 6 от
    по-ранен проход.  Датите в графика идват от дните, а лентата в MS Project —
    от продължителността; щом двете не съвпадат, лентата излиза по-дълга и
    покрива следващата дейност.

    Тук двете се изравняват НАКРАЯ, по дните — те са това, което човекът чете
    и което изнасяме.  Milestone-ите остават нула.

    Returns:
        (задачи, бележки) — колко задачи са били разминати.
    """
    оправени = 0
    for t in tasks or []:
        if t.get("is_summary") or t.get("type") == "summary":
            continue
        if t.get("milestone") or t.get("is_milestone"):
            if _num(t.get("duration")):
                t["duration"] = 0
                оправени += 1
            continue
        начало, край = t.get("start_day"), t.get("end_day")
        if начало is None or край is None:
            continue
        дни = max(int(_num(край)) - int(_num(начало)) + 1, 1)
        if int(_num(t.get("duration"))) != дни:
            t["computed_duration"] = int(_num(t.get("duration")))
            t["duration"] = дни
            оправени += 1

    бележки: list[str] = []
    if оправени:
        бележки.append(
            f"Изравнени с дните: {оправени} задачи носеха продължителност, "
            "различна от дните, които заемат — оттам идваха застъпените ленти.")
        logger.info("%s", бележки[0])
    return tasks, бележки


def fit_contract_span(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Обектът свършва точно на ден `проектиране + строителство`.

    ЗАЩО (изпълнителят, 25.08.2026): „Искам всичко да е 255 дни, 45 дни
    проектиране и 210 дни строителство".  Обявеният срок е ТАВАН И ЦЕЛ: график,
    който свършва по-рано, поема риск без насрещна полза, а такъв, който
    свършва по-късно, е неотговаряща оферта.

    Налагането по фази (`enforce_declared_phase_terms`) работи по-нагоре по
    веригата; след него минават изравняването, редицата на екипите и сливането
    на непрекъснатите действия, и краят пак увисва — мерено на Тръстеник: 235
    дни при обявени 255.  Тук краят се ЗАКОВАВА: последните действия по
    приемането се разтеглят до целевия ден.

    Returns:
        (задачи, бележки).
    """
    from src.tender_parameters import declared_phase_days

    обявени = declared_phase_days() or {}
    проектиране = int(обявени.get("design") or 0)
    строителство = int(обявени.get("construction") or 0)
    if проектиране <= 0 or строителство <= 0:
        return tasks, []
    цел = проектиране + строителство

    листа = [t for t in tasks or []
             if not (t.get("is_summary") or t.get("type") == "summary")
             and t.get("end_day") is not None]
    if not листа:
        return tasks, []
    край = max(int(_num(t.get("end_day"))) for t in листа)
    недостиг = цел - край

    def _надзорът_до_края() -> None:
        """Надзорът върви до ДОГОВОРНИЯ край, не до последната задача.

        `enforce_construction_span` мери края по задачите, които НЕ зависят от
        него (заради ратчета) и оставаше ден по-къс: „46–254" при обявено
        „46 до края".
        """
        for t in tasks or []:
            if not t.get("spans_construction"):
                continue
            t["end_day"] = цел
            t["duration"] = цел - int(_num(t.get("start_day"))) + 1

    if недостиг <= 0:
        _надзорът_до_края()
        return tasks, ([] if недостиг == 0 else [])

    # Разтягат се ПРИЕМАНЕТО и последните му действия — те държат края.
    опашка = [t for t in листа
              if str(t.get("wbs_root") or "") == "acceptance"
              and not (t.get("milestone") or t.get("is_milestone"))]
    ако_няма = [t for t in листа if int(_num(t.get("end_day"))) == край]
    работни = опашка or [t for t in ако_няма
                         if not (t.get("milestone") or t.get("is_milestone"))]
    if not работни:
        return tasks, []

    # Последното действие поема дните, а всичко след него се мести напред.
    последно = max(работни, key=lambda t: int(_num(t.get("end_day"))))
    праг = int(_num(последно.get("end_day")))
    последно["end_day"] = праг + недостиг
    последно["duration"] = (int(_num(последно.get("end_day")))
                            - int(_num(последно.get("start_day"))) + 1)
    последно["declared_term_days"] = True
    for t in листа:
        if t is последно:
            continue
        if int(_num(t.get("start_day"))) > праг:
            t["start_day"] = int(_num(t.get("start_day"))) + недостиг
            t["end_day"] = int(_num(t.get("end_day"))) + недостиг

    _надзорът_до_края()

    бележка = (f"Договорен срок: обектът свършва на ден {цел} "
               f"({проектиране} проектиране + {строителство} строителство) — "
               f"краят е разтеглен с {недостиг} дни по приемането, а надзорът "
               f"стига до ден {цел}.")
    logger.info("%s", бележка)
    return tasks, [бележка]


def dispatch_sections(
    tasks: list[dict],
    packages: Sequence[SpatialWorkPackage],
) -> tuple[list[dict], list[str]]:
    """Свободният екип взима следващия ГОТОВ клон — не чака точно „своя".

    ЗАЩО (изпълнителят, 26.08.2026): „Каналът тръгва първи, но това не пречи на
    водопровода да върви по другите клонове.  След като каналът отвори фронт,
    екипите на водата започват и там.  Има 11 км вода и 4 км канал — място има
    за всички, само да не спират да работят."

    Прав е и точно това не правеше кодът.  Редицата на екипа се определяше
    ПРЕДВАРИТЕЛНО и беше неизменна: ако следващият клон в нея още чака канала,
    екипът СТОЕШЕ, вместо да вземе друг клон, който е готов.  Мерено на
    Тръстеник: водните екипи работеха 119–208 дни, а стояха на обекта до 378 —
    до 231 дни чакане, при 71 водопроводни клона, от които само 36 изобщо имат
    канал по същата улица.

    Тук редицата се прави ДИНАМИЧНО, както се прави на обект: минава се по
    клоновете в реда, в който стават готови, и всеки се дава на екипа, който се
    освобождава пръв.  Клон, който още чака канала, не блокира екипа — той
    просто идва по-късно в редицата.

    Клонът се мести ЦЯЛ (всичките му стъпки заедно), защото вътре в него редът
    е технологичен и вече е нареден.

    Returns:
        (задачи, бележки).
    """
    по_ид = {str(t.get("id")): t for t in tasks or []}
    листа: dict[str, list[dict]] = {}
    for t in tasks or []:
        if not t.get("chain_step") or t.get("is_summary"):
            continue
        if str(t.get("wbs_root") or "construction") != "construction":
            continue
        листа.setdefault(str(t.get("parent_id") or ""), []).append(t)
    if not листа:
        return tasks, []

    верига_на = {str(p.id): str(getattr(p, "chain", "")) for p in packages or []}
    мрежа_на = {str(p.id): str(getattr(p, "network", "")) for p in packages or []}
    екипи_на_мрежа: dict[str, list[str]] = {}
    for p in packages or []:
        фронт = str(getattr(p, "front", "") or "")
        мрежа = str(getattr(p, "network", "") or "")
        if фронт and мрежа and фронт not in екипи_на_мрежа.setdefault(мрежа, []):
            екипи_на_мрежа[мрежа].append(фронт)
    for мрежа in екипи_на_мрежа:
        екипи_на_мрежа[мрежа].sort()

    #: Кои клонове се раздават динамично — линейните.  Настилките, кабелите и
    #: съоръженията си имат своя логика и не влизат в редицата на тръбните екипи.
    подлежащи = [pid for pid in листа
                 if верига_на.get(pid) in _ЛИНЕЙНИ_ЗА_РЕДИЦА
                 and мрежа_на.get(pid) in екипи_на_мрежа]
    if not подлежащи:
        return tasks, []

    начало_на_строителството = min(
        int(_num(t.get("start_day"))) for деца in листа.values() for t in деца)

    # Кой клон от КОЯ ДРУГА мрежа чака — това е единственото истинско чакане.
    външни: dict[str, set[str]] = {}
    for pid in подлежащи:
        свои = {str(t.get("id")) for t in листа[pid]}
        за_него: set[str] = set()
        for t in листа[pid]:
            for dep in t.get("dependencies") or []:
                ид = _dep_id(dep)
                ако = по_ид.get(ид)
                if ако is None or ид in свои:
                    continue
                чужд = str(ако.get("parent_id") or "")
                if чужд in листа and мрежа_на.get(чужд) != мрежа_на.get(pid):
                    за_него.add(чужд)
        външни[pid] = за_него

    продължителност = {
        pid: sum(int(_num(t.get("duration"))) for t in листа[pid])
        for pid in подлежащи}

    # Каналът се разписва пръв: той отваря фронтовете.  После водата, която
    # вече знае кога всяка улица е свободна.
    ред_на_мрежите = sorted(екипи_на_мрежа, key=lambda м: 0 if м == "К" else 1)
    край_на_клон: dict[str, int] = {}
    редица_на_екипа: dict[str, list[str]] = {}
    преместени = 0
    for мрежа in ред_на_мрежите:
        мои = [pid for pid in подлежащи if мрежа_на.get(pid) == мрежа]
        if not мои:
            continue
        екипи = екипи_на_мрежа[мрежа]
        свободен = {е: начало_на_строителството for е in екипи}
        чакащи = set(мои)
        while чакащи:
            # Кой клон КОГА може да тръгне: щом чуждата мрежа го е освободила.
            готовност = {
                pid: max([начало_на_строителството]
                         + [край_на_клон[ч] + 1 for ч in външни.get(pid, ())
                            if ч in край_на_клон])
                for pid in чакащи}
            екип = min(екипи, key=lambda е: (свободен[е], е))
            готови = [pid for pid in чакащи if готовност[pid] <= свободен[екип]]
            if not готови:
                # НИКОЙ ЕКИП ДА НЕ ЗАПАЗВА КЛОН ЗА СЕБЕ СИ (26.08.2026).
                # Ако за най-рано свободния екип няма готов клон, часовникът му
                # просто се превърта до първата готовност — клонът остава общ и
                # може да го вземе екип, който се освобождава по-късно, но пък
                # е свободен точно тогава.  Иначе екипът „резервира" далечен
                # клон и стои, а работата се разстила: мерено 2196 екипо-дни в
                # 583 дни при 220 възможни.
                свободен[екип] = min(готовност[pid] for pid in чакащи)
                continue
            # Измежду готовите взимаме най-дългия — така опашката не остава с
            # един огромен клон накрая.
            избран = max(готови, key=lambda pid: (продължителност[pid], pid))
            старт = max(свободен[екип], готовност[избран])
            деца = листа[избран]
            беше = min(int(_num(t.get("start_day"))) for t in деца)
            изместване = старт - беше
            if изместване:
                for t in деца:
                    t["start_day"] = int(_num(t.get("start_day"))) + изместване
                    t["end_day"] = int(_num(t.get("end_day"))) + изместване
                преместени += 1
            край = max(int(_num(t.get("end_day"))) for t in деца)
            край_на_клон[избран] = край
            свободен[екип] = край + 1
            # Клонът вече е на този екип — и в графика, и в колоната „ЕКИП".
            for t in деца:
                t["team"] = екип
                t["crew_id"] = екип
            чакащи.discard(избран)
            редица_на_екипа.setdefault(екип, []).append(избран)

    # ВРЪЗКИТЕ СЛЕДВАТ НОВАТА РЕДИЦА (26.08.2026).  Старите връзки „участъкът
    # чака предишния" са от предварителната подредба; ако останат, CPM
    # пренарежда графика по ТЯХ и цялото раздаване отива на вятъра — мерено:
    # 2196 екипо-дни се разстилаха на 583 дни вместо на 220.
    махнати = 0
    for деца in листа.values():
        for t in деца:
            deps = t.get("dependencies") or []
            остават = [d for d in deps
                       if not (isinstance(d, dict)
                               and str(d.get("reason", "")).startswith(
                                   "последователна работа"))]
            if len(остават) != len(deps):
                махнати += len(deps) - len(остават)
                t["dependencies"] = остават

    добавени = 0
    for екип, редица in редица_на_екипа.items():
        for предишен, следващ in zip(редица, редица[1:]):
            край = max(листа[предишен],
                       key=lambda t: int(_num(t.get("end_day"))))
            начало = min(листа[следващ],
                         key=lambda t: int(_num(t.get("start_day"))))
            deps = list(начало.get("dependencies") or [])
            if any(_dep_id(d) == str(край.get("id")) for d in deps):
                continue
            deps.append({"predecessor_id": str(край.get("id")), "type": "FS",
                         "lag_days": 0,
                         "reason": "последователна работа: екипът кара един "
                                   "клон наведнъж"})
            начало["dependencies"] = deps
            добавени += 1

    # ВСЕКИ КРАЙ ВОДИ НАНЯКЪДЕ.  Махането на старите връзки оставя последния
    # клон на всеки екип без наследник — тогава графикът има десет края вместо
    # един и `all_leaves_reach_terminal` пада.  Последният клон се връзва там,
    # където водеха и старите: към първата задача от приемането.
    приемане = [t for t in tasks or []
                if str(t.get("wbs_root") or "") == "acceptance"
                and not t.get("is_summary")]
    закачени = 0
    if приемане:
        цел = min(приемане, key=lambda t: int(_num(t.get("start_day"))))
        имащи_наследник = {_dep_id(d) for t in tasks or []
                           for d in (t.get("dependencies") or [])}
        for редица in редица_на_екипа.values():
            if not редица:
                continue
            последен = редица[-1]
            край = max(листа[последен],
                       key=lambda t: int(_num(t.get("end_day"))))
            ид = str(край.get("id"))
            if ид in имащи_наследник:
                continue
            deps = list(цел.get("dependencies") or [])
            deps.append({"predecessor_id": ид, "type": "FS", "lag_days": 0,
                         "reason": "всяка работа преди приемането"})
            цел["dependencies"] = deps
            закачени += 1

    бележки: list[str] = []
    if край_на_клон:
        общо = sum(продължителност[pid] for pid in подлежащи)
        зает = max(край_на_клон.values()) - начало_на_строителството + 1
        бележки.append(
            f"Динамична редица: {len(подлежащи)} клона раздадени на "
            f"{sum(len(v) for v in екипи_на_мрежа.values())} екипа; "
            f"{преместени} преместени.  Свободният екип взима следващия ГОТОВ "
            f"клон, вместо да чака своя — {общо} екипо-дни работа в {зает} "
            f"дни.  Връзките в редицата: {махнати} стари махнати, "
            f"{добавени} нови, {закачени} края вързани към приемането.")
        logger.info("%s", бележки[0])
    return tasks, бележки


def compact_sections(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Дейностите в участъка се долепят една до друга — без празни дни.

    ЗАЩО (изпълнителят, 26.08.2026): „Как така клон с 62 м дължина ще се
    работи до 327 ден?!"  Прав е.  Клонът има 14 дни работа, а стоеше
    разпънат на 177: започваше рано, чакаше края на друга мрежа и довършваше
    месеци по-късно.  Бригадата не работи така — тя идва, свършва участъка и
    си отива.

    Затова тук всяко действие се ДРЪПВА НАПРЕД до деня преди следващото:
    последното остава на мястото си (то държи срока и връзките към другите
    мрежи), а предходните се долепят за него отзад напред.

    Само НАПРЕД.  Дърпане назад би нарушило предшественик; дърпане напред може
    да закъснее нещо, което ЧАКА тази задача извън участъка — затова всяко
    местене се проверява срещу външните наследници и се отказва, ако ги
    изпреварва.

    Returns:
        (задачи, бележки).
    """
    по_родител: dict[str, list[dict]] = {}
    for t in tasks or []:
        if not t.get("chain_step") or t.get("is_summary"):
            continue
        if str(t.get("wbs_root") or "construction") != "construction":
            continue
        по_родител.setdefault(str(t.get("parent_id") or ""), []).append(t)
    if not по_родител:
        return tasks, []

    по_ид = {str(t.get("id")): t for t in tasks or []}
    външни_наследници: dict[str, list[str]] = {}
    for t in tasks or []:
        за = str(t.get("parent_id") or "")
        for dep in t.get("dependencies") or []:
            ид = _dep_id(dep)
            ако = по_ид.get(ид)
            if ако is None:
                continue
            if str(ако.get("parent_id") or "") != за:
                външни_наследници.setdefault(ид, []).append(str(t.get("id")))

    затворени = отказани = 0
    for листа in по_родител.values():
        подредени = sorted(листа, key=lambda t: (int(_num(t.get("start_day"))),
                                                 int(_num(t.get("end_day")))))
        for i in range(len(подредени) - 2, -1, -1):
            текуща, следваща = подредени[i], подредени[i + 1]
            цел = int(_num(следваща.get("start_day"))) - 1
            изместване = цел - int(_num(текуща.get("end_day")))
            if изместване <= 0:
                continue
            # Кой чака ТАЗИ задача извън участъка — той не бива да я изпревари.
            най_рано = min(
                (int(_num(по_ид[н].get("start_day")))
                 for н in външни_наследници.get(str(текуща.get("id")), [])
                 if н in по_ид),
                default=None)
            if най_рано is not None and цел >= най_рано:
                отказани += 1
                continue
            текуща["start_day"] = int(_num(текуща.get("start_day"))) + изместване
            текуща["end_day"] = цел
            текуща["compacted_days"] = изместване
            затворени += изместване

    бележки: list[str] = []
    if затворени:
        бележки.append(
            f"Долепени действия: {затворени} празни дни махнати от участъците "
            "— бригадата идва, изкарва участъка и си отива.")
        logger.info("%s", бележки[0])
    if отказани:
        бележки.append(
            f"{отказани} действия НЕ се долепиха: друга мрежа ги чака и "
            "местенето би я забавило.")
    return tasks, бележки


def queue_sections_per_crew(
    tasks: list[dict],
    packages: Sequence[SpatialWorkPackage],
) -> tuple[list[dict], list[str]]:
    """Един екип — една редица от клонове, БЕЗ застъпване в датите.

    ЗАЩО (изпълнителят, 25.08.2026): „ЕВ1 започва единия етап, ЕВ2 започва
    другия и всеки като приключи започва следващ… от графиката не се вижда
    нищо".  Прав е: мерено на Тръстеник, ЕВ1 държеше 36 клона, от които 19
    двойки се застъпваха във времето — един екип на два клона наведнъж.

    Връзките в редицата ги слага `chain_sections_sequentially`, но след нея
    минават ресурсното изравняване, налагането на срока и сливането на
    непрекъснатите действия; те разтеглят отделни стъпки и застъпването се
    връща.  Затова ТУК, накрая, редицата се налага върху самите ДАТИ: клонът
    започва в първия ден, в който неговият екип е свободен.

    Само измества НАПРЕД — предшественик не може да бъде нарушен от закъснение.

    Returns:
        (задачи, бележки) — колко клона са преместени и с колко дни.
    """
    деца: dict[str, list[dict]] = {}
    for t in tasks or []:
        if t.get("is_summary") or t.get("type") == "summary":
            continue
        деца.setdefault(str(t.get("parent_id") or ""), []).append(t)

    редици: dict[tuple[str, str], list[str]] = {}
    for pkg in packages or []:
        if pkg.chain not in _ЗА_РЕДИЦА_НА_ЕКИПА:
            continue
        фронт = str(getattr(pkg, "front", "") or "")
        if not фронт or str(pkg.id) not in деца:
            continue
        редици.setdefault((str(getattr(pkg, "network", "") or ""), фронт),
                          []).append(str(pkg.id))

    преместени = 0
    дни_общо = 0
    for (_мрежа, _фронт), pids in sorted(редици.items()):
        обхвати = []
        for pid in pids:
            листа = деца[pid]
            начало = min(int(_num(t.get("start_day")) or 0) for t in листа)
            край = max(int(_num(t.get("end_day")) or 0) for t in листа)
            обхвати.append((начало, край, pid))
        обхвати.sort()

        свободен = 0
        for начало, _край, pid in обхвати:
            изместване = max(0, свободен - начало)
            листа = деца[pid]
            if изместване:
                for t in листа:
                    t["start_day"] = int(_num(t.get("start_day"))) + изместване
                    t["end_day"] = int(_num(t.get("end_day"))) + изместване
                    t["queued_for_crew"] = изместване
                преместени += 1
                дни_общо += изместване
            свободен = max(int(_num(t.get("end_day")) or 0) for t in листа) + 1

    бележки: list[str] = []
    if преместени:
        бележки.append(
            f"Редица на екипа: {преместени} клона изместени с общо {дни_общо} "
            "дни, за да не се застъпват — един екип кара един клон наведнъж.")
        logger.info("%s", бележки[0])
    return tasks, бележки


def _num(стойност: Any) -> float:
    try:
        return float(стойност)
    except (TypeError, ValueError):
        return 0.0


#: Колко ДНИ най-много може да порасне едно действие, за да затвори празнина.
#: Освен това не може да порасне повече от двойно спрямо сметнатото.
_ТАВАН_НА_РАЗТЯГАНЕТО = 5

#: Изключването на непрекъснатите действия — за сравнение при мерене.
ФЛАГ_НЕПРЕКЪСНАТО = "CONTINUOUS_ACTIONS"


def make_actions_continuous(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Действията в участъка вървят едно след друго, без празни дни.

    ЗАЩО (изпълнителят, 25.08.2026: „направи графика да е с последователни
    действия, както е на Илиянци и Русе").  Мерено на Тръстеник преди тази
    поправка: **257 празни дни в 140 прехода** — водопроводен участък караше
    по 7 дни работа и 5 дни пауза, отново и отново.  Дупките идват от
    изравняването на общите ресурси и от лаговете на веригата.

    Човешките графици нямат такива дупки: Илиянци е чист FS с лаг 0, 608 от
    610 задачи критични.  Бригада, която е на обекта, работи; тя не си отива
    за пет дни и не се връща.

    Затова празнината се ЗАТВАРЯ чрез разтягане на предходното действие до
    деня преди следващото — датите на участъка и срокът остават същите, а
    графикът престава да твърди прекъсвания, каквито няма.  Не се мести нищо:
    местенето би скъсало изравняването, което току-що ги е подредило.

    Разтягането се ОГРАНИЧАВА от собствените зависимости на задачата: щом
    някой я чака, тя не може да се разлее върху него.

    Returns:
        (задачи, бележки).
    """
    if os.getenv(ФЛАГ_НЕПРЕКЪСНАТО, "1") == "0":
        return tasks, []

    # САМО СТРОИТЕЛСТВОТО.  „Последователни действия" е за бригадата на
    # обекта.  Проектните части вървят паралелно и са ограничени от броя
    # проектанти — разтегли ли се една, петима проектанти работят там, където
    # ги има четирима (падаше `test_minimal_input`).  Паузата между части е
    # изчакване на вход, не празен ход на бригада.
    от_пакет: dict[str, list[dict]] = {}
    for t in tasks or []:
        if not t.get("chain_step") or t.get("is_summary"):
            continue
        if str(t.get("wbs_root") or "construction") != "construction":
            continue
        от_пакет.setdefault(str(t.get("parent_id") or ""), []).append(t)
    if not от_пакет:
        return tasks, []

    # Кой чака КОГО — таванът на разтягането.
    таван: dict[str, int] = {}
    for t in tasks or []:
        начало = _as_number(t.get("start_day"))
        край = _as_number(t.get("end_day"))
        for dep in t.get("dependencies") or []:
            ид = _dep_id(dep)
            if not ид:
                continue
            вид = str(dep.get("type") or "FS").upper() if isinstance(dep, dict) else "FS"
            if вид in ("FS", "SF") and начало is not None:
                граница = int(начало) - 1
            elif вид == "FF" and край is not None:
                граница = int(край)
            else:
                continue                       # SS не ограничава края
            if ид not in таван or граница < таван[ид]:
                таван[ид] = граница

    затворени = 0
    разтегнати = 0
    for листа in от_пакет.values():
        подредени = sorted(
            листа, key=lambda t: (_as_number(t.get("start_day")) or 0,
                                  _as_number(t.get("end_day")) or 0))
        начала = sorted(_as_number(t.get("start_day")) or 0 for t in подредени)
        for t in подредени:
            if t.get("milestone") or t.get("is_milestone"):
                continue
            край = _as_number(t.get("end_day"))
            продължителност = _as_number(t.get("duration")) or 0
            if край is None or продължителност <= 0:
                continue
            край = int(край)
            следващо = next((int(н) for н in начала if н > край + 1), None)
            if следващо is None:
                continue                       # последното действие в участъка
            нов_край = следващо - 1
            граница = таван.get(str(t.get("id")))
            if граница is not None:
                нов_край = min(нов_край, граница)
            # ПРАЗНИНАТА НЕ СЕ ЗАМАЗВА С ИЗМИСЛЕНА РАБОТА (26.08.2026).
            #
            # Разтягането затваря паузите на бригадата, но когато дупката идва
            # от ЧАКАНЕ на друга мрежа, тя е дълга — и предходното действие се
            # разливаше върху нея.  Мерено на Тръстеник: „Монтаж на арматури"
            # на клон от 54 м стана 169 ДНИ, а клонът се влачеше до ден 327.
            # Изпълнителят го видя веднага: „Как така клон с 62 м дължина ще
            # се работи до 327 ден?!"
            #
            # Затова разтягането има таван: действие не може да порасне повече
            # от двойно, нито с повече от пет дни над сметнатото.  Каквото
            # остане, си остава ПРАЗНИНА — тя е истинска и по-честна от
            # действие, което твърди работа, каквато няма.
            сметнато = int(продължителност)
            таван_на_ръст = max(сметнато, _ТАВАН_НА_РАЗТЯГАНЕТО)
            нов_край = min(нов_край, край + таван_на_ръст)
            if нов_край <= край:
                continue
            t.setdefault("computed_duration", int(продължителност))
            t["duration"] = int(продължителност) + (нов_край - край)
            t["end_day"] = нов_край
            t["continuous_fill"] = нов_край - край
            затворени += нов_край - край
            разтегнати += 1

    върнати = _свий_до_наличния_ресурс(tasks)
    затворени -= върнати

    бележки: list[str] = []
    if затворени > 0:
        бележки.append(
            f"Непрекъсната работа: затворени {затворени} празни дни в "
            f"{разтегнати} действия — бригадата на обекта работи без паузи, "
            "както в човешките графици.  Датите и срокът не се менят.")
        logger.info("%s", бележки[0])
    if върнати:
        бележки.append(
            f"{върнати} дни от празнините ОСТАВАТ отворени: разтягането щеше "
            "да иска повече хора или машини, отколкото обектът има обявени.")
        logger.info("%s", бележки[-1])
    return tasks, бележки


def _свий_до_наличния_ресурс(tasks: list[dict], опити: int = 6) -> int:
    """Връща разтягането там, където то би поискало ресурс над наличния.

    Затварянето на празнина държи бригадата на място по-дълго — а точно
    заетостта на общия ресурс често е причината за празнината.  Мерено на
    Тръстеник: „Строителен работник (съоръжения)" излизаше 6 при налични 3,
    защото двата водоема се разтегнаха един върху друг.

    Затова разтегнатото се подрязва обратно, ден по ден, докато проверката за
    претоварване замълчи.  Празнина, която ресурсът не позволява да се
    затвори, си остава празнина — и се ОБЯВЯВА, вместо да се скрие.
    """
    from src.schedule_diagnostics import _capacity_overloads

    върнати = 0
    for _ in range(опити):
        претоварени = _capacity_overloads(tasks)
        if not претоварени:
            break
        дни = {int(о.get("day")) for о in претоварени if о.get("day") is not None}
        подрязано = False
        for t in tasks or []:
            запълнено = int(_as_number(t.get("continuous_fill")) or 0)
            if запълнено <= 0:
                continue
            край = int(_as_number(t.get("end_day")) or 0)
            начало_на_запълненото = край - запълнено + 1
            удари = [д for д in дни if начало_на_запълненото <= д <= край]
            if not удари:
                continue
            нов_край = min(удари) - 1
            назад = край - нов_край
            t["end_day"] = нов_край
            t["duration"] = int(_as_number(t.get("duration")) or 0) - назад
            t["continuous_fill"] = запълнено - назад
            if t["continuous_fill"] <= 0:
                t.pop("continuous_fill", None)
            върнати += назад
            подрязано = True
        if not подрязано:
            break
    return върнати
