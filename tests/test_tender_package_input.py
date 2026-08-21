"""Тръжният пакет влиза в приложението такъв, какъвто го дава възложителят.

Измерено на 21.08.2026 върху реалната процедура: четецът на чертежи работеше,
но приложението НЕ МУ ПОДАВАШЕ НИЩО.  Три отделни причини, всяка достатъчна
сама по себе си да убие целия път:

    АРХИВИ      чертежите идват в `РПИП-2_Илиянци_Канал.zip` и `…_Водос.zip`;
                `.zip` не беше поддържано разширение и никой не разархивираше
    ИМЕНА       ключовата дума беше „ситуация", а файловете се казват „СИТ_" —
                нито един от десетте чертежа не се разпознаваше
    СРОК        екипите се оразмеряват по договорния срок, но той се четеше
                само от ред в количествената сметка, какъвто НЯМА нито в нея,
                нито в спецификацията → 0 дни, 2 екипа по подразбиране, и
                график от 1034 дни при 780 по договор

FAILURE означава: човекът пак ще трябва да разархивира на ръка, да преименува
чертежите или да гледа как срокът излиза какъвто се получи вместо какъвто е
договорен.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_manager import FileManager  # noqa: E402
from src.tender_parameters import (  # noqa: E402
    contract_days, describe, for_this_run, sub_project)


# ---------------------------------------------------------------------------
# 1. Архивите се отварят сами
# ---------------------------------------------------------------------------

def _архив(път: Path, членове: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(път, "w") as z:
        for име, съдържание in членове.items():
            z.writestr(име, съдържание)
    return път


def test_zip_contents_become_project_files(tmp_path: Path):
    """Каквото е в архива, се вижда като файл на проекта."""
    _архив(tmp_path / "РПИП-2_Илиянци_Канал.zip", {
        "РПИП-2_Илиянци_Канал/5_СИТ_ИНВЕСТИЦИИ_R.pdf": b"%PDF-1.4 ",
        "РПИП-2_Илиянци_Канал/ТЕХН_СПЕЦ.docx": b"PK",
    })

    имена = {f.name for f in FileManager(str(tmp_path))._list_supported_files()}

    assert "5_СИТ_ИНВЕСТИЦИИ_R.pdf" in имена
    assert "ТЕХН_СПЕЦ.docx" in имена


def test_extraction_is_flat(tmp_path: Path):
    """Дървото на архива не се пресъздава.

    Windows къса пътя на 260 знака, а тръжните архиви носят папка със
    собственото си име плюс подпапки — цял архив не се вадеше.  За
    класификацията е важно ИМЕТО, не къде е било вътре.
    """
    _архив(tmp_path / "пакет.zip",
           {"пакет/DWG_PDF/дълга/подпапка/чертеж.pdf": b"%PDF-1.4 "})

    файлове = FileManager(str(tmp_path))._list_supported_files()
    изваден = next(f for f in файлове if f.name == "чертеж.pdf")

    assert изваден.parent.name == "пакет"


def test_unsupported_members_are_left_alone(tmp_path: Path):
    """От архива се вади само това, което приложението може да чете."""
    _архив(tmp_path / "пакет.zip", {"чертеж.dwg": b"AC10", "опис.pdf": b"%PDF"})

    имена = {f.name for f in FileManager(str(tmp_path))._list_supported_files()}

    assert имена == {"опис.pdf"}


def test_a_member_pointing_outside_is_refused(tmp_path: Path):
    """Архив не бива да пише където си иска („zip slip")."""
    _архив(tmp_path / "лош.zip", {"../избягал.pdf": b"%PDF"})

    FileManager(str(tmp_path))._list_supported_files()

    assert not (tmp_path.parent / "избягал.pdf").exists()


def test_extraction_happens_once(tmp_path: Path):
    """Втори прочит не вади наново — иначе всяко изброяване чака архива."""
    _архив(tmp_path / "пакет.zip", {"опис.pdf": b"%PDF"})
    fm = FileManager(str(tmp_path))

    assert len(fm._extract_archives()) == 1
    assert fm._extract_archives() == []


def test_a_half_extracted_archive_is_retried(tmp_path: Path):
    """Прекъснато вадене не бива да минава за готово.

    Без маркер за завършеност половината файлове оставаха невидими завинаги —
    точно това стана при първия опит с водопроводния архив.
    """
    _архив(tmp_path / "пакет.zip", {"опис.pdf": b"%PDF"})
    fm = FileManager(str(tmp_path))
    fm._extract_archives()

    маркер = next((tmp_path / "разархивирано" / "пакет").glob(".разархивирано*"))
    маркер.unlink()

    assert fm._extract_archives(), "без маркер архивът трябва да се пробва пак"


# ---------------------------------------------------------------------------
# 2. Чертежите се разпознават по истинските си имена
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("име", [
    "5_СИТ_ИНВЕСТИЦИИ_R.pdf",
    "25086_В_01_СИТ.1500_норм.раб.pdf",
    "3_СИТ_ОРАЗМ.ДАННИ_М1000_R3.pdf",
    "Prilojenie_ILIYANTSI_INVESTICIA_I_WATER SUPPLY.pdf",
    "ПР.3.SITUACIA_VODOPR_IIvar_1500_ИП_БУЛ.РОЖЕН.pdf",
    "1_СИТУАЦИЯ_ОУП M25 000_R3.pdf",
])
def test_real_drawing_names_are_recognised(tmp_path: Path, име: str):
    (tmp_path / име).write_bytes(b"%PDF-1.4 ")

    класификация = FileManager(str(tmp_path)).classify_files()

    assert класификация["situation"] == [име]


@pytest.mark.parametrize("име", [
    "Депозит.pdf",              # „сит" вътре в дума
    "Композитни материали.docx",
    "Ревизит на обекта.txt",
])
def test_the_word_inside_another_word_is_not_a_drawing(tmp_path: Path, име: str):
    """„сит" се търси слепено с разделител, не голо."""
    (tmp_path / име).write_bytes(b"x" * 20)

    класификация = FileManager(str(tmp_path)).classify_files()

    assert класификация["situation"] == []


# ---------------------------------------------------------------------------
# 3. Договорният срок се ПИТА, защото в сметката го няма
# ---------------------------------------------------------------------------

def test_contract_days_default_to_unknown(monkeypatch):
    monkeypatch.delenv("CONTRACT_DAYS", raising=False)

    assert contract_days() == 0


def test_declared_contract_days_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("CONTRACT_DAYS", "500")

    with for_this_run({"contract_days": 660}):
        assert contract_days() == 660


def test_nonsense_contract_days_are_ignored(monkeypatch):
    monkeypatch.setenv("CONTRACT_DAYS", "скоро")

    assert contract_days() == 0


def test_a_missing_deadline_is_said_out_loud(monkeypatch):
    """Мълчаливото падане към „2 екипа" беше причината срокът да е случаен."""
    monkeypatch.delenv("CONTRACT_DAYS", raising=False)

    assert any("НЕ Е ОБЯВЕН" in ред for ред in describe())


def test_the_declared_deadline_is_shown(monkeypatch):
    monkeypatch.delenv("CONTRACT_DAYS", raising=False)

    with for_this_run({"contract_days": 660}):
        assert any("660 дни" in ред for ред in describe())


# ---------------------------------------------------------------------------
# 4. Подобектът: проектът често е по-голям от процедурата
# ---------------------------------------------------------------------------

def test_sub_project_defaults_to_everything(monkeypatch):
    monkeypatch.delenv("SUB_PROJECT", raising=False)

    assert sub_project() == ""


def test_declared_sub_project_is_shown(monkeypatch):
    monkeypatch.delenv("SUB_PROJECT", raising=False)

    with for_this_run({"sub_project": "И"}):
        assert sub_project() == "И"
        assert any("подобект" in ред.lower() for ред in describe())
