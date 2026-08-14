"""ТВЪРД КРАЕН СРОК за една дълга операция (генерация).

ЖИВ ПРОГОН 14.08.2026: приложението увисна с часове.  Всяко отделно HTTP
извикване има timeout, но един опит за генерация е ДЕСЕТКИ извиквания
(пакети → допитване за неразпределени → допълване → контрольор), а при
стрийминг доставчикът може да капе по един токен и никой timeout да не се
задейства.  Тоест таванът на едно извикване НЕ е таван на опита.  Резултатът
е заключен интерфейс: човекът гледа „генерирам..." и няма какво да натисне.

Тук срокът е на ЦЕЛИЯ опит и се мери с часовник, не с доверие към доставчика.
Работата тръгва в отделна нишка, а извикващият чака до срока.

ЧЕСТНО ЗА ОГРАНИЧЕНИЕТО: Python не убива нишка.  Изоставената нишка може да
доработи в празното — затова е `daemon` (не задържа спирането на процеса) и
резултатът ѝ се изхвърля.  Това, което се гарантира, е че ИЗВИКВАЩИЯТ се
освобождава на секундата — интерфейсът престава да е заложник на доставчика.

Съобщенията за напредък минават през опашка и се предават от ЧАКАЩАТА нишка.
Причината е Streamlit: `st.*` от чужда нишка няма ScriptRunContext и просто
не се вижда — тоест наивното пренасяне в нишка би изключило целия напредък.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Колко съобщения за напредък се пазят, преди да започнат да се изхвърлят.
#: Опашката е за показване, не за архив — изоставена нишка не бива да расте в
#: паметта, докато потребителят вече работи по следващия опит.
_MAX_QUEUED = 500


#: Срок на ЕДИН опит за генерация, в секунди.  Мярката е от серията от
#: 14.08.2026: чистите прогони траят между 40 сек и 7 минути, тоест 10 минути
#: са с голям запас за нормална работа и все пак таван, който човек изчаква.
_DEFAULT_ATTEMPT_TIMEOUT = 600


class DeadlineExceeded(TimeoutError):
    """Работата не приключи в отпуснатото време и беше изоставена."""


def attempt_timeout(env_var: str = "GENERATION_ATTEMPT_TIMEOUT") -> int:
    """Срокът на един опит в секунди — от средата, с разумно подразбиране.

    `0` изключва срока изрично (за отстраняване на проблеми); празно или
    нечислова стойност се пренебрегва, вместо да чупи генерацията.
    """
    сурово = (os.getenv(env_var) or "").strip()
    if not сурово:
        return _DEFAULT_ATTEMPT_TIMEOUT
    try:
        стойност = int(float(сурово))
    except ValueError:
        logger.warning("%s=%r не е число — ползвам %d сек.",
                       env_var, сурово, _DEFAULT_ATTEMPT_TIMEOUT)
        return _DEFAULT_ATTEMPT_TIMEOUT
    return max(стойност, 0)


def _drain(съобщения: "queue.Queue[str]", progress: Callable[[str], None] | None) -> None:
    while True:
        try:
            съобщение = съобщения.get_nowait()
        except queue.Empty:
            return
        if progress:
            try:
                progress(съобщение)
            except Exception:                            # noqa: BLE001
                logger.debug("Показването на напредъка се провали", exc_info=True)


def run_with_deadline(
    work: Callable[[Callable[[str], None]], Any],
    timeout_s: float | None,
    *,
    progress: Callable[[str], None] | None = None,
    poll_s: float = 0.25,
    name: str = "work",
) -> Any:
    """Изпълни `work(напредък)` с твърд срок в секунди.

    Args:
        work: Функцията-работа.  Получава функция за напредък, която може да
              се вика от нейната нишка (съобщенията се предават на чакащия).
        timeout_s: Срок в секунди.  None или ≤0 → без срок (изпълнява се
              направо в текущата нишка, без нишки и опашки).
        progress: Къде да отиват съобщенията — вика се от ТЕКУЩАТА нишка.
        poll_s: През колко се проверява за готовност и нови съобщения.
        name: Име за лога и за нишката.

    Returns:
        Каквото върне `work`.

    Raises:
        DeadlineExceeded: ако срокът изтече.  Работата продължава в
            изоставена daemon нишка, но резултатът ѝ се изхвърля.
        Exception: каквото е вдигнала самата работа — препредава се както си е.
    """
    if not timeout_s or timeout_s <= 0:
        return work(progress or (lambda _message: None))

    съобщения: "queue.Queue[str]" = queue.Queue(maxsize=_MAX_QUEUED)
    изход: dict[str, Any] = {}

    def _sink(message: str) -> None:
        try:
            съобщения.put_nowait(str(message))
        except queue.Full:                               # изоставена нишка
            pass

    def _run() -> None:
        try:
            изход["result"] = work(_sink)
        except BaseException as exc:                     # noqa: BLE001
            изход["error"] = exc

    нишка = threading.Thread(target=_run, name=f"deadline-{name}", daemon=True)
    начало = time.monotonic()
    нишка.start()

    while True:
        нишка.join(timeout=poll_s)
        _drain(съобщения, progress)
        if not нишка.is_alive():
            break
        if time.monotonic() - начало >= timeout_s:
            logger.warning(
                "%s не завърши за %.0f сек — прекъсвам чакането "
                "(нишката е изоставена).", name, timeout_s)
            raise DeadlineExceeded(
                f"{name} не завърши за {timeout_s:.0f} сек")

    if "error" in изход:
        raise изход["error"]
    return изход.get("result")
