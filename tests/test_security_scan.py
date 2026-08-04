"""Unit tests: Unicode-коректният скан за клиентски термини РАБОТИ.

Одит v19 (P0 сигурност): предишният inline-скан четеше празен stdin (heredoc
крадеше входа) → пропускаше ВСИЧКО.  Освен това `grep -i` в Git Bash не сгъва
кирилски главни букви.  Тези тестове заключват, че скенерът:
  * чете съдържанието на файл ОТ ДИСКА;
  * съвпада независимо от главни/малки кирилски букви (casefold);
  * съвпада при composed/decomposed Unicode (NFC/NFD).

Термините тук са СИНТЕТИЧНИ (не реални клиентски имена) — тестовете доказват
логиката, без сами да изтичат чувствителни названия.

FAILURE означава: PII/клиентски скенерът пак може да пропусне термин.
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.security_scan import main, scan_file, scan_text  # noqa: E402

# синтетичен клиентски маркер — НЕ е реално име
TERM = "Зорницаград"


def _denylist(tmp_path, *terms):
    p = tmp_path / "deny.txt"
    p.write_text("\n".join(terms) + "\n", encoding="utf-8")
    return str(p)


def test_matches_exact():
    assert scan_text(f"обект в {TERM}, ул. Х", [TERM]) == [TERM]


def test_matches_all_caps():
    assert scan_text(TERM.upper(), [TERM]) == [TERM]


def test_matches_lowercase():
    assert scan_text(TERM.lower(), [TERM]) == [TERM]


def test_matches_mixed_case():
    mixed = "ЗоРнИцАгРаД"
    assert scan_text(mixed, [TERM]) == [TERM]


def test_denylist_term_lowercase_still_matches_capitalized_content():
    # denylist пази малки букви, съдържанието е с главна — трябва да съвпадне
    assert scan_text(f"Проект: {TERM}", [TERM.lower()]) == [TERM.lower()]


def test_matches_across_nfc_nfd_normalization():
    # composed (NFC) denylist vs decomposed (NFD) съдържание и обратно
    nfd_body = unicodedata.normalize("NFD", f"обект {TERM}")
    nfc_term = unicodedata.normalize("NFC", TERM)
    assert scan_text(nfd_body, [nfc_term]) == [nfc_term]


def test_no_false_positive_when_absent():
    assert scan_text("обект в Тестоград, ул. Първа", [TERM]) == []


def test_scan_file_reads_from_disk(tmp_path):
    """Регресия за v19 бъга: скенерът трябва да чете САМИЯ ФАЙЛ, не празен stdin."""
    p = tmp_path / "leak.md"
    p.write_text(f"Проект: {TERM.upper()} — метрики", encoding="utf-8")
    assert scan_file(p, [TERM]) == [TERM]


def test_scan_file_binary_is_safe(tmp_path):
    p = tmp_path / "b.bin"
    p.write_bytes(b"\x00\x01\x02\xff\xfe")
    assert scan_file(p, [TERM]) == []


# ===================================================================
# Одит v22: скенерът проверява И PATHNAME-а, не само съдържанието
# ===================================================================

def test_term_in_pathname_string_is_detected():
    """Клиентско име в ИМЕТО на файла (не в съдържанието)."""
    assert scan_text(f"reports/{TERM}_boq.md", [TERM]) == [TERM]
    assert scan_text(f"docs/{TERM.upper()}/plan.md", [TERM]) == [TERM]


def test_main_scans_both_content_and_path(tmp_path, monkeypatch):
    """CLI режимът по подразбиране лови термина И в съдържанието, И в пътя.

    NB: ползваме ОТНОСИТЕЛНИ пътища (chdir) — както реалният pipeline (git дава
    относителни), за да не матчне _PATH_RE самия абсолютен tmp път (под Temp)."""
    dl = _denylist(tmp_path, TERM)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clean_name.md").write_text(f"обект {TERM}", encoding="utf-8")
    assert main(["security_scan.py", dl, "clean_name.md"]) == 2         # съдържание
    d = tmp_path / f"{TERM}_folder"; d.mkdir()
    (d / "plan.md").write_text("нищо чувствително", encoding="utf-8")
    assert main(["security_scan.py", dl, f"{TERM}_folder/plan.md"]) == 2  # в пътя
    (tmp_path / "ok.md").write_text("обект в Тестоград", encoding="utf-8")
    assert main(["security_scan.py", dl, "ok.md"]) == 0                 # чисто


def test_names_only_mode_scans_strings(tmp_path):
    """`--names-only` сканира подадените НИЗОВЕ (pathname-и)."""
    dl = _denylist(tmp_path, TERM)
    assert main(["security_scan.py", "--names-only", dl, f"a/{TERM}/b.md"]) == 2
    assert main(["security_scan.py", "--names-only", dl, "a/clean/b.md"]) == 0


def test_pathname_key_and_machine_path_in_filename_are_caught(tmp_path):
    """Одит v24 P1: token-shaped ИМЕ и машинен-път ИМЕ се ловят (не само в
    съдържанието).  Преди v24 минаваха и през двата CI канала."""
    dl = _denylist(tmp_path, TERM)
    token = "sk-ant-" + "api03-" + "A" * 20
    machine_path = "Users/" + "alice/" + "Desktop/plan.md"   # split → source чист
    assert main(["security_scan.py", "--names-only", dl, f"{token}.txt"]) == 2
    assert main(["security_scan.py", "--names-only", dl, machine_path]) == 2
    # чисто Unicode ИМЕ → минава
    assert main(["security_scan.py", "--names-only", dl, "проект-Тестоград.md"]) == 0


def test_pathname_with_spaces_and_unicode_is_handled(tmp_path, monkeypatch):
    """Attack: име с ИНТЕРВАЛИ и Unicode + термин — четенето и сканирането не се чупят."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / f"проект с интервал {TERM}.md").write_text("съдържание", encoding="utf-8")
    assert main(["security_scan.py", _denylist(tmp_path, TERM),
                 f"проект с интервал {TERM}.md"]) == 2
    clean = tmp_path / "проект с интервал.md"
    clean.write_text(f"вътре има {TERM}", encoding="utf-8")
    assert scan_file(clean, [TERM]) == [TERM]      # интервалите в пътя не пречат


def test_invalid_utf8_denylist_is_operational_error(tmp_path):
    """Одит v24: невалиден-UTF-8 denylist → structured rc=3, НЕ traceback/rc=1."""
    bad = tmp_path / "bad_deny.txt"
    bad.write_bytes(b"\xff\xfe\x00invalid")
    f = tmp_path / "x.md"; f.write_text("нещо", encoding="utf-8")
    assert main(["security_scan.py", str(bad), str(f)]) == 3


# ===================================================================
# Одит v23 (остатъчен P0): грешка при четене на staged blob = fail-closed
# ===================================================================

def test_staged_git_show_failure_is_operational_error(tmp_path, monkeypatch):
    """git diff казва, че файлът е staged (ACM), но `git show :path` се проваля →
    ОПЕРАЦИОННА грешка (rc=3), НЕ „чисто" (rc=0).  Точната fault-injection на одита."""
    import types
    import tools.security_scan as ss
    dl = _denylist(tmp_path, TERM)

    def fake_run(cmd, **kw):
        r = types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        if "diff" in cmd:
            r.stdout = b"secret.md\x00"          # има staged ACM файл
        elif "show" in cmd:
            r.returncode = 128                    # но blob-ът НЕ се чете
        return r

    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    assert main(["security_scan.py", "--staged", dl]) == 3   # fail-closed, не 0/2


def test_staged_git_enumerate_failure_is_operational_error(tmp_path, monkeypatch):
    """Ако git не може дори да ИЗБРОИ индекса → rc=3."""
    import types
    import tools.security_scan as ss
    dl = _denylist(tmp_path, TERM)
    monkeypatch.setattr(ss.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(
                            stdout=b"", stderr=b"", returncode=128))
    assert main(["security_scan.py", "--staged", dl]) == 3


def test_missing_denylist_is_operational_error_not_traceback(tmp_path):
    """Одит v23: липсващ denylist → structured rc=3, НЕ traceback/rc=1."""
    f = tmp_path / "x.md"
    f.write_text("нещо", encoding="utf-8")
    assert main(["security_scan.py", str(tmp_path / "nope.txt"), str(f)]) == 3


def test_staged_key_pattern_caught_from_index(tmp_path, monkeypatch):
    """--staged лови и secret-шаблон (синтетичен токен) в staged съдържанието."""
    import types
    import tools.security_scan as ss
    dl = _denylist(tmp_path, TERM)

    def fake_run(cmd, **kw):
        r = types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        if "diff" in cmd:
            r.stdout = b"cfg.env\x00"
        elif "show" in cmd:
            # split literal → source-ът няма contiguous match за key-скенера,
            # но runtime стойността matches (тества key-детекцията)
            token = "sk-ant-" + "api03-" + "A" * 16
            r.stdout = ("TOKEN=" + token).encode("utf-8")
        return r

    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    assert main(["security_scan.py", "--staged", dl]) == 2   # намерен ключ
