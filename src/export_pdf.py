"""PDF export for construction schedules (A3 landscape Gantt)."""

from __future__ import annotations


def export_to_pdf(
    schedule_data: list[dict], project_name: str
) -> bytes | None:
    """Export schedule data to a PDF file (A3 landscape).

    Args:
        schedule_data: List of task dicts with schedule info.
        project_name: Name of the project for the title.

    Returns:
        PDF file as bytes, or None if not yet implemented.
    """
    # Placeholder — will be implemented with reportlab in a future step
    return None
