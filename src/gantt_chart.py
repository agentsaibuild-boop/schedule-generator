"""Plotly Gantt chart visualization for construction schedules."""

from __future__ import annotations

from datetime import datetime, timedelta

import plotly.graph_objects as go


# Color map by activity category
CATEGORY_COLORS = {
    "Проектиране": "#4472C4",
    "Строителство": "#70AD47",
    "Водопровод": "#5B9BD5",
    "Канализация": "#ED7D31",
    "Пътни работи": "#A5A5A5",
    "Авт. надзор": "#7030A0",
    "КПС": "#FFC000",
    "Мобилизация": "#70AD47",
    "Дезинфекция": "#44546A",
    "Изпитване": "#44546A",
}

DEFAULT_COLOR = "#4472C4"


def create_gantt_chart(schedule_data: list[dict]) -> go.Figure:
    """Create a horizontal bar Gantt chart from schedule data.

    Args:
        schedule_data: List of task dicts with keys:
            - name: Task name (str)
            - category: Category for color coding (str)
            - start: Start date (datetime)
            - end: End date (datetime)
            - start_day: Start day number (int)
            - duration: Duration in days (int)
            - dn: Pipe diameter (str)
            - length_m: Length in meters (str)
            - team: Team name (str)

    Returns:
        Plotly Figure object with the Gantt chart.
    """
    if not schedule_data:
        schedule_data = _get_demo_data()

    fig = go.Figure()

    # Reverse order so first task appears at top
    tasks = list(reversed(schedule_data))

    for task in tasks:
        color = CATEGORY_COLORS.get(task.get("category", ""), DEFAULT_COLOR)
        start = task["start"]
        end = task["end"]

        hover_text = (
            f"<b>{task['name']}</b><br>"
            f"Категория: {task.get('category', '-')}<br>"
            f"DN: {task.get('dn', '-')}<br>"
            f"Дължина: {task.get('length_m', '-')} м<br>"
            f"Екип: {task.get('team', '-')}<br>"
            f"Начало: ден {task.get('start_day', '-')}<br>"
            f"Продължителност: {task.get('duration', '-')} дни<br>"
            f"Период: {start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"
        )

        fig.add_trace(go.Bar(
            y=[task["name"]],
            x=[(end - start).days + 1],
            base=[start],
            orientation="h",
            marker=dict(color=color, line=dict(color="white", width=1)),
            hovertext=hover_text,
            hoverinfo="text",
            name=task.get("category", task["name"]),
            showlegend=False,
        ))

    # Add legend entries (one per unique category)
    seen_categories: set[str] = set()
    for task in schedule_data:
        cat = task.get("category", "")
        if cat and cat not in seen_categories:
            seen_categories.add(cat)
            fig.add_trace(go.Bar(
                y=[None],
                x=[None],
                marker=dict(color=CATEGORY_COLORS.get(cat, DEFAULT_COLOR)),
                name=cat,
                showlegend=True,
            ))

    fig.update_layout(
        title=dict(
            text="Линеен график (Gantt)",
            font=dict(size=16),
        ),
        xaxis=dict(
            title="Дата",
            type="date",
            tickformat="%d.%m.%Y",
            gridcolor="#E5E5E5",
        ),
        yaxis=dict(
            title="",
            autorange="reversed",
        ),
        barmode="overlay",
        height=max(350, len(schedule_data) * 50 + 100),
        margin=dict(l=200, r=40, t=60, b=60),
        plot_bgcolor="white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        font=dict(family="Arial, sans-serif", size=12),
    )

    return fig


def _get_demo_data() -> list[dict]:
    """Generate demo schedule data for initial display.

    Returns:
        List of demo task dicts.
    """
    base = datetime(2025, 1, 1)
    return [
        {
            "name": "Проектиране",
            "category": "Проектиране",
            "start": base,
            "end": base + timedelta(days=179),
            "start_day": 1,
            "duration": 180,
            "dn": "-",
            "length_m": "-",
            "team": "Проектант",
        },
        {
            "name": "Мобилизация",
            "category": "Мобилизация",
            "start": base + timedelta(days=180),
            "end": base + timedelta(days=189),
            "start_day": 181,
            "duration": 10,
            "dn": "-",
            "length_m": "-",
            "team": "Всички",
        },
        {
            "name": "Водопровод Кл.1",
            "category": "Водопровод",
            "start": base + timedelta(days=190),
            "end": base + timedelta(days=229),
            "start_day": 191,
            "duration": 40,
            "dn": "DN110",
            "length_m": "520",
            "team": "Екип 1",
        },
        {
            "name": "Канализация Гл.Кл",
            "category": "Канализация",
            "start": base + timedelta(days=200),
            "end": base + timedelta(days=259),
            "start_day": 201,
            "duration": 60,
            "dn": "DN315",
            "length_m": "740",
            "team": "Екип 2",
        },
        {
            "name": "Пътни работи",
            "category": "Пътни работи",
            "start": base + timedelta(days=219),
            "end": base + timedelta(days=279),
            "start_day": 220,
            "duration": 61,
            "dn": "-",
            "length_m": "-",
            "team": "Пътна бригада",
        },
        {
            "name": "Авторски надзор",
            "category": "Авт. надзор",
            "start": base + timedelta(days=180),
            "end": base + timedelta(days=279),
            "start_day": 181,
            "duration": 100,
            "dn": "-",
            "length_m": "-",
            "team": "Проектант",
        },
    ]
