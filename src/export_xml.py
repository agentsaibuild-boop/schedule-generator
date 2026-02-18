"""MSPDI XML export for MS Project compatibility."""

from __future__ import annotations


def export_to_mspdi_xml(
    schedule_data: list[dict], project_name: str
) -> bytes | None:
    """Export schedule data to MSPDI XML format.

    Args:
        schedule_data: List of task dicts with schedule info.
        project_name: Name of the project for the XML header.

    Returns:
        XML file as bytes, or None if not yet implemented.
    """
    # Placeholder — will be implemented with xml.etree.ElementTree in a future step
    return None
