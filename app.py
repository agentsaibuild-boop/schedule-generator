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
from src.knowledge_manager import KnowledgeManager
from src.schedule_builder import ScheduleBuilder
from src.self_evolution import SelfEvolution

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

# ---------------------------------------------------------------------------
# Initialize managers (cached in session state to survive reruns)
# ---------------------------------------------------------------------------
if "router" not in st.session_state:
    st.session_state.router = AIRouter()

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

if "chat_handler" not in st.session_state:
    st.session_state.chat_handler = ChatHandler(
        ai_processor=st.session_state.ai_processor,
        file_manager=st.session_state.file_mgr,
        knowledge_manager=st.session_state.knowledge_mgr,
        evolution=st.session_state.self_evolution,
    )

router: AIRouter = st.session_state.router
knowledge_mgr: KnowledgeManager = st.session_state.knowledge_mgr
file_mgr: FileManager = st.session_state.file_mgr
ai_processor: AIProcessor = st.session_state.ai_processor
evolution: SelfEvolution = st.session_state.self_evolution
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
            "working": "🔄",
            "done": "✅",
            "skip": "⏭️",
            "error": "❌",
        }.get(status, "⏳")

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
            details.append(f"- {r['file']} → {ext_label}{method_str}: {detail}")
        elif r["action"] == "failed":
            details.append(f"- {r['file']} → ❌ {r.get('error', '')}")

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


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("# 📐 ВиК График Генератор")
    st.caption("РАИ Комерс | v0.2 — Dual AI")

    st.divider()

    # --- AI Status ---
    st.subheader("🤖 AI Статус")

    ds_status = "🟢 Работи" if ai_health.get("deepseek") else "🔴 Недостъпен"
    an_status = "🟢 Работи" if ai_health.get("anthropic") else "🔴 Недостъпен"
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
    if st.button("🔄 Провери отново", use_container_width=True, key="health_check"):
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
    st.subheader("💰 Разходи")
    stats = router.get_usage_stats()
    st.session_state.usage_stats = stats

    col_ds, col_an = st.columns(2)
    with col_ds:
        ds_cost = stats["deepseek"]["cost_usd"]
        ds_calls = stats["deepseek"]["calls"]
        st.metric("DeepSeek", f"${ds_cost:.4f}", f"{ds_calls} заявки")
    with col_an:
        an_cost = stats["anthropic"]["cost_usd"]
        an_calls = stats["anthropic"]["calls"]
        st.metric("Anthropic", f"${an_cost:.4f}", f"{an_calls} заявки")

    st.caption(f"**Общо: ${stats['total_cost_usd']:.4f}**")

    st.divider()

    # --- Project path ---
    st.subheader("📁 Проект")

    path_col, browse_col = st.columns([3, 1])
    with path_col:
        project_path_input = st.text_input(
            "Път до проектна папка",
            value=st.session_state.project_path,
            placeholder=r"D:\Projects\Горица",
            help="Пълен път до папката с тендерна документация",
            label_visibility="collapsed",
        )
    with browse_col:
        if st.button("📂", use_container_width=True, help="Избери папка"):
            ps_script = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Izberi proektna papka'; "
                "$f.ShowNewFolderButton = $false; "
                "$owner = New-Object System.Windows.Forms.Form; "
                "$owner.TopMost = $true; "
                "$result = $f.ShowDialog($owner); "
                "$owner.Dispose(); "
                "if ($result -eq 'OK') { $f.SelectedPath }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            chosen = result.stdout.strip()
            if chosen:
                st.session_state.project_path = chosen
                st.rerun()

    # --- Load project button ---
    if st.button("Зареди проект", use_container_width=True):
        path_to_load = project_path_input or st.session_state.project_path
        if path_to_load:
            info = file_mgr.set_project_path(path_to_load)
            if info["valid"]:
                st.session_state.project_path = path_to_load
                st.session_state.project_loaded = True
                st.session_state.project_info = info
                st.session_state.conversion_done = (
                    info["needs_conversion"] == 0 and info["converted_count"] > 0
                )

                folder_name = Path(path_to_load).name
                msg = (
                    f"📁 Проект **{folder_name}** зареден.\n\n"
                    f"- Файлове: **{info['files_count']}**\n"
                    f"- Конвертирани: **{info['converted_count']}**\n"
                    f"- Чакащи конверсия: **{info['needs_conversion']}**"
                )
                if info["needs_conversion"] > 0:
                    msg += "\n\n⚠️ Натиснете **Конвертирай файлове** в страничната лента."
                elif info["converted_count"] > 0:
                    msg += "\n\n✅ Всички файлове са конвертирани и готови."
                else:
                    msg += "\n\nНяма файлове за конверсия в тази папка."

                st.session_state.messages.append({"role": "assistant", "content": msg})
                st.rerun()
            else:
                st.error("Папката не съществува или не е достъпна.")
        else:
            st.warning("Моля, изберете папка.")

    # --- Loaded project details ---
    if st.session_state.project_loaded and st.session_state.project_path:
        if not file_mgr.base_path:
            file_mgr.set_project_path(st.session_state.project_path)

        info = st.session_state.project_info
        summary = file_mgr.get_project_summary()
        folder_name = Path(st.session_state.project_path).name

        st.caption(f"📂 {folder_name}")
        if summary["by_type"]:
            type_str = ", ".join(
                f"{ext}: {cnt}" for ext, cnt in sorted(summary["by_type"].items())
            )
            st.caption(f"Файлове: {type_str}")

        needs = info.get("needs_conversion", 0)
        converted = info.get("converted_count", 0)
        total = info.get("files_count", 0)

        if needs > 0:
            st.warning(f"⚠️ {needs} от {total} файла чакат конверсия")
            if st.button("🔄 Конвертирай файлове", use_container_width=True):
                _run_conversion(force=False)
                st.rerun()
        elif converted > 0:
            st.success(f"✅ {converted}/{total} файла конвертирани")
        elif total > 0:
            st.info(f"{total} файла намерени")

        if converted > 0:
            if st.button("🔁 Преконвертирай всичко", use_container_width=True):
                _run_conversion(force=True)
                st.rerun()

    st.divider()

    # --- Knowledge stats ---
    st.subheader("🧠 Знания")
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
    st.subheader("🔧 Еволюция")
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
        if st.button("📜 История", use_container_width=True, key="evo_history_btn"):
            if evo_history:
                history_lines = ["**📜 История на промените:**\n"]
                for entry in reversed(evo_history[-10:]):
                    ts = entry.get("timestamp", "?")[:16].replace("T", " ")
                    lvl = entry.get("level", "?")
                    desc = entry.get("description", "?")
                    status = entry.get("status", "?")
                    emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴", "rollback": "⏪"}.get(lvl, "⚪")
                    history_lines.append(f"{emoji} [{ts}] {desc} ({status})")
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "\n".join(history_lines),
                })
                st.rerun()
            else:
                st.info("Няма история.")
    with evo_col2:
        if st.button("⏪ Върни", use_container_width=True, key="evo_rollback_btn"):
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
                    f"⚠️ Ще се върнете към: {commit_short} — {desc}"
                )
                rollback_code = st.text_input(
                    "Въведете админ код:",
                    type="password",
                    key="rollback_admin_code",
                )
                rb_col1, rb_col2 = st.columns(2)
                with rb_col1:
                    if st.button("✅ Потвърди", key="confirm_rollback", use_container_width=True):
                        if evolution.verify_admin_code(rollback_code):
                            rb_result = evolution.rollback(last_backup["backup_commit"])
                            if rb_result["success"]:
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": f"⏪ Успешен rollback към {commit_short}. Рестартирайте приложението.",
                                })
                            else:
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": f"❌ Rollback неуспешен: {rb_result.get('error', '?')}",
                                })
                            st.session_state["show_rollback_dialog"] = False
                            st.rerun()
                        else:
                            st.error("❌ Невалиден админ код.")
                with rb_col2:
                    if st.button("❌ Откажи", key="cancel_rollback", use_container_width=True):
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

    # --- Current project info ---
    st.subheader("📋 Текущ проект")
    if st.session_state.project_loaded:
        folder_name = Path(st.session_state.project_path).name
        st.write(f"**Име:** {folder_name}")
        st.write("**Тип:** Неопределен")
        conv_status = "Конвертиран" if st.session_state.conversion_done else "Зареден"
        st.write(f"**Статус:** {conv_status}")
    else:
        st.caption("Няма зареден проект.")

    st.divider()

    # --- Clear chat ---
    if st.button("🗑️ Изчисти чата", use_container_width=True):
        st.session_state.messages = []
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
    st.markdown("### 💬 Чат")

    # Welcome message (shown once)
    if not st.session_state.welcome_shown:
        # Build welcome with AI status
        ai_note = ""
        if ai_health.get("deepseek") and ai_health.get("anthropic"):
            ai_note = "🟢 Двата AI модела са готови (DeepSeek + Anthropic)."
        elif ai_health.get("fallback_active"):
            src = ai_health.get("fallback_source", "")
            if src == "deepseek":
                ai_note = "⚠️ DeepSeek не е достъпен — работя чрез Anthropic."
            elif src == "anthropic":
                ai_note = "⚠️ Anthropic не е достъпен — работя чрез DeepSeek."
            else:
                ai_note = "🔴 AI моделите не са достъпни. Проверете .env файла."

        welcome = (
            "Здравейте! Аз съм вашият асистент за строителни графици.\n\n"
            f"{ai_note}\n\n"
            "Можете да:\n"
            "- 📁 Заредите проектна папка от страничната лента\n"
            "- 📊 Опишете какъв график ви трябва\n"
            "- ❓ Зададете въпрос за методология или правила\n"
            "- 🔧 Поискате нова функционалност (самоеволюция)\n\n"
            "С какво мога да помогна?"
        )
        st.session_state.messages.append(
            {"role": "assistant", "content": welcome}
        )
        st.session_state.welcome_shown = True

    # Display chat history
    chat_container = st.container(height=520)
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Chat input
    user_input = st.chat_input("Напишете съобщение...")

    # Show pending changes indicator
    if st.session_state.pending_changes:
        pending_lvl = st.session_state.pending_changes.get("level", "?")
        lvl_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(pending_lvl, "⚪")
        if pending_lvl == "red":
            st.info(f"{lvl_emoji} Очаква се **админ код** за прилагане на промени...")
        else:
            st.info(f"{lvl_emoji} Очаква се **потвърждение** за прилагане на промени...")

    if user_input:
        st.session_state.messages.append(
            {"role": "user", "content": user_input}
        )

        # Build project context
        project_context = None
        if st.session_state.project_loaded:
            project_context = {
                "path": st.session_state.project_path,
                "info": st.session_state.project_info,
                "conversion_done": st.session_state.conversion_done,
            }

        # Process through ChatHandler (real AI)
        with st.spinner("Обработвам..."):
            result = chat_handler.process_message(
                user_input,
                project_loaded=st.session_state.project_loaded,
                conversion_done=st.session_state.conversion_done,
                project_context=project_context,
                pending_changes=st.session_state.pending_changes,
            )

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
                    "⚠️ _DeepSeek не отговаря. Превключвам към Anthropic._\n\n"
                    + response_text
                )
            elif src == "anthropic" and result.get("model_used") == "deepseek-chat":
                response_text = (
                    "⚠️ _Anthropic не отговаря. Превключвам към DeepSeek._\n\n"
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

        st.rerun()

# ---------------------------------------------------------------------------
# RIGHT COLUMN — Visualization
# ---------------------------------------------------------------------------
with viz_col:
    st.markdown("### 📊 Визуализация")
    schedule = st.session_state.get("current_schedule") or []

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
         if t.get("team") and t.get("team") != "—"}
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
            ["📋 Таблица", "📊 Статистика", "💾 Експорт", "🔍 Детайли"]
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
                    if row["Критичен"] == "🔴":
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
                        f"• {label}: {bd['count']} дейности ({bd['days']}д)"
                    )

        with tab_export:
            st.markdown("#### Експорт на графика")
            exp_c1, exp_c2, exp_c3 = st.columns(3)
            with exp_c1:
                st.markdown("**PDF (A3)**")
                st.caption("Gantt диаграма за печат")
                if st.button(
                    "📄 Свали PDF",
                    use_container_width=True,
                    disabled=not schedule,
                ):
                    st.warning(
                        "PDF експортът ще бъде наличен в следваща версия."
                    )
            with exp_c2:
                st.markdown("**MSPDI XML**")
                st.caption("MS Project формат")
                if st.button(
                    "📋 Свали XML",
                    use_container_width=True,
                    disabled=not schedule,
                ):
                    st.warning(
                        "XML експортът ще бъде наличен в следваща версия."
                    )
            with exp_c3:
                st.markdown("**JSON**")
                st.caption("Суровите данни")
                json_str = json.dumps(
                    schedule, ensure_ascii=False, indent=2, default=str
                )
                st.download_button(
                    label="📦 Свали JSON",
                    data=json_str,
                    file_name="schedule.json",
                    mime="application/json",
                    use_container_width=True,
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

                    if st.button("💬 Промени в чата", key="edit_in_chat"):
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
            st.caption(f"📂 Проект: {folder_name}")
        else:
            st.caption("📂 Няма зареден проект")
    with status_cols[1]:
        st.caption(f"📊 Задачи: {len(schedule) if schedule else 0}")
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
            st.caption(f"📅 Общо: {total_days} дни")
        else:
            st.caption("📅 Общо: —")
