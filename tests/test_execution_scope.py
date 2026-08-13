"""Unit tests: прикачената работа не се изпълнява втори път.

ОДИТ 13.08.2026, P0.2: „SVO/SKO/УО/кожуси се разгъват като пълни pipe/structure
chains и дублират операции, които вече съществуват в основните templates...
Quantity conservation не гарантира execution-scope conservation."

Гейтът за количествата пази СБОРА: 174 бр. СВО се разпределят точно веднъж и
той свети зелено.  Но седем СВО пакета се разгъваха по цялата 9-степенна
водопроводна верига — ВОБД, разкъртване, изкоп, заваряване, изпитване,
дезинфекция — при положение че стъпка 8 на самия водопроводен участък вече
съдържа „реконструкция на СВО".  Тоест работата се изпълнява два пъти, а
количеството е налице един път.

FAILURE означава: графикът пак ще плаща изкоп и изпитване по два пъти за един
и същ обхват, а количественият гейт ще мълчи.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    execution_scope_duplicates,
    expand_packages,
    load_chains,
)


def _пакет(id_: str, name: str, chain: str, клас: str = "laying"):
    return SpatialWorkPackage(
        id=id_, network="В" if chain.startswith("water") else "К", chain=chain,
        name=name,
        items=(PackageItem(source_ref="КСС!9", activity_class=клас,
                           quantity=12.0, unit="бр"),))


class TestAttachmentDoesNotRepeatTheSection:
    def test_svo_package_gets_only_its_own_steps(self):
        chains = load_chains()
        пълна = len(chains["chains"]["water_section"]["steps"])

        резултат = expand_packages(
            [_пакет("P1", "СВО — ул. Хортензия", "water_section")], chains)
        задачи = [t for t in резултат.tasks
                  if t.get("parent_id") == "P1" and not t.get("is_summary")]

        assert 0 < len(задачи) < пълна, (
            f"СВО пакетът роди {len(задачи)} задачи при {пълна} в пълната верига "
            "— технологията се изпълнява втори път")

    def test_a_real_section_still_gets_the_whole_chain(self):
        """Стесняването важи САМО за прикачена работа."""
        chains = load_chains()
        пълна = len(chains["chains"]["water_section"]["steps"])

        резултат = expand_packages(
            [_пакет("P2", "Кл. В4-1: бул. Рожен", "water_section")], chains)
        задачи = [t for t in резултат.tasks
                  if t.get("parent_id") == "P2" and not t.get("is_summary")]

        assert len(задачи) >= 1
        # Участъкът не бива да е стеснен до една стъпка като СВО.
        assert len(задачи) > 1 or пълна == 1


class TestDuplicateDetector:
    def test_it_names_the_packages_that_repeat_execution(self):
        chains = load_chains()
        дубликати = execution_scope_duplicates(
            [_пакет("P1", "СВО — ул. Хортензия", "water_section"),
             _пакет("P2", "СКО — ул. Черковна", "sewer_section"),
             _пакет("P3", "Кл. В4-1: бул. Рожен", "water_section")], chains)

        имена = {d["package"] for d in дубликати}
        assert имена == {"P1", "P2"}, \
            "или пропуска прикачена работа, или маркира истински участък"

    def test_it_reports_how_much_is_repeated(self):
        chains = load_chains()
        (запис,) = execution_scope_duplicates(
            [_пакет("P1", "СВО — ул. Хортензия", "water_section")], chains)

        assert запис["emitted_tasks"] > запис["steps_that_are_its_own"] > 0

    def test_after_the_fix_the_emitted_schedule_is_clean(self):
        """Детекторът мери породеното, не конфигурацията.

        Иначе би светил червено и след като дублирането е премахнато — тоест
        не би мерил нищо.
        """
        chains = load_chains()
        пакети = [_пакет("P1", "СВО — ул. Хортензия", "water_section"),
                  _пакет("P2", "Кл. В4-1: бул. Рожен", "water_section")]
        задачи = expand_packages(пакети, chains).tasks

        assert execution_scope_duplicates(пакети, chains, задачи) == []


class TestGateMatchesTheModel:
    """Гейтът за пълнота трябва да иска това, което пакетът наистина е.

    СЕРИЯ 14.08.2026: след като прикачената работа спря да се разгъва като цял
    участък, `template_complete` падна в 14 от 30 реални прогона — гейтът все
    още искаше цялата верига от пакет, който нарочно ражда само своите стъпки.
    Чистите паднаха от 16/40 на 6/40 заради собствената ни поправка.

    FAILURE означава: премахването на дублирането пак ще се брои за дефект.
    """

    def test_an_attachment_package_satisfies_the_template(self):
        from src.schedule_diagnostics import structural_flags

        chains = load_chains()
        пакети = [_пакет("P1", "СВО — ул. Хортензия", "water_section"),
                  _пакет("P2", "Кл. В4-1: бул. Рожен", "water_section")]
        задачи = expand_packages(пакети, chains).tasks

        флагове = structural_flags(задачи, packages=пакети, chains=chains)
        assert флагове["template_complete"] is True, (
            "гейтът иска от прикачената работа стъпки, които не са нейни")

    def test_a_section_missing_steps_still_fails(self):
        """Стесняването не бива да отваря вратата за истински непълен участък."""
        from src.schedule_diagnostics import structural_flags

        chains = load_chains()
        пакети = [_пакет("P2", "Кл. В4-1: бул. Рожен", "water_section")]
        задачи = [t for t in expand_packages(пакети, chains).tasks
                  if t.get("is_summary") or t.get("chain_step") in (None, "")
                  or list(t.get("chain_step"))[:1] == ["1"]]

        флагове = structural_flags(задачи, packages=пакети, chains=chains)
        assert флагове["template_complete"] is False
