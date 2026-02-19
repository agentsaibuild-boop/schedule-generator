"""ВиК График Генератор — Main Streamlit Application.

A local web application for generating construction schedules (Gantt)
for water and sewage infrastructure projects in Bulgaria.

Dual AI system: DeepSeek (worker) + Anthropic Claude (controller).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src.ai_processor import AIProcessor
from src.ai_router import AIRouter
from src.chat_handler import ChatHandler
from src.file_manager import FileManager
from src.gantt_chart import (
    DEFAULT_LAYERS,
    TYPE_LABELS,
    create_gantt_chart,
    create_task_detail_panel,
    day_to_date,
    get_schedule_stats,
    get_type_label,
)
from src.export_pdf import export_to_pdf
from src.export_xml import export_to_mspdi_xml
from src.knowledge_manager import KnowledgeManager
from src.project_manager import ProjectManager
from src.schedule_builder import ScheduleBuilder
from src.self_evolution import SelfEvolution
from src.docs_updater import DocsUpdater


def _ensure_schedule_list(data: object) -> list[dict]:
    """Parse schedule data into a list of task dicts.

    Handles: list[dict] (passthrough), JSON string, markdown-fenced JSON.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, str) or not data.strip():
        return []
    cleaned = data.strip()
    # Strip markdown code fences
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(parsed, dict):
        return parsed.get("tasks", [])
    if isinstance(parsed, list):
        return parsed
    return []


# ---------------------------------------------------------------------------
# Environment & Paths
# ---------------------------------------------------------------------------
load_dotenv()

APP_DIR = Path(__file__).parent
KNOWLEDGE_DIR = APP_DIR / "knowledge"
CONFIG_DIR = APP_DIR / "config"

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ВиК График Генератор",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stChatMessage { max-width: 100%; }
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1 {
        font-size: 1.3rem;
        padding-bottom: 0;
    }
    .stTabs [data-baseweb="tab-list"] button { font-size: 0.95rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Configuration check (for installer deployments)
# ---------------------------------------------------------------------------
def _check_configuration():
    """Check if the application is properly configured."""
    issues = []
    if not os.path.exists(".env"):
        issues.append("missing_env")
    else:
        if not os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY") == "sk-ant-your-key-here":
            issues.append("invalid_anthropic_key")
        if not os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY") == "sk-your-deepseek-key-here":
            issues.append("invalid_deepseek_key")
        if not os.getenv("ADMIN_CODE") or os.getenv("ADMIN_CODE") == "your-admin-code-here":
            issues.append("invalid_admin_code")
    return issues

_config_issues = _check_configuration()
if _config_issues:
    st.error("Приложението не е конфигурирано правилно!")
    if "missing_env" in _config_issues:
        st.warning("Липсва файл `.env`. Стартирайте `install.bat` или копирайте `.env.example` в `.env`.")
    if "invalid_anthropic_key" in _config_issues:
        st.warning("Anthropic API ключът не е валиден. Обърнете се към администратора.")
    if "invalid_deepseek_key" in _config_issues:
        st.warning("DeepSeek API ключът не е валиден. Обърнете се към администратора.")
    if "invalid_admin_code" in _config_issues:
        st.info("Админ кодът не е зададен. Функцията за еволюция няма да работи.")
    st.stop()

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "schedule_data" not in st.session_state:
    builder = ScheduleBuilder()
    st.session_state.schedule_data = builder.build_from_ai_response({})

if "current_schedule" not in st.session_state:
    st.session_state.current_schedule = st.session_state.schedule_data

if "project_path" not in st.session_state:
    st.session_state.project_path = ""

if "project_loaded" not in st.session_state:
    st.session_state.project_loaded = False

if "project_info" not in st.session_state:
    st.session_state.project_info = {}

if "conversion_done" not in st.session_state:
    st.session_state.conversion_done = False

if "welcome_shown" not in st.session_state:
    st.session_state.welcome_shown = False

if "ai_health" not in st.session_state:
    st.session_state.ai_health = None

if "usage_stats" not in st.session_state:
    st.session_state.usage_stats = {
        "deepseek": {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
        "anthropic": {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
        "total_cost_usd": 0.0,
        "total_calls": 0,
    }

if "current_model" not in st.session_state:
    st.session_state.current_model = None

if "pending_changes" not in st.session_state:
    st.session_state.pending_changes = None

if "evolution_history" not in st.session_state:
    st.session_state.evolution_history = []

if "gantt_layers" not in st.session_state:
    st.session_state.gantt_layers = DEFAULT_LAYERS.copy()

if "selected_task_id" not in st.session_state:
    st.session_state.selected_task_id = None

if "project_start_date" not in st.session_state:
    st.session_state.project_start_date = "2025-04-01"

if "current_project" not in st.session_state:
    st.session_state.current_project = None

if "recent_projects" not in st.session_state:
    st.session_state.recent_projects = []

if "processing" not in st.session_state:
    st.session_state.processing = False

if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False

if "restored_history" not in st.session_state:
    st.session_state.restored_history = []

# ---------------------------------------------------------------------------
# Initialize managers (cached in session state to survive reruns)
# ---------------------------------------------------------------------------
if "router" not in st.session_state:
    _router = AIRouter()
    _router.set_cumulative_path(str(CONFIG_DIR))
    st.session_state.router = _router

if "knowledge_mgr" not in st.session_state:
    st.session_state.knowledge_mgr = KnowledgeManager(str(KNOWLEDGE_DIR))

if "file_mgr" not in st.session_state:
    st.session_state.file_mgr = FileManager()

if "ai_processor" not in st.session_state:
    st.session_state.ai_processor = AIProcessor(
        router=st.session_state.router,
        knowledge_manager=st.session_state.knowledge_mgr,
    )

if "self_evolution" not in st.session_state:
    st.session_state.self_evolution = SelfEvolution(
        app_root=str(APP_DIR),
        router=st.session_state.router,
    )

if "project_manager" not in st.session_state:
    st.session_state.project_manager = ProjectManager(str(APP_DIR))

if "schedule_builder" not in st.session_state:
    st.session_state.schedule_builder = ScheduleBuilder()

if "chat_handler" not in st.session_state:
    st.session_state.chat_handler = ChatHandler(
        ai_processor=st.session_state.ai_processor,
        file_manager=st.session_state.file_mgr,
        knowledge_manager=st.session_state.knowledge_mgr,
        evolution=st.session_state.self_evolution,
        project_manager=st.session_state.project_manager,
        schedule_builder=st.session_state.schedule_builder,
    )

router: AIRouter = st.session_state.router
knowledge_mgr: KnowledgeManager = st.session_state.knowledge_mgr
file_mgr: FileManager = st.session_state.file_mgr
ai_processor: AIProcessor = st.session_state.ai_processor
evolution: SelfEvolution = st.session_state.self_evolution
project_mgr: ProjectManager = st.session_state.project_manager
chat_handler: ChatHandler = st.session_state.chat_handler

# ---------------------------------------------------------------------------
# Health check on first load
# ---------------------------------------------------------------------------
if st.session_state.ai_health is None:
    with st.spinner("Проверка на AI модели..."):
        try:
            st.session_state.ai_health = router.check_health()
        except Exception:
            st.session_state.ai_health = {
                "deepseek": False,
                "anthropic": False,
                "fallback_active": True,
                "fallback_source": "both",
            }

ai_health = st.session_state.ai_health

# Fetch recent projects on first load
if not st.session_state.recent_projects and st.session_state.welcome_shown is False:
    st.session_state.recent_projects = project_mgr.get_recent_projects(5)


def _save_chat_history() -> None:
    """Persist last 10 message pairs (20 entries) to project history.

    Only saves messages that contain actual user interaction (user+assistant pairs).
    Skips system-generated welcome/status messages to prevent duplication on reload.
    """
    proj = st.session_state.get("current_project")
    if not proj:
        return
    pid = proj.get("id")
    if not pid:
        return
    # Only save messages from actual chat interactions (not welcome/status).
    # A valid conversation starts with a user message.
    all_msgs = list(st.session_state.get("messages", []))
    # Filter: keep only messages that are part of user-initiated exchanges
    filtered: list[dict] = []
    seen_user = False
    for msg in all_msgs:
        if msg.get("role") == "user":
            seen_user = True
        if seen_user:
            filtered.append(msg)
    # Keep last 20 entries (≈10 pairs)
    trimmed = filtered[-20:]
    project_mgr.save_progress(pid, {"chat_history": trimmed})


# ---------------------------------------------------------------------------
# Helper: load project by path (shared logic)
# ---------------------------------------------------------------------------
def _load_project_by_path(path: str) -> None:
    """Load a project by path — registers, converts status check, sets state."""
    info = file_mgr.set_project_path(path)
    if not info["valid"]:
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"Папката не съществува или не е достъпна:\n`{path}`",
        })
        return

    st.session_state.project_path = path
    st.session_state.project_loaded = True
    st.session_state.project_info = info
    st.session_state.conversion_done = (
        info["needs_conversion"] == 0 and info["converted_count"] > 0
    )

    # Register in project manager
    project = project_mgr.register_project(path)
    st.session_state.current_project = project

    # Update progress with file counts
    project_mgr.save_progress(project["id"], {
        "files_total": info["files_count"],
        "files_converted": info["converted_count"],
    })

    # If already has a schedule, restore it
    last_schedule = project.get("progress", {}).get("last_schedule")
    if last_schedule:
        parsed = _ensure_schedule_list(last_schedule)
        st.session_state.current_schedule = parsed
        st.session_state.schedule_data = parsed

    # Restore chat history from previous sessions
    saved_history = project.get("progress", {}).get("chat_history", [])
    if saved_history:
        st.session_state.restored_history = saved_history
        st.session_state.messages = []  # Fresh start; old msgs shown in expander
        chat_handler.restore_history(saved_history)
    else:
        st.session_state.restored_history = []

    # Update status based on conversion state
    if info["converted_count"] > 0 and info["needs_conversion"] == 0:
        if project.get("status") == "new":
            project_mgr.save_progress(project["id"], {"status": "analyzed"})

    # Generate welcome/status message (only if NOT auto-restoring from saved history)
    # When restoring from saved history, the old messages are shown in the expander,
    # so we only show a brief status — not a full welcome that would get duplicated.
    project = project_mgr.load_project(project["id"])
    if project:
        welcome = project_mgr.get_welcome_message(project)
    else:
        folder_name = Path(path).name
        welcome = f"Проект **{folder_name}** зареден."

    # Build detailed status message
    msg_parts = [welcome]

    if info["needs_conversion"] > 0:
        msg_parts.append(
            f"\n{info['needs_conversion']} файла чакат конверсия. "
            "Натиснете **Конвертирай файлове** в страничната лента."
        )
    elif info["converted_count"] > 0 and not last_schedule:
        msg_parts.append("\nВсички файлове са конвертирани и готови за анализ.")

    # Only append to messages if this is a fresh load (not auto-restore on refresh).
    # For auto-restore, the welcome_shown flag is set by the main welcome block.
    if not saved_history:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "\n".join(msg_parts),
        })

    # Refresh recent projects list
    st.session_state.recent_projects = project_mgr.get_recent_projects(5)


def _load_project_by_id(project_id: str) -> None:
    """Load a project by ID from history."""
    project = project_mgr.load_project(project_id)
    if not project:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "Проектът не е намерен в историята.",
        })
        return

    _load_project_by_path(project["path"])


# ---------------------------------------------------------------------------
# Helper: run file conversion with progress in chat
# ---------------------------------------------------------------------------
def _run_conversion(force: bool = False) -> None:
    """Execute file conversion and write progress to chat messages."""
    total_files = st.session_state.project_info.get("files_count", 0)
    if total_files == 0:
        return

    status_lines: list[str] = []

    progress_bar = st.sidebar.progress(0, text="Конвертиране...")
    status_area = st.sidebar.empty()

    def progress_cb(current: int, total: int, filename: str, status: str) -> None:
        emoji = {
            "working": "\U0001f504",
            "done": "\u2705",
            "skip": "\u23ed\ufe0f",
            "error": "\u274c",
        }.get(status, "\u23f3")

        status_lines.append(f"{emoji} {filename}")
        progress_bar.progress(current / total, text=f"{current}/{total}: {filename}")
        status_area.caption("\n".join(status_lines[-5:]))

    result = file_mgr.convert_all(
        ai_processor=ai_processor,
        progress_callback=progress_cb,
        force=force,
    )

    progress_bar.empty()
    status_area.empty()

    # Build summary chat message
    lines = ["**Конвертиране завършено!**\n"]
    converted_count = result["converted"]
    skipped_count = result["skipped"]
    failed_count = result["failed"]

    if converted_count > 0:
        lines.append(f"Конвертирани: **{converted_count}** файла")
    if skipped_count > 0:
        lines.append(f"Пропуснати (вече готови): **{skipped_count}**")
    if failed_count > 0:
        lines.append(f"Грешки: **{failed_count}**")

    details = []
    for r in result.get("results", []):
        if r["action"] == "converted":
            ext = Path(r["file"]).suffix.lower()
            ext_label = {
                ".pdf": "PDF", ".xlsx": "Excel", ".xls": "Excel",
                ".docx": "Word", ".csv": "CSV", ".json": "JSON", ".txt": "TXT",
            }.get(ext, ext)
            method = r.get("method", "")
            detail = r.get("detail", "")
            method_str = " (OCR)" if method == "ocr_vision" else ""
            details.append(f"- {r['file']} \u2192 {ext_label}{method_str}: {detail}")
        elif r["action"] == "failed":
            details.append(f"- {r['file']} \u2192 \u274c {r.get('error', '')}")

    if details:
        lines.append("\n" + "\n".join(details))

    if failed_count == 0 and converted_count > 0:
        lines.append("\nМога да анализирам документите. Какъв график ви трябва?")
    elif converted_count == 0 and skipped_count > 0:
        lines.append("\nВсички файлове вече са конвертирани и готови за анализ.")

    st.session_state.messages.append({
        "role": "assistant",
        "content": "\n".join(lines),
    })

    st.session_state.conversion_done = True
    new_info = file_mgr.set_project_path(st.session_state.project_path)
    st.session_state.project_info = new_info

    # Update project manager with conversion progress
    if st.session_state.current_project:
        pid = st.session_state.current_project.get("id")
        if pid:
            project_mgr.save_progress(pid, {
                "status": "analyzed",
                "files_converted": new_info["converted_count"],
                "files_total": new_info["files_count"],
            })


# ---------------------------------------------------------------------------
# Auto-restore last active project if session was lost (e.g. page refresh)
# ---------------------------------------------------------------------------
if not st.session_state.project_loaded:
    _last_active = project_mgr.get_last_active_project()
    if _last_active and Path(_last_active.get("path", "")).is_dir():
        _load_project_by_path(_last_active["path"])
        # Restore schedule from project history if available
        _restored_sched = _last_active.get("progress", {}).get("last_schedule")
        if _restored_sched:
            _parsed = _ensure_schedule_list(_restored_sched)
            st.session_state.current_schedule = _parsed
            st.session_state.schedule_data = _parsed
            chat_handler.current_schedule = _parsed
        # Note: chat history restore is already handled in _load_project_by_path
        # Mark welcome as shown to prevent duplicate welcome message on refresh
        st.session_state.welcome_shown = True


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("# \U0001f4d0 ВиК График Генератор")
    st.caption("РАИ Комерс | v0.3 — Dual AI + Projects")

    st.divider()

    # --- AI Status ---
    st.subheader("\U0001f916 AI Статус")

    ds_status = "\U0001f7e2 Работи" if ai_health.get("deepseek") else "\U0001f534 Недостъпен"
    an_status = "\U0001f7e2 Работи" if ai_health.get("anthropic") else "\U0001f534 Недостъпен"
    st.caption(f"DeepSeek (работник): {ds_status}")
    st.caption(f"Anthropic (контрольор): {an_status}")

    if ai_health.get("fallback_active"):
        src = ai_health.get("fallback_source", "")
        if src == "deepseek":
            st.warning("DeepSeek е недостъпен — Anthropic поема всички задачи")
        elif src == "anthropic":
            st.warning("Anthropic е недостъпен — DeepSeek поема всички задачи")
        elif src == "both":
            st.error("И двата AI модела са недостъпни!")

    if st.session_state.current_model:
        st.caption(f"Текущ модел: {st.session_state.current_model}")

    # Re-check button
    if st.button("\U0001f504 Провери отново", use_container_width=True, key="health_check"):
        with st.spinner("Проверка..."):
            try:
                st.session_state.ai_health = router.check_health()
            except Exception:
                st.session_state.ai_health = {
                    "deepseek": False, "anthropic": False,
                    "fallback_active": True, "fallback_source": "both",
                }
        st.rerun()

    st.divider()

    # --- Cost tracking ---
    st.subheader("\U0001f4b0 Разходи")

    # Session costs
    stats = router.get_usage_stats()
    st.session_state.usage_stats = stats

    st.caption("**Тази сесия:**")
    col_ds, col_an = st.columns(2)
    with col_ds:
        ds_cost = stats["deepseek"]["cost_usd"]
        ds_calls = stats["deepseek"]["calls"]
        st.metric("DeepSeek", f"${ds_cost:.4f}", f"{ds_calls} заявки")
    with col_an:
        an_cost = stats["anthropic"]["cost_usd"]
        an_calls = stats["anthropic"]["calls"]
        st.metric("Anthropic", f"${an_cost:.4f}", f"{an_calls} заявки")

    st.caption(f"Сесия общо: **${stats['total_cost_usd']:.4f}**")

    # Cumulative costs (all-time, persisted)
    cumulative = router.get_cumulative_stats()
    cum_total = cumulative.get("total", 0.0)
    cum_calls = cumulative.get("total_calls", 0)
    cum_ds = cumulative.get("deepseek", 0.0)
    cum_an = cumulative.get("anthropic", 0.0)
    st.caption(
        f"**Общо (всички сесии):** ${cum_total:.4f} "
        f"({cum_calls} заявки)"
    )
    st.caption(f"DS: ${cum_ds:.4f} | AN: ${cum_an:.4f}")

    st.divider()

    # --- Current Project ---
    st.subheader("\U0001f4c2 Текущ проект")
    if st.session_state.project_loaded and st.session_state.current_project:
        cp = st.session_state.current_project
        status_label = project_mgr.get_status_label(cp.get("status", "new"))
        status_emoji = project_mgr.get_status_emoji(cp.get("status", "new"))
        st.write(f"**{cp.get('name', '?')}**")
        st.caption(f"Статус: {status_emoji} {status_label}")
        st.caption(f"Папка: {cp.get('path', '?')}")

        info = st.session_state.project_info
        converted = info.get("converted_count", 0)
        total = info.get("files_count", 0)
        if total > 0:
            st.caption(f"Файлове: {converted}/{total} конвертирани")

        if st.button("\U0001f504 Смени проект", use_container_width=True, key="change_project"):
            _save_chat_history()
            st.session_state.project_loaded = False
            st.session_state.current_project = None
            st.session_state.project_path = ""
            st.session_state.project_info = {}
            st.session_state.conversion_done = False
            st.session_state.messages = []
            st.session_state.restored_history = []
            st.session_state.welcome_shown = False
            st.session_state.recent_projects = project_mgr.get_recent_projects(5)
            chat_handler.clear_history()
            project_mgr.clear_last_active()
            st.rerun()
    else:
        st.caption("Няма зареден проект.")

    st.divider()

    # --- Project path ---
    st.subheader("\U0001f4c1 Зареди проект")

    # Show recent projects in sidebar for quick access
    _sidebar_recent = st.session_state.get("recent_projects", [])
    if _sidebar_recent and not st.session_state.project_loaded:
        st.caption("**Скорошни проекти:**")
        for _i, _proj in enumerate(_sidebar_recent):
            _emoji = project_mgr.get_status_emoji(_proj.get("status", "new"))
            _name = _proj.get("name", "?")
            _ago = _proj.get("time_ago", "")
            _exists = _proj.get("exists", True)
            _btn_label = f"{_emoji} {_name} ({_ago})"
            if not _exists:
                _btn_label += " \u274c"
            if st.button(
                _btn_label,
                use_container_width=True,
                key=f"sidebar_recent_{_i}",
                disabled=not _exists,
            ):
                _load_project_by_path(_proj["path"])
                st.rerun()
        st.divider()

    path_col, browse_col = st.columns([3, 1])
    with path_col:
        project_path_input = st.text_input(
            "Път до проектна папка",
            value=st.session_state.project_path,
            placeholder=r"D:\Проекти\Име на проект",
            help="Пълен път до папката с тендерна документация",
            label_visibility="collapsed",
        )
    with browse_col:
        if st.button("\U0001f4c2", use_container_width=True, help="Избери папка"):
            ps_script = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.Application]::EnableVisualStyles(); "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Изберете проектна папка'; "
                "$f.ShowNewFolderButton = $false; "
                "$f.RootFolder = [System.Environment+SpecialFolder]::MyComputer; "
                "$owner = New-Object System.Windows.Forms.Form; "
                "$owner.TopMost = $true; "
                "$owner.StartPosition = 'CenterScreen'; "
                "$result = $f.ShowDialog($owner); "
                "$owner.Dispose(); "
                "if ($result -eq 'OK') { $f.SelectedPath }"
            )
            try:
                result = subprocess.run(
                    ["powershell", "-STA", "-NoProfile", "-Command", ps_script],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
                chosen = result.stdout.strip()
                if chosen:
                    st.session_state.project_path = chosen
                    st.rerun()
                elif result.returncode != 0:
                    st.warning("Диалогът не можа да се отвори. Въведете пътя ръчно.")
            except subprocess.TimeoutExpired:
                st.warning("Диалогът изтече. Въведете пътя ръчно.")
            except Exception as exc:
                st.warning(f"Грешка при отваряне на диалог: {exc}")

    # --- Load project button ---
    if st.button("Зареди проект", use_container_width=True):
        path_to_load = project_path_input or st.session_state.project_path
        if path_to_load:
            _load_project_by_path(path_to_load)
            st.rerun()
        else:
            st.warning("Моля, изберете папка.")

    # --- Loaded project file details ---
    if st.session_state.project_loaded and st.session_state.project_path:
        if not file_mgr.base_path:
            file_mgr.set_project_path(st.session_state.project_path)

        info = st.session_state.project_info
        summary = file_mgr.get_project_summary()

        if summary["by_type"]:
            type_str = ", ".join(
                f"{ext}: {cnt}" for ext, cnt in sorted(summary["by_type"].items())
            )
            st.caption(f"Файлове: {type_str}")

        needs = info.get("needs_conversion", 0)
        converted = info.get("converted_count", 0)
        total = info.get("files_count", 0)

        if needs > 0:
            st.warning(f"\u26a0\ufe0f {needs} от {total} файла чакат конверсия")
            if st.button("\U0001f504 Конвертирай файлове", use_container_width=True):
                _run_conversion(force=False)
                st.rerun()
        elif converted > 0:
            st.success(f"\u2705 {converted}/{total} файла конвертирани")
        elif total > 0:
            st.info(f"{total} файла намерени")

        if converted > 0:
            if st.button("\U0001f501 Преконвертирай всичко", use_container_width=True):
                _run_conversion(force=True)
                st.rerun()

    st.divider()

    # --- Knowledge stats ---
    st.subheader("\U0001f9e0 Знания")
    k_stats = knowledge_mgr.get_knowledge_stats()
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("Уроци", k_stats["lessons"])
    with col_b:
        st.metric("Методики", k_stats["methodologies"])

    col_c, col_d = st.columns(2)
    with col_c:
        st.metric("Чакащи", k_stats["pending"])
    with col_d:
        st.metric("Референции", k_stats["skill_references"])

    st.divider()

    # --- Self-evolution ---
    st.subheader("\U0001f527 Еволюция")
    evo_history = evolution.get_change_history()
    if evo_history:
        last_change = evo_history[-1]
        last_ts = last_change.get("timestamp", "?")[:16].replace("T", " ")
        last_desc = last_change.get("description", "?")[:40]
        st.caption(f"Последна промяна: {last_ts}")
        st.caption(f"— {last_desc}")
    else:
        st.caption("Няма направени промени.")

    evo_col1, evo_col2 = st.columns(2)
    with evo_col1:
        if st.button("\U0001f4dc История", use_container_width=True, key="evo_history_btn"):
            if evo_history:
                history_lines = ["**История на промените:**\n"]
                for entry in reversed(evo_history[-10:]):
                    ts = entry.get("timestamp", "?")[:16].replace("T", " ")
                    lvl = entry.get("level", "?")
                    desc = entry.get("description", "?")
                    status = entry.get("status", "?")
                    emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534", "rollback": "\u23ea"}.get(lvl, "\u26aa")
                    history_lines.append(f"{emoji} [{ts}] {desc} ({status})")
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "\n".join(history_lines),
                })
                st.rerun()
            else:
                st.info("Няма история.")
    with evo_col2:
        if st.button("\u23ea Върни", use_container_width=True, key="evo_rollback_btn"):
            st.session_state["show_rollback_dialog"] = True
            st.rerun()

    # Rollback dialog
    if st.session_state.get("show_rollback_dialog"):
        if evo_history:
            # Find last backup commit
            last_backup = None
            for entry in reversed(evo_history):
                if entry.get("backup_commit") and entry.get("status") == "applied":
                    last_backup = entry
                    break

            if last_backup:
                commit_short = last_backup["backup_commit"][:8]
                desc = last_backup.get("description", "")
                st.warning(
                    f"\u26a0\ufe0f Ще се върнете към: {commit_short} — {desc}"
                )
                rollback_code = st.text_input(
                    "Въведете админ код:",
                    type="password",
                    key="rollback_admin_code",
                )
                rb_col1, rb_col2 = st.columns(2)
                with rb_col1:
                    if st.button("\u2705 Потвърди", key="confirm_rollback", use_container_width=True):
                        if evolution.verify_admin_code(rollback_code):
                            rb_result = evolution.rollback(last_backup["backup_commit"])
                            if rb_result["success"]:
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": f"\u23ea Успешен rollback към {commit_short}. Рестартирайте приложението.",
                                })
                            else:
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": f"\u274c Rollback неуспешен: {rb_result.get('error', '?')}",
                                })
                            st.session_state["show_rollback_dialog"] = False
                            st.rerun()
                        else:
                            st.error("\u274c Невалиден админ код.")
                with rb_col2:
                    if st.button("\u274c Откажи", key="cancel_rollback", use_container_width=True):
                        st.session_state["show_rollback_dialog"] = False
                        st.rerun()
            else:
                st.info("Няма backup за възстановяване.")
                if st.button("OK", key="no_backup_ok"):
                    st.session_state["show_rollback_dialog"] = False
                    st.rerun()
        else:
            st.info("Няма история за възстановяване.")
            if st.button("OK", key="no_history_ok"):
                st.session_state["show_rollback_dialog"] = False
                st.rerun()

    st.divider()

    # --- Documentation status ---
    st.subheader("\U0001f4dd Документация")
    _docs_updater = DocsUpdater(str(APP_DIR))
    _docs_updates = _docs_updater.check_for_updates()

    if _docs_updates:
        st.warning(f"\U0001f4dd {len(_docs_updates)} документа трябва да се обновят")
        if st.button("Обнови документацията", use_container_width=True, key="docs_update_btn"):
            result = _docs_updater.run_all_updates()
            if result["total"] > 0:
                st.success(f"\u2705 Обновени: {result['total']} секции")
            else:
                st.info("Няма секции за обновяване.")
            st.rerun()
    else:
        st.success("\U0001f4dd Документацията е актуална")

    st.divider()

    # --- Clear chat ---
    if st.button("\U0001f5d1\ufe0f Изчисти чата", use_container_width=True):
        _save_chat_history()
        st.session_state.messages = []
        st.session_state.restored_history = []
        st.session_state.welcome_shown = False
        st.session_state.pending_changes = None
        chat_handler.clear_history()
        st.rerun()

# ---------------------------------------------------------------------------
# Main layout: Chat (left) | Visualization (right)
# ---------------------------------------------------------------------------
chat_col, viz_col = st.columns([45, 55], gap="medium")

# ---------------------------------------------------------------------------
# LEFT COLUMN — Chat
# ---------------------------------------------------------------------------
with chat_col:
    st.markdown("### \U0001f4ac Чат")

    # Welcome message (shown once)
    if not st.session_state.welcome_shown:
        # Build welcome with AI status
        ai_note = ""
        if ai_health.get("deepseek") and ai_health.get("anthropic"):
            ai_note = "\U0001f7e2 Двата AI модела са готови (DeepSeek + Anthropic)."
        elif ai_health.get("fallback_active"):
            src = ai_health.get("fallback_source", "")
            if src == "deepseek":
                ai_note = "\u26a0\ufe0f DeepSeek не е достъпен — работя чрез Anthropic."
            elif src == "anthropic":
                ai_note = "\u26a0\ufe0f Anthropic не е достъпен — работя чрез DeepSeek."
            else:
                ai_note = "\U0001f534 AI моделите не са достъпни. Проверете .env файла."

        # Check for recent projects
        recent = st.session_state.recent_projects
        if recent:
            project_lines = []
            for i, proj in enumerate(recent, 1):
                emoji = project_mgr.get_status_emoji(proj.get("status", "new"))
                name = proj.get("name", "?")
                label = proj.get("status_label", "")
                time_ago = proj.get("time_ago", "")

                # Build detail
                schedule_info = ""
                progress = proj.get("progress", {})
                last_sched = _ensure_schedule_list(progress.get("last_schedule"))
                if last_sched:
                    total_days = max(
                        (
                            t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
                            for t in last_sched
                        ),
                        default=0,
                    )
                    version = progress.get("schedule_version", "")
                    if version:
                        schedule_info = f" ({version}, {total_days}д)"
                    elif total_days > 0:
                        schedule_info = f" ({total_days}д)"

                exists_mark = "" if proj.get("exists", True) else " \u274c"
                project_lines.append(
                    f"{i}. {emoji} **{name}**{schedule_info} — {label} — {time_ago}{exists_mark}"
                )

            projects_text = "\n".join(project_lines)

            welcome = (
                f"Здравейте! Аз съм вашият асистент за строителни графици.\n\n"
                f"{ai_note}\n\n"
                f"**Скорошни проекти:**\n"
                f"{projects_text}\n\n"
                f"Изберете проект с номер (напр. **1**) или заредете нов с 'Зареди D:\\Projects\\...'\n\n"
                "Мога да генерирам графици, да анализирам документи и да експортирам в PDF/XML."
            )
        else:
            welcome = (
                f"Здравейте! Аз съм вашият асистент за строителни графици.\n\n"
                f"{ai_note}\n\n"
                "За да започнете, заредете проект:\n"
                "Напишете: `Зареди D:\\Projects\\ИмеНаПроект`\n\n"
                "Мога да:\n"
                "- Анализирам тендерна документация (PDF, Excel, Word)\n"
                "- Генерирам строителен график (Gantt)\n"
                "- Проверявам спазването на правилата\n"
                "- Експортирам в PDF (A3) и MS Project (XML)\n\n"
                "Всички файлове остават на вашия компютър."
            )

        st.session_state.messages.append(
            {"role": "assistant", "content": welcome}
        )
        st.session_state.welcome_shown = True

    # Display chat history
    chat_container = st.container(height=520)
    with chat_container:
        # Show restored history from previous session in an expander
        restored = st.session_state.get("restored_history", [])
        if restored:
            n_pairs = len(restored) // 2
            last_topic = ""
            for _m in reversed(restored):
                if _m.get("role") == "user":
                    last_topic = _m["content"][:60].replace("\n", " ")
                    break
            summary = f"Заредена история от последната сесия ({n_pairs} размени)"
            if last_topic:
                summary += f". Последна тема: _{last_topic}..._"
            st.info(summary)

            with st.expander("Покажи предишна история", expanded=False):
                for msg in restored:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])
            st.divider()

        # Current session messages
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Stop button (visible when processing is active)
    if st.session_state.get("processing"):
        if st.button(
            "\u23f9 \u0421\u043f\u0440\u0438 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u044f\u0442\u0430",
            type="primary",
            use_container_width=True,
            key="stop_btn",
        ):
            st.session_state.stop_requested = True
            st.session_state.processing = False
            router.stop_requested = True
            st.session_state.messages.append({
                "role": "assistant",
                "content": "\u23f9 \u041e\u043f\u0435\u0440\u0430\u0446\u0438\u044f\u0442\u0430 \u0435 \u0441\u043f\u0440\u044f\u043d\u0430 \u043e\u0442 \u043f\u043e\u0442\u0440\u0435\u0431\u0438\u0442\u0435\u043b\u044f.",
            })
            st.rerun()

    # Chat input (disabled during processing)
    user_input = st.chat_input(
        "\u041d\u0430\u043f\u0438\u0448\u0435\u0442\u0435 \u0441\u044a\u043e\u0431\u0449\u0435\u043d\u0438\u0435..."
        if not st.session_state.get("processing")
        else "\u0418\u0437\u0447\u0430\u043a\u0432\u0430\u0439\u0442\u0435...",
        disabled=st.session_state.get("processing", False),
    )

    # Show pending changes indicator
    if st.session_state.pending_changes:
        pending_lvl = st.session_state.pending_changes.get("level", "?")
        lvl_emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}.get(pending_lvl, "\u26aa")
        if pending_lvl == "red":
            st.info(f"{lvl_emoji} \u041e\u0447\u0430\u043a\u0432\u0430 \u0441\u0435 **\u0430\u0434\u043c\u0438\u043d \u043a\u043e\u0434** \u0437\u0430 \u043f\u0440\u0438\u043b\u0430\u0433\u0430\u043d\u0435 \u043d\u0430 \u043f\u0440\u043e\u043c\u0435\u043d\u0438...")
        else:
            st.info(f"{lvl_emoji} \u041e\u0447\u0430\u043a\u0432\u0430 \u0441\u0435 **\u043f\u043e\u0442\u0432\u044a\u0440\u0436\u0434\u0435\u043d\u0438\u0435** \u0437\u0430 \u043f\u0440\u0438\u043b\u0430\u0433\u0430\u043d\u0435 \u043d\u0430 \u043f\u0440\u043e\u043c\u0435\u043d\u0438...")

    if user_input:
        st.session_state.messages.append(
            {"role": "user", "content": user_input}
        )

        # Sync schedule state: if session has schedule but chat_handler lost it
        if st.session_state.get("current_schedule") and not chat_handler.current_schedule:
            chat_handler.current_schedule = st.session_state.current_schedule

        # Build project context
        project_context = None
        if st.session_state.project_loaded:
            project_context = {
                "path": st.session_state.project_path,
                "info": st.session_state.project_info,
                "conversion_done": st.session_state.conversion_done,
            }

        # Set processing flag and reset stop
        st.session_state.processing = True
        st.session_state.stop_requested = False
        router.stop_requested = False

        # Process through ChatHandler with live progress bar
        _prog_bar = st.progress(0, text="Обработвам...")
        _prog_text = st.empty()

        def _on_progress(pct: float, text: str) -> None:
            _prog_bar.progress(min(int(pct * 100), 100), text=text)

        result = chat_handler.process_message(
            user_input,
            project_loaded=st.session_state.project_loaded,
            conversion_done=st.session_state.conversion_done,
            project_context=project_context,
            pending_changes=st.session_state.pending_changes,
            recent_projects=st.session_state.recent_projects,
            progress_callback=_on_progress,
        )

        _prog_bar.empty()
        _prog_text.empty()

        # Clear processing flag
        st.session_state.processing = False

        # Check if stopped mid-operation
        if st.session_state.get("stop_requested"):
            st.session_state.stop_requested = False
            router.stop_requested = False
            if result.get("response"):
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": result["response"] + "\n\n\u23f9 _\u041e\u043f\u0435\u0440\u0430\u0446\u0438\u044f\u0442\u0430 \u0435 \u0441\u043f\u0440\u044f\u043d\u0430._",
                })
            st.rerun()

        # Handle close project from chat
        if result.get("close_project"):
            _save_chat_history()
            st.session_state.project_loaded = False
            st.session_state.current_project = None
            st.session_state.project_path = ""
            st.session_state.project_info = {}
            st.session_state.conversion_done = False
            st.session_state.restored_history = []
            st.session_state.welcome_shown = False
            st.session_state.recent_projects = project_mgr.get_recent_projects(5)
            chat_handler.clear_history()
            project_mgr.clear_last_active()

        # Handle project loading from chat
        if result.get("load_project_path"):
            _load_project_by_path(result["load_project_path"])
            st.rerun()

        if result.get("load_project_id"):
            _load_project_by_id(result["load_project_id"])
            st.rerun()

        # Track which model was used
        st.session_state.current_model = result.get("model_used")

        # Handle evolution pending changes
        if result.get("evolution_pending"):
            st.session_state.pending_changes = result["evolution_pending"]
        if result.get("evolution_cleared") or result.get("evolution_applied"):
            st.session_state.pending_changes = None

        # Build response with fallback notice
        response_text = result["response"]

        # Check for fallback notice
        if router.fallback_active and result.get("model_used") not in ("none", None):
            src = router.fallback_source
            if src == "deepseek" and result.get("model_used") == "claude-sonnet-4-6":
                response_text = (
                    "\u26a0\ufe0f _DeepSeek не отговаря. Превключвам към Anthropic._\n\n"
                    + response_text
                )
            elif src == "anthropic" and result.get("model_used") == "deepseek-chat":
                response_text = (
                    "\u26a0\ufe0f _Anthropic не отговаря. Превключвам към DeepSeek._\n\n"
                    + response_text
                )

        st.session_state.messages.append(
            {"role": "assistant", "content": response_text}
        )

        # Update schedule if changed
        if result.get("schedule_updated") and result.get("schedule_data"):
            st.session_state.schedule_data = result["schedule_data"]
            st.session_state.current_schedule = result["schedule_data"]

        # Update usage stats
        st.session_state.usage_stats = router.get_usage_stats()

        # Persist chat history
        _save_chat_history()

        st.rerun()

# ---------------------------------------------------------------------------
# RIGHT COLUMN — Visualization
# ---------------------------------------------------------------------------
with viz_col:
    st.markdown("### \U0001f4ca Визуализация")
    schedule = _ensure_schedule_list(
        st.session_state.get("current_schedule")
    )
    # Persist the parsed version back so we don't re-parse on every rerun
    if schedule != st.session_state.get("current_schedule"):
        st.session_state.current_schedule = schedule
        st.session_state.schedule_data = schedule

    # ── Layer toggles (row 1) ─────────────────────────────────────────
    st.caption("**Слоеве:**")
    lc1, lc2, lc3, lc4 = st.columns(4)
    with lc1:
        ly_crit = st.checkbox(
            "Критичен път",
            value=st.session_state.gantt_layers.get("critical_path", True),
            key="ly_crit",
        )
    with lc2:
        ly_deps = st.checkbox(
            "Зависимости",
            value=st.session_state.gantt_layers.get("dependencies", False),
            key="ly_deps",
        )
    with lc3:
        ly_teams = st.checkbox(
            "Екипи",
            value=st.session_state.gantt_layers.get("team_labels", True),
            key="ly_teams",
        )
    with lc4:
        ly_dur = st.checkbox(
            "Дни",
            value=st.session_state.gantt_layers.get("duration_labels", False),
            key="ly_dur",
        )

    # ── Layer toggles (row 2) ─────────────────────────────────────────
    lc5, lc6, lc7, lc8 = st.columns(4)
    with lc5:
        ly_phase = st.checkbox(
            "Фазови линии",
            value=st.session_state.gantt_layers.get("phase_separators", True),
            key="ly_phase",
        )
    with lc6:
        ly_today = st.checkbox(
            "Днес",
            value=st.session_state.gantt_layers.get("today_line", True),
            key="ly_today",
        )
    with lc7:
        ly_ms = st.checkbox(
            "Етапи",
            value=st.session_state.gantt_layers.get("milestones", True),
            key="ly_ms",
        )
    with lc8:
        ly_sub = st.checkbox(
            "Поддейности",
            value=st.session_state.gantt_layers.get("subtasks", False),
            key="ly_sub",
        )

    layers = {
        "bars": True,
        "critical_path": ly_crit,
        "dependencies": ly_deps,
        "team_labels": ly_teams,
        "duration_labels": ly_dur,
        "phase_separators": ly_phase,
        "today_line": ly_today,
        "milestones": ly_ms,
        "subtasks": ly_sub,
    }
    st.session_state.gantt_layers = layers

    # ── Filters ───────────────────────────────────────────────────────
    _phase_labels = {
        "design": "Проектиране",
        "construction": "Строителство",
        "supervision": "Авт. надзор",
    }
    all_teams = sorted(
        {t.get("team", "") for t in schedule
         if t.get("team") and t.get("team") != "\u2014"}
    )
    all_phases = sorted(
        {t.get("phase", "") for t in schedule if t.get("phase")}
    )
    all_types = sorted(
        {t.get("type", "") for t in schedule if t.get("type")}
    )

    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        _view_opts = {"Месеци": "months", "Седмици": "weeks", "Дни": "days"}
        view_label = st.selectbox(
            "Изглед", list(_view_opts.keys()), key="view_sel"
        )
        view_mode = _view_opts[view_label]
    with fc2:
        phase_display = ["Всички"] + [
            _phase_labels.get(p, p) for p in all_phases
        ]
        phase_sel = st.selectbox("Фаза", phase_display, key="phase_sel")
        filter_phase = None
        if phase_sel != "Всички":
            filter_phase = next(
                (k for k, v in _phase_labels.items() if v == phase_sel),
                phase_sel,
            )
    with fc3:
        type_display = ["Всички"] + [
            get_type_label(t) for t in all_types
        ]
        type_sel = st.selectbox("Тип", type_display, key="type_sel")
        filter_type = None
        if type_sel != "Всички":
            filter_type = next(
                (k for k, v in TYPE_LABELS.items() if v == type_sel),
                type_sel,
            )
    with fc4:
        team_display = ["Всички"] + all_teams
        team_sel = st.selectbox("Екип", team_display, key="team_sel")
        filter_team = team_sel if team_sel != "Всички" else None

    # ── Gantt chart ───────────────────────────────────────────────────
    if schedule:
        start_date = st.session_state.get("project_start_date", "2025-04-01")

        # Auto-disable dependencies for large schedules
        effective_layers = layers.copy()
        if len(schedule) > 100:
            effective_layers["dependencies"] = False

        fig = create_gantt_chart(
            schedule,
            layers=effective_layers,
            view_mode=view_mode,
            selected_task_id=st.session_state.get("selected_task_id"),
            filter_team=filter_team,
            filter_phase=filter_phase,
            filter_type=filter_type,
            project_start_date=start_date,
        )

        # Render chart (with click event handling)
        try:
            event = st.plotly_chart(
                fig,
                use_container_width=True,
                key="gantt_main",
                on_select="rerun",
                selection_mode="points",
            )
            if (
                event
                and hasattr(event, "selection")
                and event.selection
                and hasattr(event.selection, "points")
                and event.selection.points
            ):
                p = event.selection.points[0]
                cdata = (
                    p.get("customdata")
                    if isinstance(p, dict)
                    else getattr(p, "customdata", None)
                )
                if cdata and len(cdata) > 0 and cdata[0]:
                    st.session_state.selected_task_id = cdata[0]
        except TypeError:
            # Fallback for older Streamlit without on_select
            st.plotly_chart(fig, use_container_width=True, key="gantt_main")

        # ── Tabs ──────────────────────────────────────────────────────
        tab_table, tab_stats, tab_export, tab_details = st.tabs(
            ["\U0001f4cb Таблица", "\U0001f4ca Статистика", "\U0001f4be Експорт", "\U0001f50d Детайли"]
        )

        with tab_table:
            # Apply same filters as Gantt
            filtered = schedule
            if filter_team:
                filtered = [
                    t for t in filtered if t.get("team") == filter_team
                ]
            if filter_phase:
                filtered = [
                    t for t in filtered if t.get("phase") == filter_phase
                ]
            if filter_type:
                filtered = [
                    t for t in filtered if t.get("type") == filter_type
                ]

            if filtered:
                tbl_builder = ScheduleBuilder()
                df = tbl_builder.to_dataframe(filtered, start_date)

                def _highlight_critical(row):
                    if row["Критичен"] == "\U0001f534":
                        return ["background-color: #FFF0F0"] * len(row)
                    return [""] * len(row)

                st.dataframe(
                    df.style.apply(_highlight_critical, axis=1),
                    use_container_width=True,
                    hide_index=True,
                    height=min(400, len(df) * 35 + 40),
                )
            else:
                st.info("Няма данни за показване с текущите филтри.")

        with tab_stats:
            stats = get_schedule_stats(schedule)
            mc1, mc2, mc3, mc4 = st.columns(4)
            with mc1:
                st.metric("Дейности", stats["total_tasks"])
            with mc2:
                st.metric("Критични", stats["critical_count"])
            with mc3:
                st.metric("Общо дни", stats["total_days"])
            with mc4:
                st.metric("Екипи", len(stats["teams"]))

            if stats["teams"]:
                st.caption(f"Екипи: {', '.join(stats['teams'])}")

            if stats["type_breakdown"]:
                st.markdown("**Разбивка по тип:**")
                for type_code, bd in stats["type_breakdown"].items():
                    label = get_type_label(type_code)
                    st.caption(
                        f"\u2022 {label}: {bd['count']} дейности ({bd['days']}д)"
                    )

        with tab_export:
            st.markdown("#### Експорт на графика")
            if schedule:
                project_name = st.session_state.get("project_name", "ВиК Проект")
                export_start_date = st.session_state.get("project_start_date", "2026-06-01")

                st.caption("**Налични формати:**")
                exp_c1, exp_c2, exp_c3 = st.columns(3)

                with exp_c1:
                    st.markdown("**\U0001f4c4 PDF (A3 Gantt)**")
                    st.caption("За печат и представяне")
                    show_critical = st.checkbox(
                        "Критичен път в PDF",
                        value=True,
                        key="pdf_critical",
                    )
                    if st.button(
                        "Генерирай PDF",
                        type="primary",
                        key="btn_pdf",
                        use_container_width=True,
                    ):
                        with st.spinner("Генерирам PDF..."):
                            try:
                                pdf_bytes = export_to_pdf(
                                    schedule,
                                    project_name,
                                    start_date=export_start_date,
                                    show_critical_path=show_critical,
                                )
                                if pdf_bytes:
                                    st.session_state["pdf_ready"] = pdf_bytes
                                else:
                                    st.error("PDF генерирането не успя.")
                            except Exception as e:
                                st.error(f"Грешка при PDF: {e}")

                    if st.session_state.get("pdf_ready"):
                        st.download_button(
                            label="\u2b07\ufe0f Свали PDF",
                            data=st.session_state["pdf_ready"],
                            file_name=f"\u0433\u0440\u0430\u0444\u0438\u043a_{project_name}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                        )
                        pdf_size = len(st.session_state["pdf_ready"])
                        st.caption(f"\u2705 {pdf_size:,} bytes")

                with exp_c2:
                    st.markdown("**\U0001f4cb XML (MS Project)**")
                    st.caption("За отваряне в MS Project")
                    st.info(
                        "MS Project \u2192 File \u2192 Open \u2192 XML Format",
                        icon="\U0001f4a1",
                    )
                    if st.button(
                        "Генерирай XML",
                        type="primary",
                        key="btn_xml",
                        use_container_width=True,
                    ):
                        with st.spinner("Генерирам XML..."):
                            try:
                                xml_bytes = export_to_mspdi_xml(
                                    schedule,
                                    project_name,
                                    start_date=export_start_date,
                                )
                                if xml_bytes:
                                    st.session_state["xml_ready"] = xml_bytes
                                else:
                                    st.error("XML генерирането не успя.")
                            except Exception as e:
                                st.error(f"Грешка при XML: {e}")

                    if st.session_state.get("xml_ready"):
                        st.download_button(
                            label="\u2b07\ufe0f Свали XML",
                            data=st.session_state["xml_ready"],
                            file_name=f"\u0433\u0440\u0430\u0444\u0438\u043a_{project_name}.xml",
                            mime="application/xml",
                            use_container_width=True,
                        )
                        xml_size = len(st.session_state["xml_ready"])
                        st.caption(f"\u2705 {xml_size:,} bytes")

                with exp_c3:
                    st.markdown("**\U0001f527 JSON (суров)**")
                    st.caption("За програмна обработка")
                    from datetime import datetime as _dt_export

                    json_data = json.dumps(
                        {
                            "metadata": {
                                "project_name": project_name,
                                "start_date": export_start_date,
                                "total_activities": len(schedule),
                                "exported": _dt_export.now().isoformat(),
                            },
                            "activities": schedule,
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                    st.download_button(
                        label="\u2b07\ufe0f Свали JSON",
                        data=json_data.encode("utf-8"),
                        file_name=f"\u0433\u0440\u0430\u0444\u0438\u043a_{project_name}.json",
                        mime="application/json",
                        use_container_width=True,
                    )

                st.divider()
                st.caption(
                    "\U0001f4a1 За .mpp файл: отворете XML в MS Project \u2192 "
                    "File \u2192 Save As \u2192 .mpp"
                )
            else:
                st.info(
                    "\U0001f4ca Генерирайте график първо, за да можете да го експортирате."
                )

        with tab_details:
            sel_id = st.session_state.get("selected_task_id")
            if sel_id:
                task_map = {t["id"]: t for t in schedule}
                sel_task = task_map.get(sel_id)
                if sel_task:
                    detail_md = create_task_detail_panel(
                        sel_task, schedule, start_date
                    )
                    st.markdown(detail_md)

                    if st.button("\U0001f4ac Промени в чата", key="edit_in_chat"):
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": (
                                f"Избрана дейност **{sel_task['id']}: "
                                f"{sel_task['name']}** за промяна.\n\n"
                                f"Опишете каква промяна желаете "
                                f"(напр. 'Промени продължителността на "
                                f"{sel_task['name']} на 45 дни')."
                            ),
                        })
                        st.rerun()
                else:
                    st.info("Избраната дейност не е намерена в графика.")
            else:
                st.info(
                    "Кликнете върху дейност в графика за да видите детайли."
                )
    else:
        st.info(
            "Няма генериран график. Използвайте чата за да създадете един."
        )

    # ── Status bar ────────────────────────────────────────────────────
    st.divider()
    status_cols = st.columns([3, 2, 2])
    with status_cols[0]:
        if st.session_state.project_loaded:
            folder_name = Path(st.session_state.project_path).name
            st.caption(f"\U0001f4c2 Проект: {folder_name}")
        else:
            st.caption("\U0001f4c2 Няма зареден проект")
    with status_cols[1]:
        st.caption(f"\U0001f4ca Задачи: {len(schedule) if schedule else 0}")
    with status_cols[2]:
        if schedule:
            total_days = max(
                (
                    t.get(
                        "end_day",
                        t.get("start_day", 0) + t.get("duration", 0),
                    )
                    for t in schedule
                ),
                default=0,
            )
            st.caption(f"\U0001f4c5 Общо: {total_days} дни")
        else:
            st.caption("\U0001f4c5 Общо: \u2014")
