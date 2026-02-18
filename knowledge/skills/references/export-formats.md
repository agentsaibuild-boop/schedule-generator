# Експорт формати

## MSPDI XML
- Основен формат за MS Project
- Генерира се директно с Python (xml.etree.ElementTree)
- MS Project -> File -> Open -> XML Format -> Save As .mpp

## PDF (A3 Landscape)
- A3 landscape Gantt диаграма
- Колони: DN, L(м), Екип, Дни
- Color-coded bars по тип дейност:
  - Проектиране: #4472C4
  - Водопровод: #5B9BD5
  - Канализация: #ED7D31
  - Пътни работи: #A5A5A5
  - Авт. надзор: #7030A0
  - КПС: #FFC000
  - Мобилизация: #70AD47

## .mpp
- НЯМА безплатен начин за директно създаване
- Workflow: MSPDI XML -> MS Project -> Save As .mpp
