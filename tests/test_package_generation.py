"""Unit tests: генерацията връща ПАКЕТИ, не готови задачи.

СЪПОСТАВКА С ЕТАЛОН (2026-08-06/07): човешкият график е организиран в 23
водопроводни и 46 канализационни ПАКЕТА — реални трасета между два възела.
Нашият модел връщаше плосък списък задачи, групиран по диаметър, и оттам
идваха двата структурни дефекта: клонирани количества по фронтове и настилки
преди изкопа под тях.

ТРУСТ ГРАНИЦА: моделът казва само кой пакет съществува и колко от кой ред му
се пада.  Класът на дейността се извежда от ОПИСАНИЕТО на цитирания ред — ако
моделът можеше да го обяви сам, той щеше да може да накара грешна работа да
покрие ред от КСС.

FAILURE означава: моделът пак може да вкара в графика работа, която не е в
КСС, или количество, което никой не е разпределил.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor, build_packages_response_schema  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402
from src.work_package import packages_from_ai  # noqa: E402


@pytest.fixture(autouse=True)
def пита_модела(monkeypatch):
    """Този файл проверява ПЪТЯ ПРЕЗ МОДЕЛА и трябва да го каже изрично.

    От 18.08.2026 без авторитетна геометрия моделът НЕ се пита изобщо:
    участъците ги прави `src/execution_batches.py`, защото КСС не съдържа
    разчленяване, а 30 живи прогона дадоха 22–132 пакета за един и същ вход.
    Пътят през модела остава за случая с ПРОЧЕТЕНА геометрия и за аварийно
    връщане с `DETERMINISTIC_BATCHES=0` — и точно него описва този файл.
    """
    monkeypatch.setenv("DETERMINISTIC_BATCHES", "0")

def _row(row: int, qty: float, desc: str, unit: str = "м",
         sheet: str = "Канализация") -> QuantityRow:
    return QuantityRow(desc, qty, unit, SourceRef("КСС.xlsx", sheet, row), {})


PIPES = _row(11, 1000.0, "Тръби PP DN300")
KERBS = _row(12, 7761.0, "Доставка и полагане на бетонови бордюри", "м", "Пътна")


# ---------------------------------------------------------------------------
# Парсване на пакетите
# ---------------------------------------------------------------------------


def test_split_quantities_are_accepted():
    data = {"packages": [
        {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
         "items": [{"source_ref": PIPES.ref, "quantity": 400}]},
        {"id": "K2", "name": "кл. 2 от РШ 2 до РШ 3",
         "items": [{"source_ref": PIPES.ref, "quantity": 600}]},
    ]}

    packages, errors = packages_from_ai(data, boq_index=[PIPES])

    assert [p.id for p in packages] == ["K1", "K2"]
    assert sum(i.quantity for p in packages for i in p.items) == pytest.approx(1000.0)
    assert errors == []


def test_activity_class_comes_from_the_cited_row_not_the_model():
    """Моделът не бива да може да обяви „това е полагане" — иначе покрива каквото си иска."""
    data = {"packages": [{
        "id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
        "items": [{"source_ref": KERBS.ref, "quantity": 7761,
                   "activity_class": "laying"}],   # ← опит за самоопределяне
    }]}

    packages, _ = packages_from_ai(data, boq_index=[KERBS])

    assert packages[0].items[0].activity_class == "pavement"


def test_network_is_inferred_from_node_type():
    data = {"packages": [
        {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2", "items": []},
        {"id": "V1", "name": "КЛ. 25 от ОТ 27 до ОТ 25", "items": []},
    ]}

    packages, _ = packages_from_ai(data, boq_index=[PIPES])
    by_id = {p.id: p for p in packages}

    assert by_id["K1"].network == "К" and by_id["K1"].chain == "sewer_section"
    assert by_id["V1"].network == "В" and by_id["V1"].chain == "water_section"


def test_invented_citation_is_rejected_not_repaired():
    data = {"packages": [{
        "id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
        "items": [{"source_ref": "КСС.xlsx!Няма!999", "quantity": 100}],
    }]}

    packages, errors = packages_from_ai(data, boq_index=[PIPES])

    assert packages[0].items == ()
    assert any("не е ред от КСС" in e for e in errors)


def test_item_without_citation_is_dropped_and_reported():
    data = {"packages": [{
        "id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
        "items": [{"quantity": 100}],
    }]}

    packages, errors = packages_from_ai(data, boq_index=[PIPES])

    assert packages[0].items == ()
    assert any("без цитат" in e for e in errors)


@pytest.mark.parametrize("bad", [0, -50, "х", None])
def test_non_positive_quantity_is_rejected(bad):
    data = {"packages": [{
        "id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
        "items": [{"source_ref": PIPES.ref, "quantity": bad}],
    }]}

    packages, errors = packages_from_ai(data, boq_index=[PIPES])

    assert packages[0].items == ()
    assert errors


def test_duplicate_package_id_is_rejected():
    data = {"packages": [
        {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
         "items": [{"source_ref": PIPES.ref, "quantity": 500}]},
        {"id": "K1", "name": "кл. 9 от РШ 8 до РШ 9",
         "items": [{"source_ref": PIPES.ref, "quantity": 500}]},
    ]}

    packages, errors = packages_from_ai(data, boq_index=[PIPES])

    assert len(packages) == 1
    assert any("повторен идентификатор" in e for e in errors)


def test_unknown_chain_is_rejected():
    data = {"packages": [{"id": "X1", "name": "нещо без възли",
                          "chain": "измислена", "items": []}]}

    packages, errors = packages_from_ai(data, boq_index=[PIPES])

    assert packages == []
    assert any("непозната верига" in e for e in errors)


def test_garbage_response_yields_no_packages():
    packages, errors = packages_from_ai({"tasks": []}, boq_index=[PIPES])
    assert packages == [] and errors


# ---------------------------------------------------------------------------
# Схемата
# ---------------------------------------------------------------------------


def test_schema_requires_packages_with_citations():
    schema = build_packages_response_schema()
    pkg = schema["properties"]["packages"]["items"]

    assert schema["required"] == ["packages"]
    assert set(pkg["required"]) == {"id", "name", "items"}
    assert pkg["properties"]["items"]["items"]["required"] == ["source_ref", "quantity"]


def test_schema_declares_every_package_field():
    """Строгите provider-и трият недекларираните полета (виж schema-та за задачи)."""
    props = build_packages_response_schema()[
        "properties"]["packages"]["items"]["properties"]

    assert {"start_node", "end_node", "chainage_from", "chainage_to",
            "dn", "material", "network", "street", "branch"} <= set(props)


# ---------------------------------------------------------------------------
# Целият пакетен път — с фалшив модел
# ---------------------------------------------------------------------------


class _FakeRouter:
    """Връща предварително подготвен пакетен отговор."""

    deepseek_available = True
    worker_is_claude = False

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    def chat(self, messages, system_prompt, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        return {"content": json.dumps(self.payload, ensure_ascii=False),
                "model": "fake", "cost": 0.0}


def _processor(payload: dict) -> tuple[AIProcessor, _FakeRouter]:
    router = _FakeRouter(payload)
    return AIProcessor(router=router, knowledge_manager=None), router


def test_package_path_produces_wbs_and_critical_path():
    payload = {"packages": [
        {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2", "dn": 300, "material": "PP",
         "street": "ул. Първа",
         "items": [{"source_ref": PIPES.ref, "quantity": 400}]},
        {"id": "K2", "name": "кл. 2 от РШ 2 до РШ 3", "dn": 300, "material": "PP",
         "street": "ул. Втора",
         "items": [{"source_ref": PIPES.ref, "quantity": 600}]},
    ]}
    proc, _ = _processor(payload)

    out = proc.generate_packages({"analysis": "{}"}, [PIPES], num_teams=2)

    assert out["status"] == "ok", out.get("blockers")
    assert out["conservation"]["ok"] is True
    assert any(t.get("is_summary") for t in out["tasks"]), "липсва WBS"
    assert out["critical_count"] > 0, "критичният път пак е празен"
    # Договорните фази (мобилизация, приемане) НЕ са работа на бригада и
    # затова нямат фронт — разпределят се само пространствените участъци.
    spatial = [p for p in out["packages"] if not p.id.startswith("ФАЗА_")]
    assert {p.front for p in spatial} == {"Фронт 1", "Фронт 2"}
    assert any(p.id.startswith("ФАЗА_") for p in out["packages"]), \
        "договорният обхват липсва"


def test_cloned_quantities_block_the_package_path():
    """Двата фронта с пълното количество — дефектът от реалния прогон."""
    payload = {"packages": [
        {"id": "F1", "name": "кл. 1 от РШ 1 до РШ 2",
         "items": [{"source_ref": KERBS.ref, "quantity": 7761}]},
        {"id": "F2", "name": "кл. 2 от РШ 2 до РШ 3",
         "items": [{"source_ref": KERBS.ref, "quantity": 7761}]},
    ]}
    proc, _ = _processor(payload)

    out = proc.generate_packages({"analysis": "{}"}, [KERBS])

    assert out["status"] == "needs_human_review"
    assert out["conservation"]["ok"] is False
    assert any("ПРЕВИШЕНО" in b for b in out["blockers"])


def test_unallocated_row_blocks_the_package_path():
    payload = {"packages": [
        {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
         "items": [{"source_ref": PIPES.ref, "quantity": 1000}]},
    ]}
    proc, _ = _processor(payload)

    out = proc.generate_packages({"analysis": "{}"}, [PIPES, KERBS])

    assert out["status"] == "needs_human_review"
    assert any("НЕРАЗПРЕДЕЛЕН" in b for b in out["blockers"])


def test_package_path_requires_a_boq_index():
    proc, _ = _processor({"packages": []})
    out = proc.generate_packages({"analysis": "{}"}, [])
    assert out["status"] == "error"


def test_prompt_forbids_inventing_durations_and_demands_the_split():
    proc, router = _processor({"packages": [
        {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
         "items": [{"source_ref": PIPES.ref, "quantity": 1000}]}]})

    proc.generate_packages({"analysis": "{}"}, [PIPES])
    prompt = router.calls[0]["messages"][0]["content"]

    assert "НЕ измисляй дейности, продължителности" in prompt
    assert "НИКОГА не давай пълното количество на повече от един пакет" in prompt
    assert router.calls[0]["kw"]["response_schema"]["required"] == ["packages"]


# ---------------------------------------------------------------------------
# Включване в pipeline-а — с падане към досегашния път
# ---------------------------------------------------------------------------


class _StubAI:
    def __init__(self, result=None, boom=False):
        self.result = result
        self.boom = boom
        self.calls = 0

    def generate_schedule_packaged(self, analysis, boq_index, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("моделът гръмна")
        return self.result


def _handler(ai):
    from src.chat_handler import ChatHandler
    return ChatHandler(ai_processor=ai)


def _noop(_message):
    pass


def test_package_path_is_used_when_boq_exists():
    ai = _StubAI({"status": "approved", "schedule": {"tasks": []}})
    out = _handler(ai)._try_package_generation(
        {}, [PIPES], num_teams=2, locations=None, progress=_noop)

    assert out is not None and ai.calls == 1


def test_package_path_is_skipped_without_boq():
    ai = _StubAI({"status": "approved"})
    out = _handler(ai)._try_package_generation(
        {}, [], num_teams=1, locations=None, progress=_noop)

    assert out is None and ai.calls == 0


def test_package_path_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("PACKAGE_GENERATION", "0")
    ai = _StubAI({"status": "approved"})
    out = _handler(ai)._try_package_generation(
        {}, [PIPES], num_teams=1, locations=None, progress=_noop)

    assert out is None and ai.calls == 0


def test_exception_falls_back_instead_of_failing_the_run():
    """Нов път не бива да е причина за нула изход."""
    ai = _StubAI(boom=True)
    out = _handler(ai)._try_package_generation(
        {}, [PIPES], num_teams=1, locations=None, progress=_noop)

    assert out is None


def test_error_status_falls_back_too():
    ai = _StubAI({"status": "error", "message": "моделът не върна пакети"})
    out = _handler(ai)._try_package_generation(
        {}, [PIPES], num_teams=1, locations=None, progress=_noop)

    assert out is None


def test_needs_human_review_is_kept_not_discarded():
    """Нарушен инвариант е РЕЗУЛТАТ, не повод да се пробва пак по стария път."""
    ai = _StubAI({"status": "needs_human_review", "schedule": {"tasks": []},
                  "export_blockers": ["ПРЕВИШЕНО количество"]})
    out = _handler(ai)._try_package_generation(
        {}, [PIPES], num_teams=1, locations=None, progress=_noop)

    assert out is not None and out["status"] == "needs_human_review"


# ---------------------------------------------------------------------------
# Спасяване на отрязан OCR отговор (отсечки от ситуационния чертеж)
# ---------------------------------------------------------------------------


def test_salvage_recovers_segments_from_truncated_json():
    """OCR таванът е 4096 токена — голям чертеж отрязва отговора по средата.

    Целият масив става непарсируем, при положение че първите обекти са
    валидни.  Частичен списък отсечки е предложение за именуване, не
    доказателство — по-полезен от нула.
    """
    from src.ai_processor import _salvage_json_objects

    truncated = (
        '{"segments": ['
        '{"branch":"кл. 1","start_node":"РШ 1","end_node":"РШ 2"},'
        '{"branch":"кл. 2","start_node":"РШ 3","end_node":"РШ 4"},'
        '{"branch":"кл'
    )

    salvaged = _salvage_json_objects(truncated)

    assert [s["branch"] for s in salvaged] == ["кл. 1", "кл. 2"]


def test_salvage_ignores_objects_that_are_not_segments():
    from src.ai_processor import _salvage_json_objects

    assert _salvage_json_objects('{"a": 1}') == []
    assert _salvage_json_objects("нищо смислено") == []


def test_salvage_is_not_fooled_by_braces_inside_strings():
    """Скоба в текст на улица не бива да размества броенето."""
    from src.ai_processor import _salvage_json_objects

    text = ('{"segments":[{"street":"ул. {А}","start_node":"РШ 1",'
            '"end_node":"РШ 2"},{"br')

    salvaged = _salvage_json_objects(text)

    assert len(salvaged) == 1
    assert salvaged[0]["street"] == "ул. {А}"


# ===================================================================
# Празният отговор е ЗАСЕЧКА, не резултат (измерено 17.08.2026)
# ===================================================================
#
# Шест от 40 живи прогона свършиха така: работникът връща ~7 изходни токена за
# две секунди — валиден JSON с НУЛА участъка — и прогонът се отчиташе като
# грешка веднага, без нито един повторен опит.  Точно отдолу обаче НЕГОДНОТО
# разделяне се пита още веднъж; по-лошият случай получаваше по-малко търпение
# от по-лекия.
#
# Проверката в `ai_router._request_with_empty_retry` не го хваща: тя гледа за
# празен НИЗ, а тук низът е непразен и се разчита без грешка.  Празнотата е
# смислова, не синтактична.
#
# FAILURE означава: една засечка на доставчика пак ще струва цял прогон.


class _Работник:
    """Връща подадените отговори по ред; брои заявките."""

    def __init__(self, *отговори: str) -> None:
        self._отговори = list(отговори)
        self.calls = 0
        self.deepseek_available = True
        self.anthropic_available = False

    def chat(self, messages, system_prompt, **kwargs) -> dict:
        self.calls += 1
        съдържание = self._отговори[min(self.calls - 1, len(self._отговори) - 1)]
        return {"content": съдържание, "model": "тест",
                "usage": {"input_tokens": 10, "output_tokens": 7},
                "cost": 0.0, "fallback": False, "truncated": False}


ПРАЗЕН = '{"packages": []}'
ГОДЕН = json.dumps({"packages": [{
    "id": "К1", "name": "кл. 1 от РШ 1 до РШ 2", "network": "К",
    "branch": "кл. 1", "start_node": "РШ 1", "end_node": "РШ 2",
    "items": [{"source_ref": "КСС.xlsx!Канализация!11", "quantity": 1000.0}],
}]}, ensure_ascii=False)


def _генерирай(работник, monkeypatch, опити="2"):
    monkeypatch.setenv("EMPTY_PACKAGES_RETRIES", опити)
    monkeypatch.setenv("PARTITION_RETRIES", "0")
    monkeypatch.setenv("PACKAGE_REPAIR_ROUNDS", "0")
    return AIProcessor(router=работник).generate_schedule_packaged(
        {"analysis": "реконструкция"}, [PIPES], num_teams=1)


def test_zero_packages_is_asked_again(monkeypatch):
    работник = _Работник(ПРАЗЕН, ГОДЕН)

    резултат = _генерирай(работник, monkeypatch)

    assert работник.calls >= 2, (
        "нулата участъци мина за отговор — прогонът се губи от една засечка")
    assert резултат.get("status") != "error", резултат.get("message")


def test_it_gives_up_after_the_configured_attempts(monkeypatch):
    """Търпението е с таван — иначе засечка на доставчика върти безкрайно."""
    работник = _Работник(ПРАЗЕН)

    резултат = _генерирай(работник, monkeypatch, опити="2")

    assert работник.calls == 3, f"направени {работник.calls} заявки"
    assert резултат["status"] == "error"


def test_a_good_first_answer_is_not_asked_twice(monkeypatch):
    работник = _Работник(ГОДЕН)

    _генерирай(работник, monkeypatch)

    assert работник.calls == 1, "питаме пак, при положение че отговорът е годен"
