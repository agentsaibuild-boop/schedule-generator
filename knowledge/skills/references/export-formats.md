# Експорт формати

## КРИТИЧНО: Pipeline до крайния .mpp файл

```
JSON (AI генерира) → MSPDI XML (export_xml.py) → MS Project отваря → Save As .mpp
```

Крайният файл за клиента е **.mpp** (нативен MS Project формат).
XML е само междинна стъпка. Пряко генериране на .mpp е невъзможно без лиценз.

**Пълни правила за структуриране на .mpp-съвместим JSON:**
→ Виж `ms-project-structure.md` (задължително четене преди генериране!)

---

## MSPDI XML (src/export_xml.py)

- Генерира се от Python (xml.etree.ElementTree)
- SaveVersion=14 → MS Project 2010+
- DurationFormat=5 → дни (НЕ elapsed days) — КРИТИЧНО
- Manual=1 → фиксирани дати, без автоматично преизчисляване
- 7-дневен работен календар (08:00-12:00, 13:00-17:00)
- Кастъмни полета: Text1=DN, Number1=L(м), Text2=Мярка, Text3=Екип
- UID=0 root task (задължителен от MS Project)
- Empty resource UID=0 (задължителен от MS Project)

**Отваряне в MS Project:**
File → Open → XML Format → (избери файла) → File → Save As → .mpp

---

## PDF (A3 Landscape) — src/export_pdf.py

- A3 landscape Gantt диаграма за печат и представяне
- Колони: DN, L(м), Екип, Дни
- Color-coded bars по тип дейност:
  - Проектиране: #4472C4
  - Водопровод: #5B9BD5
  - Канализация: #ED7D31
  - Пътни работи: #A5A5A5
  - Авт. надзор: #7030A0
  - КПС: #FFC000
  - Мобилизация: #70AD47
- Шрифт: DejaVu Sans (поддържа кирилица) от fonts/

---

## JSON (суров) — за програмна обработка

- Изтегля се директно от app.py (tab Експорт)
- Съдържа: metadata + activities списък
- Полезен за дебъг и повторна обработка
