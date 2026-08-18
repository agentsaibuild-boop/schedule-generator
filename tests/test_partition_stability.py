"""Unit tests: разделянето на обекта е СВОЙСТВО, не хвърляне на зара.

ЖИВ ПРОГОН 14.08.2026: четири последователни опита дадоха 36, 28, 11 и 33
участъка за един и същ обект.  Обектът е един — променя се само едрината, която
моделът избира наново на всеки опит.  11 е познатото израждане: по един пакет
на ДИАМЕТЪР, тоест старото групиране по редове, наречено „участъци".

Затова: (1) разделянето се проверява детерминистично — участък е ТРАСЕ между
два възела, а не купчина от цял ред на КСС; (2) негодното разделяне се пита
още веднъж, с казано какво не е наред; (3) следващият опит научава от провала
на предишния, вместо да започва от нулата.

FAILURE означава: едрината на графика пак ще зависи от късмета на опита.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.chat_handler import ChatHandler  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402
from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    partition_diagnosis,
)


class _Ред:
    """Ред от КСС — само каквото диагнозата ползва."""

    def __init__(self, ref, quantity, unit="м", description="Тръби PE DN315"):
        self.ref = ref
        self.quantity = quantity
        self.unit = unit
        self.description = description


def _пакет(pid, *, items, network="К", name="", start="", end=""):
    # Пакет, на който този файл ДАВА двойка възли, е такъв с прочетена
    # геометрия — иначе възлите не биха съществували.  Казва се изрично,
    # защото от 18.08.2026 непотвърдените възли не напускат програмата и
    # мълчаливото подразбиране би направило тестовете тук неверни по премиса.
    return SpatialWorkPackage(
        id=pid, network=network, chain="sewer_section", name=name,
        start_node=start, end_node=end,
        spatial_verified=bool(start and end),
        items=tuple(PackageItem(source_ref=r, activity_class="laying", quantity=q)
                    for r, q in items))


class TestDegeneratePartitionIsRecognised:
    def test_whole_rows_in_one_package_each_is_not_a_split(self):
        """По един пакет на ДИАМЕТЪР — точно случаят с 11-те „участъка"."""
        index = [_Ред("КСС!1", 1200.0), _Ред("КСС!2", 800.0), _Ред("КСС!3", 640.0)]
        packages = [
            _пакет("P1", items=[("КСС!1", 1200.0)], name="Цялата мрежа DN315"),
            _пакет("P2", items=[("КСС!2", 800.0)], name="Цялата мрежа DN400"),
            _пакет("P3", items=[("КСС!3", 640.0)], name="Цялата мрежа DN500"),
        ]
        диагноза = partition_diagnosis(packages, index)
        assert диагноза["ok"] is False
        assert "трасета" in диагноза["prompt_note"]

    def test_a_row_split_between_sections_is_a_real_partition(self):
        index = [_Ред("КСС!1", 1200.0), _Ред("КСС!2", 800.0)]
        packages = [
            _пакет("P1", items=[("КСС!1", 400.0), ("КСС!2", 300.0)],
                   name="кл. 48 от РШ 36 до РШ 37", start="РШ 36", end="РШ 37"),
            _пакет("P2", items=[("КСС!1", 500.0), ("КСС!2", 500.0)],
                   name="кл. 48 от РШ 37 до РШ 38", start="РШ 37", end="РШ 38"),
            _пакет("P3", items=[("КСС!1", 300.0)],
                   name="кл. 49 от РШ 38 до РШ 39", start="РШ 38", end="РШ 39"),
        ]
        assert partition_diagnosis(packages, index)["ok"] is True

    def test_small_rows_may_honestly_live_in_one_section(self):
        """Шест крана в един участък не са израждане."""
        index = [_Ред("КСС!1", 6.0, unit="бр.", description="Спирателен кран")]
        packages = [_пакет("P1", items=[("КСС!1", 6.0)], name="кл. 1 от ОТ 1 до ОТ 2")]
        assert partition_diagnosis(packages, index)["ok"] is True

    def test_fewer_packages_than_drawn_segments_is_a_signal(self):
        index = [_Ред("КСС!1", 100.0)]
        packages = [_пакет("P1", items=[("КСС!1", 100.0)], name="кл. 1")]
        segments = [
            {"start_node": "РШ 1", "end_node": "РШ 2"},
            {"start_node": "РШ 2", "end_node": "РШ 3"},
            {"start_node": "РШ 3", "end_node": "РШ 4"},
        ]
        диагноза = partition_diagnosis(packages, index, segments)
        assert диагноза["ok"] is False
        assert диагноза["drawn_segments"] == 3
        assert any("чертеж" in s for s in диагноза["signals"])

    def test_two_sections_with_the_same_name_are_not_two_sections(self):
        index = [_Ред("КСС!1", 1000.0)]
        packages = [
            _пакет("P1", items=[("КСС!1", 500.0)], name="кл. 48 от РШ 1 до РШ 2"),
            _пакет("P2", items=[("КСС!1", 500.0)], name="кл. 48 от РШ 1 до РШ 2"),
        ]
        диагноза = partition_diagnosis(packages, index)
        assert диагноза["ok"] is False
        assert any("име" in s for s in диагноза["signals"])


class TestChoosingBetweenTwoPartitions:
    def test_fewer_broken_signals_wins(self):
        по_добро = AIProcessor._better_partition(
            {"signals": [], "packages": 12}, {"signals": ["x"], "packages": 30})
        assert по_добро is True

    def test_on_equal_signals_the_finer_split_wins(self):
        """Еталонът е 46 участъка, израждането — един на диаметър."""
        assert AIProcessor._better_partition(
            {"signals": ["x"], "packages": 30},
            {"signals": ["x"], "packages": 11}) is True

    def test_a_worse_second_answer_does_not_replace_the_first(self):
        assert AIProcessor._better_partition(
            {"signals": ["x", "y"], "packages": 40},
            {"signals": ["x"], "packages": 11}) is False


class _Роутер:
    """Фалшив роутер с ПОРЕДИЦА от отговори — по един на питане."""

    deepseek_available = True
    worker_is_claude = False

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def chat(self, messages, system_prompt, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return {"content": json.dumps(payload, ensure_ascii=False),
                "model": "fake", "cost": 0.0}


ТРЪБИ = QuantityRow("Тръби PP DN300", 1000.0, "м",
                    SourceRef("КСС.xlsx", "Канализация", 11), {})
ТРЪБИ_400 = QuantityRow("Тръби PP DN400", 800.0, "м",
                        SourceRef("КСС.xlsx", "Канализация", 12), {})
КСС = [ТРЪБИ, ТРЪБИ_400]

#: По един пакет на ДИАМЕТЪР — израждането от живия прогон.  Имената са
#: правдоподобни (иначе пакетът пада още при парсването); дефектът е, че всеки
#: ред стои ЦЯЛ в един участък.
ИЗРОДЕНО = {"packages": [
    {"id": "D1", "name": "кл. 1 от РШ 1 до РШ 2 (DN300)",
     "items": [{"source_ref": ТРЪБИ.ref, "quantity": 1000}]},
    {"id": "D2", "name": "кл. 2 от РШ 3 до РШ 4 (DN400)",
     "items": [{"source_ref": ТРЪБИ_400.ref, "quantity": 800}]},
]}

ПО_ТРАСЕТА = {"packages": [
    {"id": "K1", "name": "кл. 1 от РШ 1 до РШ 2",
     "items": [{"source_ref": ТРЪБИ.ref, "quantity": 400},
               {"source_ref": ТРЪБИ_400.ref, "quantity": 300}]},
    {"id": "K2", "name": "кл. 2 от РШ 2 до РШ 3",
     "items": [{"source_ref": ТРЪБИ.ref, "quantity": 600},
               {"source_ref": ТРЪБИ_400.ref, "quantity": 500}]},
]}


class TestTheGateAsksAgainInsteadOfBuildingOnSand:
    def test_a_degenerate_split_is_questioned_once_more(self, monkeypatch):
        monkeypatch.setenv("PARTITION_RETRIES", "1")
        роутер = _Роутер(ИЗРОДЕНО, ПО_ТРАСЕТА)
        proc = AIProcessor(router=роутер, knowledge_manager=None)

        изход = proc.generate_packages({"analysis": "{}"}, КСС)

        assert len(роутер.calls) == 2, "негодното разделяне мина без въпрос"
        второ = роутер.calls[1]["messages"][0]["content"]
        assert "НЕ Е ГОДНО" in второ, "моделът не разбра какво не е наред"
        assert изход["partition_diagnosis"]["ok"] is True
        assert [p.id for p in изход["packages"] if not p.id.startswith("ФАЗА_")] \
            == ["K1", "K2"]

    def test_a_sound_split_is_not_questioned(self, monkeypatch):
        monkeypatch.setenv("PARTITION_RETRIES", "1")
        роутер = _Роутер(ПО_ТРАСЕТА)
        proc = AIProcessor(router=роутер, knowledge_manager=None)

        изход = proc.generate_packages({"analysis": "{}"}, КСС)

        assert len(роутер.calls) == 1
        assert изход["partition_diagnosis"]["ok"] is True

    def test_a_worse_second_answer_is_not_taken(self, monkeypatch):
        """Второто питане може да е още по-лошо — тогава остава първото."""
        monkeypatch.setenv("PARTITION_RETRIES", "1")
        по_лошо = {"packages": [           # цели редове И еднакви имена
            {"id": "X1", "name": "кл. 1 от РШ 1 до РШ 2",
             "items": [{"source_ref": ТРЪБИ.ref, "quantity": 1000}]},
            {"id": "X2", "name": "кл. 1 от РШ 1 до РШ 2",
             "items": [{"source_ref": ТРЪБИ_400.ref, "quantity": 800}]},
        ]}
        роутер = _Роутер(ИЗРОДЕНО, по_лошо)
        proc = AIProcessor(router=роутер, knowledge_manager=None)

        изход = proc.generate_packages({"analysis": "{}"}, КСС)

        assert [p.id for p in изход["packages"] if not p.id.startswith("ФАЗА_")] \
            == ["D1", "D2"]

    def test_the_previous_attempt_feedback_reaches_the_prompt(self):
        роутер = _Роутер(ПО_ТРАСЕТА)
        proc = AIProcessor(router=роутер, knowledge_manager=None)

        proc.generate_packages({"analysis": "{}"}, КСС,
                               feedback="3 реда останаха непокрити — КСС!9")

        промпт = роутер.calls[0]["messages"][0]["content"]
        assert "КСС!9" in промпт and "ПРЕДИШНИЯТ ОПИТ" in промпт


class TestTheNextAttemptLearnsFromThisOne:
    def test_the_feedback_names_the_uncovered_rows(self):
        текст = ChatHandler._feedback_for_next_attempt(
            {"packages": [1, 2, 3],
             "citation_report": {"uncovered": ["КСС!9", "КСС!12"]}}, 1)
        assert "КСС!9" in текст and "3 участъка" in текст

    def test_the_feedback_names_the_partition_signals(self):
        текст = ChatHandler._feedback_for_next_attempt(
            {"packages": [1],
             "partition_diagnosis": {"signals": ["по един пакет на диаметър"]}}, 2)
        assert "по един пакет на диаметър" in текст

    def test_the_feedback_names_the_unallocated_rows(self):
        текст = ChatHandler._feedback_for_next_attempt(
            {"packages": [1],
             "conservation": {"ok": False, "missing": ["КСС!4"],
                              "over": {"КСС!7": {}}}}, 1)
        assert "КСС!4" in текст and "КСС!7" in текст

    def test_a_clean_result_says_nothing_about_problems(self):
        текст = ChatHandler._feedback_for_next_attempt(
            {"packages": [1, 2], "conservation": {"ok": True}}, 1)
        assert текст.strip() == "Опит 1 раздели обекта на 2 участъка."


class TestBatchesWithoutGeometryAreStillDistinct:
    """Без потвърдени възли пакетите по един клон се именуват като ЕТАПИ.

    Иначе и двата се свиват до „кл. 48" — а еднаквите имена са признак за
    изродено разделяне, тоест поправката срещу съчинените възли би вдигнала
    фалшива тревога срещу самата себе си.
    """

    def test_two_batches_on_one_branch_do_not_collide(self):
        from src.work_package import number_execution_batches

        пакети = number_execution_batches([
            _пакет("P1", items=[("КСС!1", 400.0)], name="кл. 48 от РШ 36 до РШ 37"),
            _пакет("P2", items=[("КСС!1", 500.0)], name="кл. 48 от РШ 37 до РШ 38"),
        ])

        имена = [p.label for p in пакети]
        assert имена == ["кл. 48 — етап 1 от 2", "кл. 48 — етап 2 от 2"]
        assert not any("РШ" in име for име in имена)

    def test_such_a_partition_is_not_called_degenerate(self):
        from src.work_package import number_execution_batches

        index = [_Ред("КСС!1", 1200.0), _Ред("КСС!2", 800.0)]
        пакети = number_execution_batches([
            _пакет("P1", items=[("КСС!1", 400.0), ("КСС!2", 300.0)],
                   name="кл. 48 от РШ 36 до РШ 37"),
            _пакет("P2", items=[("КСС!1", 500.0), ("КСС!2", 500.0)],
                   name="кл. 48 от РШ 37 до РШ 38"),
            _пакет("P3", items=[("КСС!1", 300.0)],
                   name="кл. 49 от РШ 38 до РШ 39"),
        ])

        assert partition_diagnosis(пакети, index)["ok"] is True
