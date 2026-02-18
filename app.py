"""ВиК График Генератор — Main Streamlit Application.

A local web application for generating construction schedules (Gantt)
for water and sewage infrastructure projects in Bulgaria.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src.ai_processor import AIProcessor
from src.chat_handler import ChatHandler
from src.file_manager import FileManager
from src.gantt_chart import create_gantt_chart
from src.knowledge_manager import KnowledgeManager
from src.schedule_builder import ScheduleBuilder

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

# ---------------------------------------------------------------------------
# Initialize managers
# ---------------------------------------------------------------------------
knowledge_mgr = KnowledgeManager(str(KNOWLEDGE_DIR))
file_mgr = FileManager()
chat_handler = ChatHandler(knowledge_manager=knowledge_mgr)

api_key = os.getenv("ANTHROPIC_API_KEY", "")
ai_processor = AIProcessor(api_key=api_key) if api_key else None

# ---------------------------------------------------------------------------
# Helper: run file conversion with progress in chat
# ---------------------------------------------------------------------------

def _run_conversion(force: bool = False) -> None:
    """Execute file conversion and write progress to chat messages."""
    total_files = st.session_state.project_info.get("files_count", 0)
    if total_files == 0:
        return

    # Initial chat message
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

    # Details per file
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
            method_str = f" (OCR)" if method == "ocr_vision" else ""
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

    # Update state
    st.session_state.conversion_done = True
    new_info = file_mgr.set_project_path(st.session_state.project_path)
    st.session_state.project_info = new_info


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("# 📐 ВиК График Генератор")
    st.caption("РАИ Комерс | v0.1")

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
                st.session_state.conversion_done = (info["needs_conversion"] == 0 and info["converted_count"] > 0)

                # Chat notification
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

        # Conversion status
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

        # Force reconvert
        if converted > 0:
            if st.button("🔁 Преконвертирай всичко", use_container_width=True):
                _run_conversion(force=True)
                st.rerun()

    st.divider()

    # --- Knowledge stats ---
    st.subheader("🧠 Знания")
    stats = knowledge_mgr.get_knowledge_stats()
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("Уроци", stats["lessons"])
    with col_b:
        st.metric("Методики", stats["methodologies"])

    col_c, col_d = st.columns(2)
    with col_c:
        st.metric("Чакащи", stats["pending"])
    with col_d:
        st.metric("Референции", stats["skill_references"])

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

    # --- API status ---
    if ai_processor and ai_processor.is_configured:
        st.caption("🟢 Anthropic API: свързан")
    else:
        st.caption("🔴 Anthropic API: няма ключ (.env)")

    # --- Clear chat ---
    if st.button("🗑️ Изчисти чата", use_container_width=True):
        st.session_state.messages = []
        st.session_state.welcome_shown = False
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
        welcome = (
            "Здравейте! Аз съм вашият асистент за строителни графици.\n\n"
            "Можете да:\n"
            "- 📁 Заредите проектна папка от страничната лента\n"
            "- 📊 Опишете какъв график ви трябва\n"
            "- ❓ Зададете въпрос за методология или правила\n\n"
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

    if user_input:
        st.session_state.messages.append(
            {"role": "user", "content": user_input}
        )

        # Build context about loaded project files
        context_note = ""
        if st.session_state.project_loaded and st.session_state.conversion_done:
            if file_mgr.base_path:
                converted = file_mgr.get_converted_files()
                if converted:
                    flist = ", ".join(c["original"] for c in converted)
                    context_note = f"\n\n*[Налични документи: {flist}]*"

        response = chat_handler.process_message(user_input)
        if context_note:
            response += context_note

        st.session_state.messages.append(
            {"role": "assistant", "content": response}
        )
        st.rerun()

# ---------------------------------------------------------------------------
# RIGHT COLUMN — Visualization
# ---------------------------------------------------------------------------
with viz_col:
    tab_gantt, tab_table, tab_export = st.tabs(
        ["📊 Gantt диаграма", "📋 Таблица", "💾 Експорт"]
    )

    schedule = st.session_state.schedule_data

    with tab_gantt:
        if schedule:
            fig = create_gantt_chart(schedule)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Няма генериран график. Използвайте чата за да създадете един.")

    with tab_table:
        if schedule:
            table_data = []
            for task in schedule:
                table_data.append({
                    "Дейност": task["name"],
                    "DN": task.get("dn", "-"),
                    "L (м)": task.get("length_m", "-"),
                    "Екип": task.get("team", "-"),
                    "Начало": task["start"].strftime("%d.%m.%Y"),
                    "Край": task["end"].strftime("%d.%m.%Y"),
                    "Дни": task.get("duration", "-"),
                })
            st.dataframe(table_data, use_container_width=True, hide_index=True)
        else:
            st.info("Няма данни за показване.")

    with tab_export:
        st.markdown("#### Експорт на графика")

        if not schedule:
            st.info("Първо генерирайте график чрез чата.")
        else:
            exp_col1, exp_col2 = st.columns(2)

            with exp_col1:
                st.markdown("**PDF (A3 Landscape)**")
                st.caption("Gantt диаграма за печат")
                if st.button("📄 Свали PDF (A3)", use_container_width=True):
                    st.warning("PDF експортът ще бъде наличен в следваща версия.")

            with exp_col2:
                st.markdown("**MSPDI XML (MS Project)**")
                st.caption("Отваря се директно в MS Project")
                if st.button("📋 Свали XML (MS Project)", use_container_width=True):
                    st.warning("XML експортът ще бъде наличен в следваща версия.")

    # --- Status bar ---
    st.divider()
    status_cols = st.columns([3, 2, 2])
    with status_cols[0]:
        if st.session_state.project_loaded:
            folder_name = Path(st.session_state.project_path).name
            st.caption(f"📂 Проект: {folder_name}")
        else:
            st.caption("📂 Няма зареден проект")
    with status_cols[1]:
        st.caption(f"📊 Задачи: {len(schedule)}")
    with status_cols[2]:
        if schedule:
            total_days = max(
                t.get("start_day", 0) + t.get("duration", 0) - 1 for t in schedule
            )
            st.caption(f"📅 Общо: {total_days} дни")
        else:
            st.caption("📅 Общо: —")
