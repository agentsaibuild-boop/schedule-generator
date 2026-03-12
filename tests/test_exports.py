"""Test PDF and XML export with demo schedule data."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Fix Windows console encoding for Cyrillic output
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_pdf import export_to_pdf
from src.export_xml import export_to_mspdi_xml
from src.gantt_chart import generate_demo_schedule

NAMESPACE = "http://schemas.microsoft.com/project"


def test_exports():
    """Test PDF and XML export with demo data."""
    demo = generate_demo_schedule()
    project_name = "Тестов проект"
    start_date = "2026-06-01"

    errors = []

    # --- Test PDF ---
    print("Testing PDF export...")
    try:
        pdf = export_to_pdf(demo, project_name, start_date=start_date)
        if pdf is None:
            errors.append("PDF export returned None")
        elif len(pdf) == 0:
            errors.append("PDF export returned empty bytes")
        else:
            # Check PDF header
            if not pdf[:5] == b"%PDF-":
                errors.append(f"Invalid PDF header: {pdf[:20]}")
            else:
                print(f"  PDF: {len(pdf):,} bytes - OK")

                # Save test PDF
                out_dir = Path(__file__).parent / "output"
                out_dir.mkdir(exist_ok=True)
                (out_dir / "test_schedule.pdf").write_bytes(pdf)
                print(f"  Saved to: {out_dir / 'test_schedule.pdf'}")
    except Exception as exc:
        errors.append(f"PDF export raised: {exc}")

    # --- Test XML ---
    print("\nTesting XML export...")
    try:
        xml_bytes = export_to_mspdi_xml(demo, project_name, start_date=start_date)
        if xml_bytes is None:
            errors.append("XML export returned None")
        elif len(xml_bytes) == 0:
            errors.append("XML export returned empty bytes")
        else:
            print(f"  XML: {len(xml_bytes):,} bytes")

            # Parse and validate structure
            root = ET.fromstring(xml_bytes)

            # Check namespace
            if not root.tag.endswith("Project"):
                errors.append(f"Invalid root tag: {root.tag}")

            # Check SaveVersion
            sv = root.find(f"{{{NAMESPACE}}}SaveVersion")
            if sv is None or sv.text != "14":
                errors.append(f"SaveVersion should be 14, got: {sv.text if sv else 'None'}")

            # Check DurationFormat
            df = root.find(f"{{{NAMESPACE}}}DurationFormat")
            if df is None or df.text != "5":
                errors.append(f"DurationFormat should be 5, got: {df.text if df else 'None'}")

            # Check tasks
            tasks = root.findall(f".//{{{NAMESPACE}}}Task")
            if len(tasks) == 0:
                errors.append("No tasks found in XML")
            else:
                print(f"  Tasks: {len(tasks)} (including root)")

            # Check UID=0 root task
            root_task = tasks[0] if tasks else None
            if root_task is not None:
                uid_elem = root_task.find(f"{{{NAMESPACE}}}UID")
                if uid_elem is None or uid_elem.text != "0":
                    errors.append("Root task UID should be 0")

            # Check Manual=1 on all tasks
            for task in tasks:
                manual = task.find(f"{{{NAMESPACE}}}Manual")
                if manual is None or manual.text != "1":
                    name_elem = task.find(f"{{{NAMESPACE}}}Name")
                    name = name_elem.text if name_elem is not None else "?"
                    errors.append(f"Task '{name}' missing Manual=1")
                    break

            # Check calendar
            cals = root.findall(f".//{{{NAMESPACE}}}Calendar")
            if len(cals) == 0:
                errors.append("No calendars found in XML")
            else:
                cal_name = cals[0].find(f"{{{NAMESPACE}}}Name")
                print(f"  Calendar: {cal_name.text if cal_name is not None else '?'}")

            # Check resources (should have UID=0 empty resource)
            resources = root.findall(f".//{{{NAMESPACE}}}Resource")
            if len(resources) == 0:
                errors.append("No resources found in XML")
            elif resources[0].find(f"{{{NAMESPACE}}}UID").text != "0":
                errors.append("First resource UID should be 0 (empty resource)")
            else:
                print(f"  Resources: {len(resources)} (including empty)")

            # Check assignments
            assignments = root.findall(f".//{{{NAMESPACE}}}Assignment")
            print(f"  Assignments: {len(assignments)}")

            # Check extended attributes
            ext_attrs = root.findall(f".//{{{NAMESPACE}}}ExtendedAttributes/{{{NAMESPACE}}}ExtendedAttribute")
            print(f"  Custom fields: {len(ext_attrs)}")

            # Check predecessor links exist
            pred_links = root.findall(f".//{{{NAMESPACE}}}PredecessorLink")
            print(f"  Dependencies: {len(pred_links)}")

            if not errors:
                print("  XML: All validations passed - OK")

            # Save test XML
            out_dir = Path(__file__).parent / "output"
            out_dir.mkdir(exist_ok=True)
            (out_dir / "test_schedule.xml").write_bytes(xml_bytes)
            print(f"  Saved to: {out_dir / 'test_schedule.xml'}")

    except Exception as exc:
        errors.append(f"XML export raised: {exc}")

    # --- Summary ---
    print("\n" + "=" * 50)
    if errors:
        print(f"FAILED - {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
    else:
        print("ALL TESTS PASSED")

    assert not errors, f"{len(errors)} export error(s): {'; '.join(errors)}"


if __name__ == "__main__":
    try:
        test_exports()
        sys.exit(0)
    except AssertionError as e:
        print(str(e))
        sys.exit(1)
