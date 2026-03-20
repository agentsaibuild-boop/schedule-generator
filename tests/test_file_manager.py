"""Unit tests for FileManager — non-classify helpers.

Covers: _serialize_value, _copy_json_txt, _convert_csv,
        get_project_summary, set_project_path, get_conversion_status.

FAILURE означава: src/file_manager.py е счупена —
конвертирането на CSV/JSON/TXT файлове или скенирането на проектна папка
ще върне грешни резултати и AI-ът ще работи с непълни данни.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_manager import FileManager, _serialize_value


# ---------------------------------------------------------------------------
# _serialize_value (pure utility)
# ---------------------------------------------------------------------------

def test_serialize_none():
    assert _serialize_value(None) is None


def test_serialize_int():
    assert _serialize_value(42) == 42


def test_serialize_float():
    assert _serialize_value(3.14) == 3.14


def test_serialize_bool_true():
    assert _serialize_value(True) is True


def test_serialize_bool_false():
    assert _serialize_value(False) is False


def test_serialize_string():
    assert _serialize_value("hello") == "hello"


def test_serialize_datetime():
    dt = datetime(2026, 3, 20, 12, 0, 0)
    result = _serialize_value(dt)
    assert isinstance(result, str)
    assert "2026" in result


def test_serialize_other_type_to_str():
    class Custom:
        def __str__(self):
            return "custom_value"
    assert _serialize_value(Custom()) == "custom_value"


# ---------------------------------------------------------------------------
# _copy_json_txt — JSON path
# ---------------------------------------------------------------------------

def test_copy_json_valid():
    fm = FileManager()
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
        json.dump({"key": "value", "num": 42}, f, ensure_ascii=False)
        fpath = f.name
    result = fm._copy_json_txt(fpath)
    assert result["status"] == "ok"
    assert result["method"] == "json_copy"
    assert result["data"]["content"] == {"key": "value", "num": 42}
    assert result["data"]["type"] == "json"


def test_copy_json_invalid_json_returns_error():
    fm = FileManager()
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
        f.write("{not valid json")
        fpath = f.name
    result = fm._copy_json_txt(fpath)
    assert result["status"] == "error"
    assert "Invalid JSON" in result["error"]


# ---------------------------------------------------------------------------
# _copy_json_txt — TXT path
# ---------------------------------------------------------------------------

def test_copy_txt_valid():
    fm = FileManager()
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", encoding="utf-8", delete=False) as f:
        f.write("Тестов текст\nВтори ред\n")
        fpath = f.name
    result = fm._copy_json_txt(fpath)
    assert result["status"] == "ok"
    assert result["method"] == "txt_copy"
    assert "Тестов текст" in result["data"]["full_text"]
    assert result["data"]["type"] == "txt"


def test_copy_txt_pages_or_rows_counts_lines():
    fm = FileManager()
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", encoding="utf-8", delete=False) as f:
        f.write("line1\nline2\nline3")
        fpath = f.name
    result = fm._copy_json_txt(fpath)
    assert result["pages_or_rows"] == 3  # 2 newlines + 1


# ---------------------------------------------------------------------------
# _convert_csv
# ---------------------------------------------------------------------------

def test_convert_csv_comma_delimiter():
    fm = FileManager()
    csv_content = "name,value,count\nАлфа,100,5\nБета,200,3\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", delete=False) as f:
        f.write(csv_content)
        fpath = f.name
    result = fm._convert_csv(fpath)
    assert result["status"] == "ok"
    assert result["method"] == "csv"
    rows = result["data"]["sheets"][0]["rows"]
    assert len(rows) == 2
    assert rows[0]["name"] == "Алфа"
    assert rows[1]["value"] == "200"


def test_convert_csv_semicolon_delimiter():
    fm = FileManager()
    csv_content = "Дейност;Дни;Ресурс\nИзкоп;10;Бригада А\nПолагане;5;Бригада Б\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", delete=False) as f:
        f.write(csv_content)
        fpath = f.name
    result = fm._convert_csv(fpath)
    assert result["status"] == "ok"
    rows = result["data"]["sheets"][0]["rows"]
    assert len(rows) == 2
    assert rows[0]["Дейност"] == "Изкоп"


def test_convert_csv_empty_file_returns_error():
    fm = FileManager()
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", delete=False) as f:
        f.write("")
        fpath = f.name
    result = fm._convert_csv(fpath)
    assert result["status"] == "error"


def test_convert_csv_skips_empty_rows():
    fm = FileManager()
    csv_content = "col1,col2\nА,Б\n,,\nВ,Г\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", delete=False) as f:
        f.write(csv_content)
        fpath = f.name
    result = fm._convert_csv(fpath)
    assert result["status"] == "ok"
    rows = result["data"]["sheets"][0]["rows"]
    # Empty row should be filtered out
    assert len(rows) == 2


def test_convert_csv_headers_correct():
    fm = FileManager()
    csv_content = "Task,Duration,Resource\nTask1,10,Team A\n"
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", delete=False) as f:
        f.write(csv_content)
        fpath = f.name
    result = fm._convert_csv(fpath)
    headers = result["data"]["sheets"][0]["headers"]
    assert headers == ["Task", "Duration", "Resource"]


def test_convert_csv_cp1251_encoding():
    fm = FileManager()
    csv_content = "Дейност,Дни\nИзкоп,10\n"
    raw = csv_content.encode("cp1251")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(raw)
        fpath = f.name
    result = fm._convert_csv(fpath)
    assert result["status"] == "ok"
    assert result["data"]["encoding"] in ("cp1251", "utf-8-sig", "utf-8", "latin-1")


# ---------------------------------------------------------------------------
# set_project_path
# ---------------------------------------------------------------------------

def test_set_project_path_invalid_returns_not_valid():
    fm = FileManager()
    result = fm.set_project_path("/nonexistent/path/that/does/not/exist")
    assert result["valid"] is False
    assert result["files_count"] == 0


def test_set_project_path_valid_empty_dir():
    fm = FileManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = fm.set_project_path(tmpdir)
        assert result["valid"] is True
        assert result["files_count"] == 0
        assert result["needs_conversion"] == 0


def test_set_project_path_counts_supported_files():
    fm = FileManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a supported file
        Path(tmpdir, "КСС.xlsx").touch()
        Path(tmpdir, "notes.txt").touch()
        Path(tmpdir, "image.png").touch()  # not supported
        result = fm.set_project_path(tmpdir)
        assert result["valid"] is True
        assert result["files_count"] == 2  # xlsx + txt only


# ---------------------------------------------------------------------------
# get_project_summary
# ---------------------------------------------------------------------------

def test_get_project_summary_empty():
    fm = FileManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        fm.set_project_path(tmpdir)
        summary = fm.get_project_summary()
        assert summary["total_files"] == 0
        assert summary["total_size_kb"] == 0
        assert summary["by_type"] == {}


def test_get_project_summary_groups_by_type():
    fm = FileManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "КСС.xlsx").write_text("dummy", encoding="utf-8")
        (Path(tmpdir) / "notes.txt").write_text("note", encoding="utf-8")
        (Path(tmpdir) / "data.csv").write_text("a,b", encoding="utf-8")
        fm.set_project_path(tmpdir)
        summary = fm.get_project_summary()
        assert summary["total_files"] == 3
        assert ".xlsx" in summary["by_type"]
        assert ".txt" in summary["by_type"]
        assert ".csv" in summary["by_type"]


# ---------------------------------------------------------------------------
# get_conversion_status
# ---------------------------------------------------------------------------

def test_get_conversion_status_new_file_is_pending():
    fm = FileManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "doc.txt").write_text("content", encoding="utf-8")
        fm.set_project_path(tmpdir)
        status = fm.get_conversion_status()
        assert status["total"] == 1
        assert status["pending"] == 1
        assert status["converted"] == 0


def test_get_conversion_status_converted_file_not_pending():
    fm = FileManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        txt_path = Path(tmpdir) / "test.txt"
        txt_path.write_text("hello", encoding="utf-8")
        fm.set_project_path(tmpdir)

        # Simulate converting via _copy_json_txt and saving result
        result = fm._copy_json_txt(str(txt_path))
        assert result["status"] == "ok"

        # Manually update manifest to mark as converted
        stat = txt_path.stat()
        mtime = (
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="seconds")
        )
        fm._manifest.setdefault("files", {})["test.txt"] = {
            "original_size": stat.st_size,
            "original_modified": mtime,
            "status": "ok",
            "conversion_method": "txt_copy",
        }

        status = fm.get_conversion_status()
        assert status["converted"] == 1
        assert status["pending"] == 0


if __name__ == "__main__":
    tests = [
        test_serialize_none,
        test_serialize_int,
        test_serialize_float,
        test_serialize_bool_true,
        test_serialize_bool_false,
        test_serialize_string,
        test_serialize_datetime,
        test_serialize_other_type_to_str,
        test_copy_json_valid,
        test_copy_json_invalid_json_returns_error,
        test_copy_txt_valid,
        test_copy_txt_pages_or_rows_counts_lines,
        test_convert_csv_comma_delimiter,
        test_convert_csv_semicolon_delimiter,
        test_convert_csv_empty_file_returns_error,
        test_convert_csv_skips_empty_rows,
        test_convert_csv_headers_correct,
        test_convert_csv_cp1251_encoding,
        test_set_project_path_invalid_returns_not_valid,
        test_set_project_path_valid_empty_dir,
        test_set_project_path_counts_supported_files,
        test_get_project_summary_empty,
        test_get_project_summary_groups_by_type,
        test_get_conversion_status_new_file_is_pending,
        test_get_conversion_status_converted_file_not_pending,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:
            import traceback
            print(f"  ERROR {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
