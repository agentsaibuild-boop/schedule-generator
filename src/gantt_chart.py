"""Interactive Plotly Gantt chart with layers, critical path, and filters."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import plotly.graph_objects as go

from src.constants import COLOR_PALETTE as COLOR_MAP, CRITICAL_PATH_COLOR, CRITICAL_PATH_BORDER, TYPE_LABELS  # noqa: F401

# ---------------------------------------------------------------------------
# Constants (re-exported for backwards compatibility)
# ---------------------------------------------------------------------------

# Note: TYPE_LABELS and COLOR_MAP are imported above from src.constants.

DEFAULT_LAYERS: dict[str, bool] = {
    "bars": True,
    "critical_path": True,
    "dependencies": False,
    "team_labels": True,
    "duration_labels": False,
    "phase_separators": True,
    "today_line": True,
    "milestones": True,
    "subtasks": False,
}

_MS_PER_DAY = 86_400_000


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_type_label(type_code: str) -> str:
    """Translate type code to Bulgarian label."""
    return TYPE_LABELS.get(type_code, type_code)


def day_to_date(day_number: int, start_date: str) -> str:
    """Convert day number to formatted date string."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    return (start + timedelta(days=day_number - 1)).strftime("%d.%m.%Y")


def _day_to_dt(day_number: int, start_date: str) -> datetime:
    """Convert day number to datetime object."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    return start + timedelta(days=day_number - 1)


def _filter_tasks(
    tasks: list[dict],
    filter_team: str | None,
    filter_phase: str | None,
    filter_type: str | None,
) -> list[dict]:
    """Filter tasks by team, phase, and/or type."""
    result = tasks
    if filter_team:
        result = [t for t in result if t.get("team") == filter_team]
    if filter_phase:
        result = [t for t in result if t.get("phase") == filter_phase]
    if filter_type:
        result = [t for t in result if t.get("type") == filter_type]
    return result


# ---------------------------------------------------------------------------
# Main chart function
# ---------------------------------------------------------------------------


def create_gantt_chart(
    schedule_data: list[dict],
    layers: dict[str, bool] | None = None,
    view_mode: str = "months",
    selected_task_id: str | None = None,
    show_subtasks: bool = True,
    filter_team: str | None = None,
    filter_phase: str | None = None,
    filter_type: str | None = None,
    project_start_date: str = "2025-04-01",
) -> go.Figure:
    """Create an interactive Gantt chart with Plotly.

    Args:
        schedule_data: List of task dicts (see module docstring for format).
        layers: Which visual layers to enable.
        view_mode: Time axis granularity — "days", "weeks", or "months".
        selected_task_id: Highlighted task ID (click selection).
        show_subtasks: Whether the subtasks layer toggle is on.
        filter_team: Show only tasks for this team (None = all).
        filter_phase: Show only tasks in this phase (None = all).
        filter_type: Show only tasks of this type (None = all).
        project_start_date: Calendar date for day 1 (ISO format).

    Returns:
        Plotly Figure.
    """
    if layers is None:
        layers = DEFAULT_LAYERS.copy()

    if not schedule_data:
        schedule_data = generate_demo_schedule()

    # Task lookup by ID (for dependencies)
    task_lookup: dict[str, dict] = {t["id"]: t for t in schedule_data}

    # Filter
    visible = _filter_tasks(schedule_data, filter_team, filter_phase, filter_type)

    # Build flat display list (optionally including sub-activities)
    display: list[dict] = []
    for task in visible:
        display.append(task)
        if layers.get("subtasks") and task.get("sub_activities"):
            for sub in task["sub_activities"]:
                display.append(sub)

    # Sort by start_day, then end_day
    display.sort(key=lambda t: (t.get("start_day", 0), t.get("end_day", 0)))

    if not display:
        fig = go.Figure()
        fig.update_layout(
            title="Няма дейности за показване",
            height=300,
            plot_bgcolor="white",
        )
        return fig

    # Flags
    show_critical = layers.get("critical_path", False)
    legend_added: set[str] = set()

    # Y-axis labels (top-to-bottom by start_day)
    y_labels: list[str] = []
    for task in display:
        is_sub = _is_subtask(task, schedule_data)
        if is_sub:
            y_labels.append(f"  ↳ {task['name']}")
        else:
            y_labels.append(task["name"])

    fig = go.Figure()

    # ── BARS ──────────────────────────────────────────────────────────────
    for idx, task in enumerate(display):
        duration = task.get("duration", 1)
        if duration == 0:
            continue  # milestones rendered separately

        task_type = task.get("type", "design")
        is_critical = task.get("is_critical", False) and show_critical
        is_sub = _is_subtask(task, schedule_data)

        # Color / opacity
        if is_critical:
            bar_color = CRITICAL_PATH_COLOR
            border_color = CRITICAL_PATH_BORDER
            border_width = 2
            opacity = 1.0
            legend_key = "critical_path"
            legend_name = "Критичен път"
        else:
            bar_color = COLOR_MAP.get(task_type, "#4472C4")
            border_color = "white"
            border_width = 1
            opacity = 0.5 if show_critical else 0.85
            if is_sub:
                opacity *= 0.7
            legend_key = task_type
            legend_name = get_type_label(task_type)

        start_dt = _day_to_dt(task["start_day"], project_start_date)
        end_day = task.get(
            "end_day", task["start_day"] + max(duration, 1) - 1
        )
        end_dt = _day_to_dt(end_day, project_start_date)
        duration_ms = ((end_dt - start_dt).days + 1) * _MS_PER_DAY

        # Custom data for hover: [id, name, type_label, dn, length, team,
        #                          start_str, end_str, duration, critical]
        customdata = [[
            task.get("id", ""),
            task["name"],
            get_type_label(task_type),
            str(task.get("diameter", "—")),
            str(task.get("length_m", "—")),
            task.get("team", "—"),
            day_to_date(task["start_day"], project_start_date),
            day_to_date(end_day, project_start_date),
            str(duration),
            "Да" if task.get("is_critical") else "Не",
        ]]

        show_legend = legend_key not in legend_added
        if show_legend:
            legend_added.add(legend_key)

        bar_width = 0.4 if is_sub else 0.6

        fig.add_trace(go.Bar(
            y=[y_labels[idx]],
            x=[duration_ms],
            base=[start_dt],
            orientation="h",
            marker=dict(
                color=bar_color,
                opacity=opacity,
                line=dict(color=border_color, width=border_width),
            ),
            width=[bar_width],
            customdata=customdata,
            hovertemplate=(
                "<b>%{customdata[1]}</b><br>"
                "Тип: %{customdata[2]}<br>"
                "DN: %{customdata[3]} | L: %{customdata[4]}м<br>"
                "Екип: %{customdata[5]}<br>"
                "Начало: %{customdata[6]}<br>"
                "Край: %{customdata[7]}<br>"
                "Продължителност: %{customdata[8]}д<br>"
                "Критичен път: %{customdata[9]}<br>"
                "<extra></extra>"
            ),
            name=legend_name,
            showlegend=show_legend,
            legendgroup=legend_key,
        ))

        # ── Team label inside bar ─────────────────────────────────────────
        if (
            layers.get("team_labels")
            and task.get("team")
            and duration > 15
            and not is_sub
        ):
            mid_dt = start_dt + (end_dt - start_dt) / 2
            fig.add_annotation(
                x=mid_dt,
                y=y_labels[idx],
                text=task["team"],
                showarrow=False,
                font=dict(size=9, color="white"),
                xref="x",
                yref="y",
            )

        # ── Duration label to the right ───────────────────────────────────
        if layers.get("duration_labels"):
            fig.add_annotation(
                x=end_dt + timedelta(days=2),
                y=y_labels[idx],
                text=f"{duration}д",
                showarrow=False,
                font=dict(size=8, color="#888"),
                xref="x",
                yref="y",
                xanchor="left",
            )

    # ── MILESTONES ────────────────────────────────────────────────────────
    if layers.get("milestones"):
        ms_x: list[datetime] = []
        ms_y: list[str] = []
        ms_text: list[str] = []
        ms_hover: list[str] = []
        for idx, task in enumerate(display):
            if task.get("duration", 1) == 0:
                dt = _day_to_dt(task["start_day"], project_start_date)
                ms_x.append(dt)
                ms_y.append(y_labels[idx])
                ms_text.append(task["name"])
                date_str = day_to_date(task["start_day"], project_start_date)
                ms_hover.append(
                    f"<b>{task['name']}</b><br>"
                    f"Дата: {date_str}<br>"
                    f"Ден: {task['start_day']}"
                )
        if ms_x:
            show_ms_legend = "milestones" not in legend_added
            legend_added.add("milestones")
            fig.add_trace(go.Scatter(
                x=ms_x,
                y=ms_y,
                mode="markers+text",
                marker=dict(
                    symbol="diamond",
                    size=14,
                    color="gold",
                    line=dict(color="black", width=1),
                ),
                text=ms_text,
                textposition="top center",
                textfont=dict(size=9),
                name="Етапи",
                showlegend=show_ms_legend,
                legendgroup="milestones",
                hoverinfo="text",
                hovertext=ms_hover,
            ))

    # ── DEPENDENCIES (arrows) ────────────────────────────────────────────
    if layers.get("dependencies") and len(display) <= 100:
        _add_dependency_arrows(fig, display, y_labels, task_lookup, project_start_date)

    # ── PHASE SEPARATORS ─────────────────────────────────────────────────
    if layers.get("phase_separators"):
        design_end_day = 0
        for task in schedule_data:
            if task.get("phase") == "design":
                end_d = task.get(
                    "end_day",
                    task.get("start_day", 0) + task.get("duration", 0),
                )
                design_end_day = max(design_end_day, end_d)
        if design_end_day > 0:
            boundary_dt = _day_to_dt(design_end_day, project_start_date)
            fig.add_vline(
                x=boundary_dt,
                line_dash="dash",
                line_color="red",
                line_width=1,
            )
            fig.add_annotation(
                x=boundary_dt,
                y=1.05,
                yref="paper",
                text="Край на проектирането",
                showarrow=False,
                font=dict(size=10, color="red"),
            )

    # ── TODAY LINE ────────────────────────────────────────────────────────
    if layers.get("today_line"):
        _add_today_line(fig, schedule_data, project_start_date)

    # ── SELECTED TASK HIGHLIGHT ──────────────────────────────────────────
    if selected_task_id:
        for idx, task in enumerate(display):
            if task.get("id") == selected_task_id:
                s_dt = _day_to_dt(task["start_day"], project_start_date)
                e_day = task.get(
                    "end_day",
                    task["start_day"] + max(task.get("duration", 1), 1) - 1,
                )
                e_dt = _day_to_dt(e_day, project_start_date)
                fig.add_shape(
                    type="rect",
                    x0=s_dt,
                    x1=e_dt + timedelta(days=1),
                    y0=idx - 0.4,
                    y1=idx + 0.4,
                    line=dict(color="#FFD700", width=3),
                    fillcolor="rgba(255,215,0,0.12)",
                    xref="x",
                    yref="y",
                )
                break

    # ── LAYOUT ────────────────────────────────────────────────────────────
    if view_mode == "months":
        tick_fmt = "%b %Y"
        dtick = "M1"
    elif view_mode == "weeks":
        tick_fmt = "%d %b"
        dtick = 604_800_000
    else:
        tick_fmt = "%d.%m"
        dtick = _MS_PER_DAY

    fig.update_layout(
        height=max(400, len(display) * 30 + 120),
        margin=dict(l=420, r=50, t=60, b=80),
        xaxis=dict(
            title="",
            type="date",
            rangeslider=dict(visible=True, thickness=0.05),
            tickformat=tick_fmt,
            gridcolor="#E5E5E5",
            dtick=dtick,
        ),
        yaxis=dict(
            autorange="reversed",
            tickfont=dict(size=11),
            categoryorder="array",
            categoryarray=y_labels,
        ),
        showlegend=True,
        legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5),
        plot_bgcolor="white",
        paper_bgcolor="white",
        bargap=0.3,
        barmode="overlay",
        font=dict(family="Arial, sans-serif", size=12),
        title=dict(text="Линеен график (Gantt)", font=dict(size=16)),
    )

    return fig


# ---------------------------------------------------------------------------
# Private helpers for chart layers
# ---------------------------------------------------------------------------


def _is_subtask(task: dict, schedule_data: list[dict]) -> bool:
    """Return True if *task* is a sub-activity of another task."""
    pid = task.get("parent_id")
    if not pid:
        return False
    return any(t["id"] == pid for t in schedule_data)


def _add_dependency_arrows(
    fig: go.Figure,
    display: list[dict],
    y_labels: list[str],
    task_lookup: dict[str, dict],
    project_start_date: str,
) -> None:
    """Draw dependency arrows between predecessor end → successor start."""
    display_ids = {t.get("id") for t in display}
    for idx, task in enumerate(display):
        deps = task.get("dependencies", [])
        if not deps:
            continue
        succ_start = _day_to_dt(task["start_day"], project_start_date)
        succ_y = y_labels[idx]
        for dep_id in deps:
            if dep_id not in task_lookup or dep_id not in display_ids:
                continue
            pred = task_lookup[dep_id]
            pred_idx = next(
                (i for i, t in enumerate(display) if t.get("id") == dep_id),
                None,
            )
            if pred_idx is None:
                continue
            pred_end_day = pred.get(
                "end_day",
                pred.get("start_day", 0) + max(pred.get("duration", 1), 1) - 1,
            )
            pred_end = _day_to_dt(pred_end_day, project_start_date)
            fig.add_annotation(
                x=succ_start,
                y=succ_y,
                ax=pred_end,
                ay=y_labels[pred_idx],
                xref="x",
                yref="y",
                axref="x",
                ayref="y",
                showarrow=True,
                arrowhead=2,
                arrowsize=1,
                arrowwidth=1,
                arrowcolor="#888",
                standoff=5,
            )


def _add_today_line(
    fig: go.Figure,
    schedule_data: list[dict],
    project_start_date: str,
) -> None:
    """Add a green vertical line for today's date (if within range)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    proj_start = datetime.strptime(project_start_date, "%Y-%m-%d")
    max_end_day = max(
        (
            t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
            for t in schedule_data
        ),
        default=0,
    )
    if max_end_day == 0:
        return
    proj_end = _day_to_dt(max_end_day, project_start_date)
    if proj_start <= today <= proj_end:
        today_str = today.strftime("%d.%m.%Y")
        fig.add_vline(
            x=today,
            line_color="green",
            line_width=2,
        )
        fig.add_annotation(
            x=today,
            y=1.05,
            yref="paper",
            text=f"Днес ({today_str})",
            showarrow=False,
            font=dict(size=10, color="green"),
        )


# ---------------------------------------------------------------------------
# Task detail panel
# ---------------------------------------------------------------------------


def create_task_detail_panel(
    task: dict,
    schedule_data: list[dict],
    start_date: str,
) -> str:
    """Generate Markdown with full details for a selected task."""
    task_lookup = {t["id"]: t for t in schedule_data}
    end_day = task.get(
        "end_day",
        task.get("start_day", 0) + max(task.get("duration", 1), 1) - 1,
    )

    lines: list[str] = [f"### {task['name']}"]
    lines.append(
        f"**Тип:** {get_type_label(task.get('type', ''))} "
        f"| **Фаза:** {task.get('phase', '—')}"
    )
    if task.get("diameter"):
        lines.append(
            f"**DN:** {task['diameter']} | **Дължина:** {task.get('length_m', '—')}м"
        )
    lines.append(f"**Екип:** {task.get('team', '—')}")
    lines.append(
        f"**Начало:** {day_to_date(task['start_day'], start_date)} "
        f"(ден {task['start_day']})"
    )
    lines.append(
        f"**Край:** {day_to_date(end_day, start_date)} (ден {end_day})"
    )
    lines.append(f"**Продължителност:** {task.get('duration', 0)} дни")
    crit = "Да 🔴" if task.get("is_critical") else "Не"
    lines.append(f"**Критичен път:** {crit}")

    # Dependencies
    deps = task.get("dependencies", [])
    if deps:
        dep_names = [
            f"{d} ({task_lookup[d]['name']})" if d in task_lookup else d
            for d in deps
        ]
        lines.append(f"**Зависи от:** {', '.join(dep_names)}")

    # Successors
    tid = task.get("id", "")
    successors = [
        t for t in schedule_data if tid in t.get("dependencies", [])
    ]
    if successors:
        succ_names = [f"{s['id']} ({s['name']})" for s in successors]
        lines.append(f"**Следващи:** {', '.join(succ_names)}")

    # Sub-activities table
    subs = task.get("sub_activities", [])
    if subs:
        lines.append("")
        lines.append("**Поддейности:**")
        lines.append("")
        lines.append("| Дейност | Начало | Край | Дни |")
        lines.append("|---------|--------|------|-----|")
        for sub in subs:
            s_end = sub.get(
                "end_day",
                sub.get("start_day", 0) + max(sub.get("duration", 1), 1) - 1,
            )
            lines.append(
                f"| {sub['name']} "
                f"| {day_to_date(sub['start_day'], start_date)} "
                f"| {day_to_date(s_end, start_date)} "
                f"| {sub.get('duration', '—')} |"
            )

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Schedule statistics
# ---------------------------------------------------------------------------


def get_schedule_stats(schedule_data: list[dict]) -> dict[str, Any]:
    """Compute summary statistics for a schedule.

    Returns dict with keys: total_tasks, critical_count, total_days,
    teams, type_breakdown.
    """
    if not schedule_data:
        return {
            "total_tasks": 0,
            "critical_count": 0,
            "total_days": 0,
            "teams": [],
            "type_breakdown": {},
        }

    critical = [t for t in schedule_data if t.get("is_critical")]
    teams = sorted(
        {t.get("team", "—") for t in schedule_data if t.get("team")} - {"—"}
    )

    max_end = max(
        (
            t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
            for t in schedule_data
        ),
        default=0,
    )

    # Breakdown by type
    type_breakdown: dict[str, dict[str, int]] = {}
    for t in schedule_data:
        tp = t.get("type", "other")
        if tp not in type_breakdown:
            type_breakdown[tp] = {"count": 0, "days": 0}
        type_breakdown[tp]["count"] += 1
        type_breakdown[tp]["days"] += t.get("duration", 0)

    return {
        "total_tasks": len(schedule_data),
        "critical_count": len(critical),
        "total_days": max_end,
        "teams": teams,
        "type_breakdown": type_breakdown,
    }


# ---------------------------------------------------------------------------
# Demo schedule generator
# ---------------------------------------------------------------------------


def generate_demo_schedule() -> list[dict]:
    """Generate a realistic engineering project demo schedule (~35 tasks).

    Uses КСС-style naming with real street names, DN diameters, and
    О.Т. (осови точки) references matching Bulgarian tender conventions.

    Covers: Design → Mobilization → Protocol obr.2 → Water (3 branches) →
    Sewage (2 collectors) → KPS → Road works (3 zones) → Electrical →
    Completion → Act obr.15, plus Supervision.

    Total: ~780 days, critical path marked.
    """
    tasks: list[dict] = []

    # =====================================================================
    # PHASE 1: DESIGN (day 1–260)
    # =====================================================================
    tasks.append({
        "id": "П00",
        "name": "Фаза Проектиране",
        "type": "design",
        "phase": "design",
        "start_day": 1,
        "end_day": 260,
        "duration": 260,
        "team": "Проектант",
        "parent_id": None,
        "dependencies": [],
        "is_critical": True,
        "sub_activities": [
            {"id": "П01", "name": "Геодезия и трасиране", "type": "design",
             "start_day": 1, "end_day": 40, "duration": 40, "parent_id": "П00"},
            {"id": "П02", "name": "ИГ проучвания", "type": "design",
             "start_day": 20, "end_day": 80, "duration": 61, "parent_id": "П00"},
            {"id": "П03", "name": "Работни проекти ВиК", "type": "design",
             "start_day": 70, "end_day": 200, "duration": 131, "parent_id": "П00"},
            {"id": "П04", "name": "ВОБД и съгласуване", "type": "design",
             "start_day": 190, "end_day": 240, "duration": 51, "parent_id": "П00"},
            {"id": "П05", "name": "Сметна документация", "type": "design",
             "start_day": 230, "end_day": 260, "duration": 31, "parent_id": "П00"},
        ],
    })

    # =====================================================================
    # MOBILIZATION (day 261–270)
    # =====================================================================
    tasks.append({
        "id": "М01",
        "name": "Мобилизация на техника и персонал",
        "type": "mobilization",
        "phase": "construction",
        "start_day": 261,
        "end_day": 270,
        "duration": 10,
        "team": "Всички",
        "parent_id": None,
        "dependencies": ["П00"],
        "is_critical": True,
    })

    # =====================================================================
    # MILESTONE: Protocol obr.2 (day 271)
    # =====================================================================
    tasks.append({
        "id": "МС01",
        "name": "Протокол обр.2 — Откриване строителна площадка",
        "type": "completion",
        "phase": "construction",
        "start_day": 271,
        "end_day": 271,
        "duration": 0,
        "team": "\u2014",
        "parent_id": None,
        "dependencies": ["М01"],
        "is_critical": True,
    })

    # =====================================================================
    # WATER SUPPLY — 3 parallel branches (КСС naming)
    # =====================================================================
    tasks.append({
        "id": "В01",
        "name": "Кл.1 Водопровод PE-HD Ф225 ул. Ал. Стамболийски (О.Т.45\u2013О.Т.62)",
        "type": "water_pipe",
        "phase": "construction",
        "start_day": 271,
        "end_day": 355,
        "duration": 85,
        "team": "ЕВ1",
        "diameter": 225,
        "length_m": 826,
        "parent_id": None,
        "dependencies": ["МС01"],
        "is_critical": False,
        "sub_activities": [
            {"id": "В01.1", "name": "Изкопи и укрепване", "type": "water_pipe",
             "start_day": 271, "end_day": 298, "duration": 28, "parent_id": "В01"},
            {"id": "В01.2", "name": "Полагане PE-HD Ф225", "type": "water_pipe",
             "start_day": 296, "end_day": 328, "duration": 33, "parent_id": "В01"},
            {"id": "В01.3", "name": "Засипка и уплътняване", "type": "water_pipe",
             "start_day": 326, "end_day": 342, "duration": 17, "parent_id": "В01"},
            {"id": "В01.4", "name": "Изпитване на водоплътност", "type": "water_pipe",
             "start_day": 343, "end_day": 346, "duration": 4, "parent_id": "В01"},
            {"id": "В01.5", "name": "Дезинфекция и промиване", "type": "water_pipe",
             "start_day": 347, "end_day": 351, "duration": 5, "parent_id": "В01"},
            {"id": "В01.6", "name": "Почистване и СВО", "type": "water_pipe",
             "start_day": 352, "end_day": 355, "duration": 4, "parent_id": "В01"},
        ],
    })

    tasks.append({
        "id": "В02",
        "name": "Кл.2 Водопровод PE-HD Ф110 ул. Хр. Ботев (О.Т.62\u2013О.Т.78)",
        "type": "water_pipe",
        "phase": "construction",
        "start_day": 271,
        "end_day": 320,
        "duration": 50,
        "team": "ЕВ2",
        "diameter": 110,
        "length_m": 520,
        "parent_id": None,
        "dependencies": ["МС01"],
        "is_critical": False,
        "sub_activities": [
            {"id": "В02.1", "name": "Изкопи и укрепване", "type": "water_pipe",
             "start_day": 271, "end_day": 293, "duration": 23, "parent_id": "В02"},
            {"id": "В02.2", "name": "Полагане PE-HD Ф110", "type": "water_pipe",
             "start_day": 291, "end_day": 312, "duration": 22, "parent_id": "В02"},
            {"id": "В02.3", "name": "Засипка и уплътняване", "type": "water_pipe",
             "start_day": 310, "end_day": 317, "duration": 8, "parent_id": "В02"},
            {"id": "В02.4", "name": "Дезинфекция и промиване", "type": "water_pipe",
             "start_day": 318, "end_day": 320, "duration": 3, "parent_id": "В02"},
        ],
    })

    tasks.append({
        "id": "В03",
        "name": "Кл.3 Водопровод PE-HD Ф90 ул. В. Левски (О.Т.78\u2013О.Т.95)",
        "type": "water_pipe",
        "phase": "construction",
        "start_day": 271,
        "end_day": 304,
        "duration": 34,
        "team": "ЕВ3",
        "diameter": 90,
        "length_m": 380,
        "parent_id": None,
        "dependencies": ["МС01"],
        "is_critical": False,
        "sub_activities": [
            {"id": "В03.1", "name": "Изкопи и укрепване", "type": "water_pipe",
             "start_day": 271, "end_day": 288, "duration": 18, "parent_id": "В03"},
            {"id": "В03.2", "name": "Полагане PE-HD Ф90", "type": "water_pipe",
             "start_day": 286, "end_day": 298, "duration": 13, "parent_id": "В03"},
            {"id": "В03.3", "name": "Засипка и уплътняване", "type": "water_pipe",
             "start_day": 297, "end_day": 301, "duration": 5, "parent_id": "В03"},
            {"id": "В03.4", "name": "Дезинфекция и промиване", "type": "water_pipe",
             "start_day": 302, "end_day": 304, "duration": 3, "parent_id": "В03"},
        ],
    })

    # =====================================================================
    # SEWAGE — 2 collectors (sequential, bottom-up) (КСС naming)
    # =====================================================================
    tasks.append({
        "id": "К01",
        "name": "Главен колектор I DN400 PVC ул. Ал. Стамболийски (РШ1\u2013РШ15)",
        "type": "sewer",
        "phase": "construction",
        "start_day": 271,
        "end_day": 560,
        "duration": 290,
        "team": "ЕК1",
        "diameter": 400,
        "length_m": 1200,
        "parent_id": None,
        "dependencies": ["МС01"],
        "is_critical": True,
        "sub_activities": [
            {"id": "К01.1", "name": "Подготовка и разкъртване", "type": "sewer",
             "start_day": 271, "end_day": 290, "duration": 20, "parent_id": "К01"},
            {"id": "К01.2", "name": "Монтаж DN400 PVC + РШ", "type": "sewer",
             "start_day": 288, "end_day": 535, "duration": 248, "parent_id": "К01"},
            {"id": "К01.3", "name": "Засипка и изпитване канал", "type": "sewer",
             "start_day": 533, "end_day": 560, "duration": 28, "parent_id": "К01"},
        ],
    })

    tasks.append({
        "id": "К02",
        "name": "Вторичен колектор DN315 PVC ул. Хр. Ботев (РШ15\u2013РШ22)",
        "type": "sewer",
        "phase": "construction",
        "start_day": 561,
        "end_day": 640,
        "duration": 80,
        "team": "ЕК2",
        "diameter": 315,
        "length_m": 680,
        "parent_id": None,
        "dependencies": ["К01"],
        "is_critical": False,
        "sub_activities": [
            {"id": "К02.1", "name": "Подготовка и разкъртване", "type": "sewer",
             "start_day": 561, "end_day": 572, "duration": 12, "parent_id": "К02"},
            {"id": "К02.2", "name": "Монтаж DN315 PVC + РШ", "type": "sewer",
             "start_day": 570, "end_day": 628, "duration": 59, "parent_id": "К02"},
            {"id": "К02.3", "name": "Засипка и изпитване канал", "type": "sewer",
             "start_day": 626, "end_day": 640, "duration": 15, "parent_id": "К02"},
        ],
    })

    # =====================================================================
    # KPS (pump station + pressure pipe)
    # =====================================================================
    tasks.append({
        "id": "КПС01",
        "name": "КПС + Тласкател DN110 PE-HD (до ПСОВ)",
        "type": "kps",
        "phase": "construction",
        "start_day": 561,
        "end_day": 680,
        "duration": 120,
        "team": "ЕКПС",
        "parent_id": None,
        "dependencies": ["К01"],
        "is_critical": True,
    })

    # =====================================================================
    # ROAD WORKS — 3 zones (Rolling Wave with LAG) (КСС naming)
    # =====================================================================
    tasks.append({
        "id": "Р01",
        "name": "Възстановяване настилки ул. Ал. Стамболийски",
        "type": "road",
        "phase": "construction",
        "start_day": 375,
        "end_day": 435,
        "duration": 61,
        "team": "ЕП1",
        "parent_id": None,
        "dependencies": ["В01"],
        "is_critical": False,
    })

    tasks.append({
        "id": "Р02",
        "name": "Възстановяване настилки ул. Хр. Ботев",
        "type": "road",
        "phase": "construction",
        "start_day": 650,
        "end_day": 710,
        "duration": 61,
        "team": "ЕП2",
        "parent_id": None,
        "dependencies": ["К02"],
        "is_critical": False,
    })

    tasks.append({
        "id": "Р03",
        "name": "Възстановяване настилки ул. В. Левски",
        "type": "road",
        "phase": "construction",
        "start_day": 681,
        "end_day": 740,
        "duration": 60,
        "team": "ЕП3",
        "parent_id": None,
        "dependencies": ["КПС01"],
        "is_critical": True,
    })

    # =====================================================================
    # ELECTRICAL
    # =====================================================================
    tasks.append({
        "id": "Е01",
        "name": "Преместване ЕЛ/ТТ кабели (ул. Ал. Стамболийски)",
        "type": "electrical",
        "phase": "construction",
        "start_day": 380,
        "end_day": 460,
        "duration": 81,
        "team": "ЕЕТТ",
        "parent_id": None,
        "dependencies": ["МС01"],
        "is_critical": False,
    })

    # =====================================================================
    # COMPLETION
    # =====================================================================
    tasks.append({
        "id": "З01",
        "name": "Пусково-наладъчни работи и 72ч проби",
        "type": "completion",
        "phase": "construction",
        "start_day": 741,
        "end_day": 779,
        "duration": 39,
        "team": "Всички",
        "parent_id": None,
        "dependencies": ["Р03"],
        "is_critical": True,
    })

    # =====================================================================
    # MILESTONE: Act obr.15 (day 780)
    # =====================================================================
    tasks.append({
        "id": "МС02",
        "name": "Констативен акт обр.15 — Годност за приемане",
        "type": "completion",
        "phase": "construction",
        "start_day": 780,
        "end_day": 780,
        "duration": 0,
        "team": "\u2014",
        "parent_id": None,
        "dependencies": ["З01"],
        "is_critical": True,
    })

    # =====================================================================
    # SUPERVISION (entire construction period)
    # =====================================================================
    tasks.append({
        "id": "Н01",
        "name": "Авторски надзор",
        "type": "supervision",
        "phase": "supervision",
        "start_day": 271,
        "end_day": 780,
        "duration": 510,
        "team": "Проектант",
        "parent_id": None,
        "dependencies": ["МС01"],
        "is_critical": False,
    })

    return tasks
