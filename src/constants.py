"""Shared constants for Gantt visualisation (Plotly + PDF)."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical color palette (hex strings — single source of truth)
# ---------------------------------------------------------------------------

COLOR_PALETTE: dict[str, str] = {
    "design": "#4472C4",
    "water_pipe": "#5B9BD5",
    "sewer": "#ED7D31",
    "kps": "#FFC000",
    "road": "#A5A5A5",
    "electrical": "#70AD47",
    "mobilization": "#9DC3E6",
    "completion": "#BF8F00",
    "supervision": "#7030A0",
}

CRITICAL_PATH_COLOR = "#FF0000"
CRITICAL_PATH_BORDER = "#8B0000"

# ---------------------------------------------------------------------------
# Bulgarian display labels (single source of truth)
# ---------------------------------------------------------------------------

TYPE_LABELS: dict[str, str] = {
    "design": "Проектиране",
    "water_pipe": "Водоснабдяване",
    "sewer": "Канализация",
    "kps": "КПС",
    "road": "Пътни работи",
    "electrical": "ЕЛ/ТТ Кабели",
    "mobilization": "Мобилизация",
    "completion": "Завършване",
    "supervision": "Авт. надзор",
}
