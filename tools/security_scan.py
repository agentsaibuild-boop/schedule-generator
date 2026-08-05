#!/usr/bin/env python3
"""Скан за клиентски/чувствителни термини в СЪДЪРЖАНИЕТО на файлове.

Одит v19: предишният inline-скан в `hooks/pre-commit` беше СЧУПЕН — heredoc-ът
(`python - <<'PY'`) заемаше stdin и `sys.stdin.read()` връщаше празно, затова
скенерът пропускаше всичко.  Тук логиката е в ОТДЕЛЕН tracked файл, който чете
самите файлове ОТ ДИСКА (никакъв stdin), и има unit тестове.

Съвпадението е Unicode-коректно:
  * NFC нормализация (composed/decomposed текст съвпада);
  * casefold (кирилски главни/малки букви — `grep -i` в Git Bash НЕ ги сгъва).

Denylist-ът с РЕАЛНИ имена НЕ е в git (иначе сам е изтичане) — подава се като
път (локално `.git/hooks-secrets`, в CI — от secret).  Един термин на ред,
редове започващи с `#` са коментари.

Проверява И СЪДЪРЖАНИЕТО, И PATHNAME-а (одит v22: клиентско име може да е в
ИМЕТО на файла, не само вътре).

CLI:
    python tools/security_scan.py <denylist> <file> [<file> ...]
        → сканира съдържанието + собствения път на всеки файл
    python tools/security_scan.py --names-only <denylist> <str> [<str> ...]
        → сканира подадените НИЗОВЕ (pathname-и)
    python tools/security_scan.py --staged <denylist>
        → сканира STAGED промените ДИРЕКТНО ОТ GIT INDEX (чете от git индекса, одит v23):
          чете `git show :path`, не работното дърво, затова staged-но-после-изтрит
          от worktree файл ПАК се сканира.  Проверява и pathname, и съдържание.
Изход: 0 = чисто; 2 = намерен термин; 3 = ОПЕРАЦИОННА грешка (липсващ/нечетим
файл, git грешка) — fail-closed; 1 = грешна употреба.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

OPERATIONAL_ERROR = 3   # одит v23: „не можах да прочета" ≠ „чисто"

# Секрет-шаблони (одит v23: сканирани заедно с denylist-а в `--staged`, за да е
# ЕДИН чете от git индекса път).  Не са тайни — форматите са публични.
_KEY_RE = re.compile(
    r"sk-ant-api[0-9]|sk-or-v1-[A-Za-z0-9]{20}|sk-proj-[A-Za-z0-9_-]{20}"
    r"|sk-[a-f0-9]{32}|gsk_[A-Za-z0-9]{20}|AIza[A-Za-z0-9_-]{30}"
    r"|xoxb-[0-9]|ghp_[A-Za-z0-9]{20}")
# IGNORECASE (одит 2026-08, т.4): без него същият машинен път, изписан изцяло
# с малки букви, заобикаляше блокирането, докато формата с главни се лавеше.
# Пътищата не са case-sensitive на Windows/macOS, затова сгъваме регистъра.
# (Без литерален пример тук — иначе скенерът флагва собствения си коментар.)
_PATH_RE = re.compile(
    r"Users[/\\][^/\\]+[/\\](?:Desktop|Downloads)|AppData[/\\]Local[/\\]Temp",
    re.IGNORECASE)


def _norm(text: str) -> str:
    """NFC + casefold — за да съвпадат и главни/малки, и composed/decomposed."""
    return unicodedata.normalize("NFC", text).casefold()


def load_terms(denylist_path: str | Path) -> list[str]:
    """Прочети denylist (един термин на ред; `#` = коментар; празни се пропускат)."""
    out: list[str] = []
    for line in Path(denylist_path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def scan_text(text: str, terms: list[str]) -> list[str]:
    """Върни списъка термини (в оригиналния им вид), намерени в текста."""
    body = _norm(text)
    return [t for t in terms if _norm(t) in body]


def _scan_value(value: str, terms: list[str]) -> list[tuple[str, str]]:
    """ВСИЧКИ проверки върху ЕДИН низ (одит v24): клиентски denylist + secret-
    шаблони + машинни пътища.  Прилага се и върху PATHNAME, и върху съдържание —
    иначе token-shaped/машинен-път ИМЕ на файл минаваше (P1)."""
    out: list[tuple[str, str]] = [("denylist", h) for h in scan_text(value, terms)]
    out += [("API ключ", m.group(0)[:10] + "…") for m in _KEY_RE.finditer(value)]
    if _PATH_RE.search(value):
        out.append(("машинен път", "<обезличи>"))
    return out


def scan_file(path: str | Path, terms: list[str]) -> list[str]:
    """Прочети файла ОТ ДИСКА и го сканирай.

    Одит v23: РАЗЛИЧАВА „файлът е чист" от „не можах да го прочета".  Липсващ или
    нечетим файл вдига OSError (fail-closed при извикващия), НЕ връща празно.
    Двоично съдържание се чете с errors="ignore" (не е операционна грешка).
    """
    text = Path(path).read_text(encoding="utf-8", errors="ignore")  # OSError → нагоре
    return scan_text(text, terms)


def scan_staged(terms: list[str]) -> tuple[list[tuple[str, str, str]], bool] | None:
    """Сканирай STAGED промените ДИРЕКТНО ОТ GIT INDEX (чете от git индекса, одит v23).

    Чете `git show :path` (staged blob), НЕ работното дърво — затова файл, който
    е `git add`-нат и после ИЗТРИТ от worktree, ПАК се сканира (staged версията
    отива в commit-а).  Проверява pathname, съдържание, secret-шаблони и
    машинни пътища — в ЕДИН път.

    Одит v23 (остатъчен P0): ако `git show :path` за ACM-файл се провали (файлът е
    в индекса, но blob-ът не се чете), това е ОПЕРАЦИОННА ГРЕШКА → fail-closed,
    НЕ `continue`.  (--diff-filter=ACM вече изключва изтритите, затова провал тук
    е реален проблем, не легитимно изтриване.)

    Returns (hits, op_error), или None ако git не може да ИЗБРОИ индекса.
    """
    def _git(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *a], capture_output=True)

    r = _git("diff", "--cached", "--name-only", "--diff-filter=ACM", "-z")
    if r.returncode != 0:
        return None
    paths = [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\x00") if p]
    hits: list[tuple[str, str, str]] = []
    op_error = False
    for p in paths:
        for kind, h in _scan_value(p, terms):         # PATHNAME (denylist+ключ+път)
            hits.append((p, "име/" + kind, h))
        blob = _git("show", f":{p}")
        if blob.returncode != 0:
            # ACM файл, но staged blob-ът НЕ се чете → fail-closed (не continue!)
            hits.append((p, "ГРЕШКА", "staged съдържанието не може да се прочете"))
            op_error = True
            continue
        text = blob.stdout.decode("utf-8", "ignore")
        for kind, h in _scan_value(text, terms):      # СЪДЪРЖАНИЕ (denylist+ключ+път)
            hits.append((p, "съдържание/" + kind, h))
    return hits, op_error


def main(argv: list[str]) -> int:
    # Одит v20: на Windows stdout е cp1252 и „✗"/кирилицата гърмят с
    # UnicodeEncodeError, ако hook-ът извика python без PYTHONIOENCODING.
    # Скенерът е защита — НЕ бива да гърми при печат.  Правим изхода устойчив.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = argv[1:]
    mode = "content"
    if args and args[0] in ("--names-only", "--staged"):
        mode = args[0][2:]                      # "names-only" | "staged"
        args = args[1:]
    need = 1 if mode == "staged" else 2
    if len(args) < need:
        sys.stderr.write(
            "употреба: security_scan.py [--names-only|--staged] <denylist> [<file|str> ...]\n")
        return 1
    # Одит v23/v24: липсващ ИЛИ невалиден-UTF-8 denylist е ОПЕРАЦИОННА грешка
    # (rc=3), не traceback/rc=1.  UnicodeError покрива нечетимото кодиране.
    try:
        terms = load_terms(args[0])
    except (OSError, UnicodeError) as exc:
        sys.stderr.write(f"  ! ОПЕРАЦИОННА грешка: denylist не се чете: {exc}\n")
        return OPERATIONAL_ERROR

    if mode == "staged":
        result = scan_staged(terms)
        if result is None:
            sys.stderr.write("  ! ОПЕРАЦИОННА грешка: git не можа да ИЗБРОИ индекса\n")
            return OPERATIONAL_ERROR
        hits, op_error = result
        for p, where, h in hits:
            sys.stdout.write(f"  ✗ намерено в {p} ({where}): {h}\n")
        if op_error:
            return OPERATIONAL_ERROR   # fail-closed: непрочетено staged съдържание
        return 2 if hits else 0

    found = False
    op_error = False
    for item in args[1:]:
        # PATHNAME винаги (denylist + ключ + път) — одит v24 P1: token-shaped/
        # машинен-път ИМЕ на файл трябва да се лови.
        marked = [("път/" + kind, h) for kind, h in _scan_value(item, terms)]
        if mode != "names-only":
            try:
                text = Path(item).read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                sys.stderr.write(f"  ! ОПЕРАЦИОННА грешка при {item}: {exc}\n")
                op_error = True
                text = None
            if text is not None:
                marked += [("съдържание/" + kind, h)
                           for kind, h in _scan_value(text, terms)]
        for where, hit in marked:
            sys.stdout.write(f"  ✗ намерено в {item} ({where}): {hit}\n")
            found = True
    if op_error:
        return OPERATIONAL_ERROR
    return 2 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
