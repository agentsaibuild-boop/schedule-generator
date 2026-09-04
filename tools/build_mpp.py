"""Строи .mpp през ОБЕКТНИЯ МОДЕЛ на MS Project — НА ЕТАПИ, със запис след всеки.

ЗАЩО НЕ ПРЕЗ MSPDI.  Преобразуването XML → .mpp в самия MS Project разваля
назначенията: след записа и повторно отваряне ВСИЧКИ сочат към последния ресурс
в листа.  Мерено 03.09.2026, възпроизведено с два реда и пет ресурса, без наша
намеса между вноса и записа.

ЗАЩО НА ЕТАПИ.  MS Project на тази машина умира при продължителна работа през
COM — „The RPC server is unavailable", „The object invoked has disconnected".
При 1109 задачи това се случва през път, а при строеж наведнъж се губи всичко.
Затова работата е разделена на пет етапа; след всеки файлът се ЗАПИСВА и
инстанцията се затваря.  Докъде е стигнато стои в `.напредък.json` до файла —
при срив се продължава оттам, а не отначало.

    python tools/build_mpp.py задачи.json график.mpp --name "обект"
"""

from __future__ import annotations

import argparse
import json
import contextlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Кодовете на връзките за обектния модел (проверени по датите 03.09.2026).
_ТИП = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}
_ДЕН = 480                       #: работен ден в минути
_ФИКСИРАНА = 1                   #: pjFixedDuration
_ЕТАПИ = ("задачи", "полета", "връзки", "ресурси", "изглед")


def _кирилица(път: Path) -> bool:
    return any("Ѐ" <= знак <= "ӿ" for знак in str(път))


def _разбий(име: str) -> tuple[str, float]:
    m = re.match(r"^(.*?)\[(\d+)%\]$", име.strip())
    return (m.group(1).strip(), int(m.group(2)) / 100.0) if m else (име.strip(), 1.0)


def _нива(задачи: list[dict]) -> dict[str, int]:
    """ID → ниво в дървото, сметнато веднъж, а не при всяка задача."""
    родител = {з["id"]: з.get("parent_id") for з in задачи}
    ниво: dict[str, int] = {}

    def дълбочина(ид: str) -> int:
        if ид in ниво:
            return ниво[ид]
        р = родител.get(ид)
        ниво[ид] = 1 if not р else дълбочина(р) + 1
        return ниво[ид]

    return {ид: дълбочина(ид) for ид in родител}


def _върхове(задачи: list[dict], листа: set[str]) -> dict[str, float]:
    """Максималната едновременна нужда от всеки ресурс."""
    по_дни: dict[int, dict[str, float]] = {}
    връх: dict[str, float] = {}
    for з in задачи:
        for суров in з.get("resources") or []:
            име, бр = _разбий(суров)
            if з["id"] not in листа:
                връх.setdefault(име, 1.0)
                continue
            for д in range(int(з["start_day"]),
                           int(з.get("end_day", з["start_day"])) + 1):
                ден = по_дни.setdefault(д, {})
                ден[име] = ден.get(име, 0.0) + бр
                връх[име] = max(връх.get(име, 0.0), ден[име])
    return връх


def _пусни(wc, mpp: Path):
    последна = None
    for _ in range(6):
        try:
            wc.gencache.EnsureDispatch("MSProject.Application")
            app = wc.DispatchEx("MSProject.Application")
            app.Visible = False
            app.DisplayAlerts = False
            if mpp.exists():
                app.FileOpen(str(mpp))
            else:
                app.FileNew()
            _ = app.ActiveProject.Tasks.Count
            return app
        except Exception as грешка:
            последна = грешка
            time.sleep(6)
    raise RuntimeError(f"MS Project не тръгна: {последна}")


def _спри(app, mpp: Path) -> None:
    try:
        app.FileSaveAs(Name=str(mpp), FormatID="MSProject.MPP")
    finally:
        try:
            app.FileCloseAll(0)
            app.Quit()
        except Exception:
            pass
    time.sleep(2)


def _етап_задачи(app, проект, задачи, к) -> None:
    проект.ScheduleFromStart = True
    проект.ProjectStart = к["ден_нула"]
    if к["име"]:
        проект.Title = к["име"]
    try:
        календар = проект.Calendar
        for ден in range(1, 8):
            седмица = календар.WeekDays(ден)
            седмица.Working = True
            седмица.Shift1.Start, седмица.Shift1.Finish = "08:00", "12:00"
            седмица.Shift2.Start, седмица.Shift2.Finish = "13:00", "17:00"
    except Exception as грешка:
        print(f"  ВНИМАНИЕ: календарът 7/7 не се нагласи ({грешка})")
    # НОВИТЕ ЗАДАЧИ ДА СА АВТОМАТИЧНИ.  В MS Project 2010+ `Tasks.Add` прави
    # РЪЧНО планирани задачи, ако такава е настройката по подразбиране — тогава
    # връзките не движат нищо: мерено, 972 от 978 листа оставаха на ден 1 и
    # критичният път излизаше нула.
    for настройка in (lambda: app.OptionsSchedule(NewTasksAreManual=False),
                      lambda: setattr(проект, "NewTasksCreatedAsManual", False)):
        try:
            настройка()
            break
        except Exception:
            continue
    нива = к["нива"]
    for i, з in enumerate(задачи, 1):
        т = проект.Tasks.Add(з["name"])
        # НИВОТО СЕ ЗАДАВА ВИНАГИ, включително 1.  Новата задача наследява
        # позицията на предишната, затова „ЧАСТ Б" след задача от ниво 4
        # оставаше вложена и цялото строителство попадаше ПОД „ЧАСТ А".
        try:
            т.OutlineLevel = нива[з["id"]]
        except Exception:
            pass
        if i % 300 == 0:
            print(f"    {i}/{len(задачи)}")
    print(f"  задачи: {проект.Tasks.Count}")


def _етап_полета(app, проект, задачи, к) -> None:
    for i, з in enumerate(задачи, 1):
        т = проект.Tasks(i)
        нач = int(з.get("start_day") or 1)
        кр = int(з.get("end_day") or нач)
        try:
            т.Number4, т.Number5 = нач, кр
        except Exception:
            pass
        try:
            т.Manual = False
        except Exception:
            pass
        try:
            # БЕЗ ВЪПРОСИТЕЛНА В КОЛОНА „Срок" (неотменимо правило, 04.09.2026).
            # Новите задачи в MS Project са с ПРЕДПОЛАГАЕМА продължителност и
            # той дописва „?" след всяко число — „8 days?".  Продължителностите
            # тук идват от нормите и от разписанието, не са предположение.
            т.Estimated = False
        except Exception:
            pass
        if з["id"] not in к["листа"]:
            continue
        try:
            if bool(з.get("milestone")) or int(з.get("duration") or 0) == 0:
                т.Duration = 0
            else:
                т.Type = _ФИКСИРАНА
                т.EffortDriven = False
                т.Duration = max(1, кр - нач + 1) * _ДЕН
            т.ConstraintType = 0
            for поле, стойност in (("Text1", з.get("diameter")),
                                   ("Text2", з.get("unit")),
                                   ("Text3", з.get("team")),
                                   ("Number1", з.get("length_m"))):
                if стойност not in (None, ""):
                    setattr(т, поле, стойност)
            if з.get("contractual"):
                т.Deadline = к["ден_нула"] + timedelta(days=кр - 1)
        except Exception as грешка:
            print(f"    ВНИМАНИЕ {з['id']}: {str(грешка)[:50]}")
    print("  полета: готови")


def _етап_връзки(app, проект, задачи, к) -> None:
    редове = {з["id"]: n for n, з in enumerate(задачи, 1)}
    добавени = пропуснати = 0
    for n, з in enumerate(задачи, 1):
        за = з.get("dependencies") or []
        if not за:
            continue
        т = проект.Tasks(n)
        for d in за:
            откъде = редове.get(str(d.get("predecessor_id")))
            if откъде is None:
                пропуснати += 1
                continue
            try:
                т.TaskDependencies.Add(
                    проект.Tasks(откъде),
                    _ТИП.get(str(d.get("type", "FS")).upper(), 1),
                    int(d.get("lag") or 0) * _ДЕН)
                добавени += 1
            except Exception:
                пропуснати += 1
    print(f"  връзки: {добавени}"
          + (f"; пропуснати {пропуснати}" if пропуснати else ""))


def _етап_ресурси(app, проект, задачи, к) -> None:
    връх = _върхове(задачи, к["листа"])
    съществуващи = {р.Name for р in проект.Resources if р is not None}
    for име, бройка in връх.items():
        if име in съществуващи:
            continue
        р = проект.Resources.Add(име)
        р.MaxUnits = max(1.0, float(бройка or 1.0))
    print(f"  ресурси: {проект.Resources.Count}")
    # ЕДИН РЕД НА ЗАДАЧА.  По едно повикване за всеки ресурс значи ~8500
    # обръщения и MS Project умира някъде към 6800-то.
    брой = 0
    for n, з in enumerate(задачи, 1):
        ресурси = з.get("resources") or []
        if з["id"] not in к["листа"] or not ресурси:
            continue
        try:
            проект.Tasks(n).ResourceNames = ";".join(ресурси)
            брой += len(ресурси)
        except Exception as грешка:
            print(f"    ВНИМАНИЕ {з['id']}: {str(грешка)[:50]}")
    print(f"  назначения: {брой}")
    # СВЕРКА СРЕЩУ СЛЕПВАНЕ.  `ResourceNames = "А;Б;В"` е един ред вместо
    # осем повиквания, но ако разделителят не съвпадне с локализацията, MS
    # Project прави ЕДИН ресурс на име „А, Б" вместо два — и то мълчаливо.
    # Проверява се по броя: колкото имена сме подали, толкова трябва да има.
    очаквани = {р for з in задачи if з["id"] in к["листа"]
                for р in (з.get("resources") or [])}
    във_файла = {р.Name for р in проект.Resources if р is not None}
    липсват = очаквани - във_файла
    if липсват:
        print(f"  ВНИМАНИЕ: {len(липсват)} ресурса не стигнаха до файла — "
              f"{sorted(липсват)[:3]}")
    else:
        print(f"  сверка на ресурсите: {len(очаквани)} подадени, "
              f"{len(очаквани)} налични")


def _етап_изглед(app, проект, задачи, к) -> None:
    from to_mpp import (_докладвай_критичния_път, _заглавен_ред, _константи,
                        _ленти_без_текст, _мрежа, _скала, _таблица, _търпеливо)

    _търпеливо(lambda: app.CalculateProject())
    _таблица(app)
    pj = _константи()
    ден = pj.get("pjCalendarLabelDayFromStart_dd", 56)
    _скала(app, pj.get("pjTimescaleDays", 4), ден, ден,
           увеличение=100, дребна_стъпка=1, едра_стъпка=10, редове=2)
    _ленти_без_текст(app)
    _мрежа(app)
    _заглавен_ред(app)

    базов = проект.ProjectStart.date()
    разлики = []
    for n, з in enumerate(задачи, 1):
        т = проект.Tasks(n)
        try:
            if т.Summary or т.Milestone:
                continue
            техен = (т.Start.date() - базов).days + 1
        except Exception:
            continue
        if техен != int(з["start_day"]):
            разлики.append(f"{з['name'][:24]}: наш {з['start_day']}, MS {техен}")
    if разлики:
        print(f"  РАЗМИНАВАНЕ: {len(разлики)}; първите: {разлики[:4]}")
    else:
        print("  сверка по дни: нула разминавания")
    _докладвай_критичния_път(проект)


_ЕТАП_ФУНКЦИИ = {
    "задачи": _етап_задачи,
    "полета": _етап_полета,
    "връзки": _етап_връзки,
    "ресурси": _етап_ресурси,
    "изглед": _етап_изглед,
}


def _пусни_етап(име: str, задачи: list[dict], mpp: Path, контекст: dict) -> None:
    import win32com.client as wc

    # Катинарът е на ЕТАП, не на целия строеж: между етапите MS Project е
    # свободен, а етапът трае под три минути.  Така чуждият прогон не чака
    # двайсет минути за нашия, а най-много един етап.
    with _катинар(f"{Path(sys.argv[0]).stem}/{име}"):
        app = _пусни(wc, mpp)
        проект = app.ActiveProject
        for изключи in (lambda: setattr(app, "Calculation", 0),
                        lambda: setattr(app, "ScreenUpdating", False)):
            try:
                изключи()
            except Exception:
                pass
        try:
            _ЕТАП_ФУНКЦИИ[име](app, проект, задачи, контекст)
        finally:
            for върни in (lambda: setattr(app, "Calculation", 1),
                          lambda: setattr(app, "ScreenUpdating", True)):
                try:
                    върни()
                except Exception:
                    pass
        _спри(app, mpp)


#: MS PROJECT Е ЕДИН COM СЪРВЪР ЗА ЦЯЛАТА МАШИНА.  `DispatchEx` НЕ прави втори
#: процес — два едновременни строежа пишат в една инстанция и вторият получава
#: случайни откази („Unspecified error", -2147467259).  Мерено на 04.09.2026:
#: чужд строеж събори наш по средата на пълненето на задачите.
#: Затова: файл-катинар.  Чуждият .mpp не се пипа и не се затваря — само се
#: чака.  Катинар по-стар от ИЗТИЧАНЕ се смята за забравен и се прегазва.
КАТИНАР = Path(tempfile.gettempdir()) / "msproject.lock"
ИЗТИЧАНЕ = 15 * 60          # секунди
ТЪРПЕНИЕ = 15 * 60          # колко чакаме чужд строеж


@contextlib.contextmanager
def _катинар(кой: str):
    край = time.time() + ТЪРПЕНИЕ
    известено = False
    while КАТИНАР.exists():
        try:
            възраст = time.time() - КАТИНАР.stat().st_mtime
            чий = КАТИНАР.read_text(encoding="utf-8").strip()
        except OSError:
            break
        if възраст > ИЗТИЧАНЕ:
            print(f"катинарът е забравен от {чий} ({възраст / 60:.0f} мин) — "
                  f"продължавам")
            break
        if time.time() > край:
            print(f"чаках {ТЪРПЕНИЕ // 60} мин за {чий} — продължавам на риск")
            break
        if not известено:
            print(f"MS Project е зает от {чий}; чакам…")
            известено = True
        time.sleep(10)
    try:
        КАТИНАР.write_text(f"{кой} · {datetime.now():%H:%M:%S}",
                           encoding="utf-8")
    except OSError:
        pass
    try:
        yield
    finally:
        try:
            КАТИНАР.unlink(missing_ok=True)
        except OSError:
            pass


def построй(задачи: list[dict], mpp: Path, име: str, начало: str) -> None:
    напредък = mpp.with_suffix(".напредък.json")
    готови = set(json.loads(напредък.read_text(encoding="utf-8"))) \
        if напредък.exists() else set()
    родители = {з.get("parent_id") for з in задачи if з.get("parent_id")}
    контекст = {
        # ЧАСЪТ ПАЗИ ДАТАТА.  Подадено като полунощ, началото се записва два
        # часа по-рано (часова зона) и пада в ПРЕДИШНИЯ ден — тогава дневната
        # скала брои от 30.09, а колоните „Начало (ден)" от 01.10.
        "ден_нула": datetime.strptime(начало, "%Y-%m-%d").replace(hour=8),
        "име": име,
        "нива": _нива(задачи),
        "листа": {з["id"] for з in задачи
                  if not (з.get("is_summary") or з["id"] in родители)},
    }
    for етап in _ЕТАПИ:
        if етап in готови:
            print(f"етап {етап}: вече е минал, пропуска се")
            continue
        последна = None
        for опит in range(1, 5):
            try:
                print(f"етап {етап} (опит {опит}) …")
                _пусни_етап(етап, задачи, mpp, контекст)
                готови.add(етап)
                напредък.write_text(json.dumps(sorted(готови), ensure_ascii=False),
                                    encoding="utf-8")
                break
            except Exception as грешка:
                последна = грешка
                print(f"  прекъсна: {str(грешка)[:70]}")
                subprocess.run(["taskkill", "/F", "/IM", "WINPROJ.EXE"],
                               capture_output=True)
                time.sleep(8)
        else:
            raise SystemExit(f"етап {етап} не мина за четири опита: {последна}")
    напредък.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("задачи")
    parser.add_argument("mpp")
    parser.add_argument("--name", default="")
    parser.add_argument("--start", default="2026-10-01")
    args = parser.parse_args()

    задачи = json.loads(Path(args.задачи).read_text(encoding="utf-8"))
    цел = Path(args.mpp)
    работна = Path(tempfile.mkdtemp(prefix="mpp_"))
    временна = работна / "schedule.mpp" if _кирилица(цел) else цел
    построй(задачи, временна, args.name, args.start)
    if временна != цел:
        if цел.exists():
            цел.unlink()
        shutil.move(str(временна), str(цел))
    shutil.rmtree(работна, ignore_errors=True)
    print(f"MPP: {цел} ({цел.stat().st_size} байта)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
