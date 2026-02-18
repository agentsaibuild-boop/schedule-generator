"""Chat session handler for processing user messages and managing history."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ai_processor import AIProcessor
    from src.knowledge_manager import KnowledgeManager


INTENT_KEYWORDS = {
    "load_project": ["зареди", "папка", "проект", "път", "директория", "отвори"],
    "generate_schedule": [
        "генерирай", "график", "създай", "направи", "gantt",
        "линеен", "графика", "нов график",
    ],
    "ask_question": [
        "какво", "как", "защо", "кога", "колко", "обясни",
        "правило", "методика", "урок",
    ],
    "export": ["свали", "експорт", "pdf", "xml", "mspdi", "export"],
    "modify_schedule": [
        "промени", "корекция", "измени", "обнови", "добави",
        "премахни", "премести",
    ],
}


class ChatHandler:
    """Manages the chat session: message processing, history, and intent detection."""

    def __init__(
        self,
        knowledge_manager: KnowledgeManager | None = None,
        ai_processor: AIProcessor | None = None,
    ) -> None:
        """Initialize the chat handler.

        Args:
            knowledge_manager: KnowledgeManager instance for knowledge lookups.
            ai_processor: AIProcessor instance for AI responses.
        """
        self.knowledge_manager = knowledge_manager
        self.ai_processor = ai_processor
        self.history: list[dict[str, str]] = []

    def process_message(self, user_message: str) -> str:
        """Process a user message and return a response.

        Args:
            user_message: The user's input message.

        Returns:
            Assistant response string.
        """
        # Add user message to history
        self.history.append({"role": "user", "content": user_message})

        # Detect intent
        intent = self._detect_intent(user_message)

        # Generate response based on intent
        response = self._generate_response(user_message, intent)

        # Add assistant response to history
        self.history.append({"role": "assistant", "content": response})

        return response

    def get_chat_history(self) -> list[dict[str, str]]:
        """Get the full chat history.

        Returns:
            List of message dicts with 'role' and 'content'.
        """
        return self.history

    def clear_history(self) -> None:
        """Clear all chat history."""
        self.history = []

    def _detect_intent(self, message: str) -> str:
        """Detect the user's intent from their message.

        Args:
            message: The user's input message.

        Returns:
            Intent string: 'load_project', 'generate_schedule', 'ask_question',
            'export', 'modify_schedule', or 'general'.
        """
        message_lower = message.lower()

        best_intent = "general"
        best_score = 0

        for intent, keywords in INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            if score > best_score:
                best_score = score
                best_intent = intent

        return best_intent

    def _generate_response(self, message: str, intent: str) -> str:
        """Generate a response based on the message and detected intent.

        Currently a placeholder that echoes the intent. Will be connected
        to the AI processor in a future step.

        Args:
            message: The user's input message.
            intent: Detected intent string.

        Returns:
            Response string.
        """
        intent_labels = {
            "load_project": "Зареждане на проект",
            "generate_schedule": "Генериране на график",
            "ask_question": "Въпрос",
            "export": "Експорт",
            "modify_schedule": "Промяна на график",
            "general": "Общо",
        }

        intent_label = intent_labels.get(intent, "Общо")

        # Placeholder responses by intent
        if intent == "generate_schedule":
            return (
                f"**Разпознат намерение:** {intent_label}\n\n"
                "Разбрах, че искате да генерирате график. "
                "За да продължа, ще ми трябва:\n"
                "1. Път до проектната папка с документация\n"
                "2. Тип на проекта (разпределителна мрежа, довеждащ, единичен, инженеринг)\n"
                "3. Основни параметри (DN, дължини, срокове)\n\n"
                "*Тази функция ще бъде свързана с Claude API в следваща стъпка.*"
            )
        elif intent == "load_project":
            return (
                f"**Разпознат намерение:** {intent_label}\n\n"
                "Моля, въведете пътя до проектната папка "
                "в страничната лента (вляво).\n\n"
                "*Тази функция ще бъде свързана с Claude API в следваща стъпка.*"
            )
        elif intent == "ask_question":
            stats = {}
            if self.knowledge_manager:
                stats = self.knowledge_manager.get_knowledge_stats()
            return (
                f"**Разпознат намерение:** {intent_label}\n\n"
                f"Търся отговор в базата знания "
                f"({stats.get('lessons', 0)} урока, "
                f"{stats.get('methodologies', 0)} методики)...\n\n"
                "*Тази функция ще бъде свързана с Claude API в следваща стъпка.*"
            )
        elif intent == "export":
            return (
                f"**Разпознат намерение:** {intent_label}\n\n"
                "Форматите за експорт са:\n"
                "- **PDF** (A3 landscape Gantt диаграма)\n"
                "- **MSPDI XML** (за MS Project)\n\n"
                "Използвайте таб '💾 Експорт' вдясно.\n\n"
                "*Тази функция ще бъде свързана с Claude API в следваща стъпка.*"
            )
        elif intent == "modify_schedule":
            return (
                f"**Разпознат намерение:** {intent_label}\n\n"
                "Промяната на графика ще бъде достъпна след генериране.\n\n"
                "*Тази функция ще бъде свързана с Claude API в следваща стъпка.*"
            )
        else:
            return (
                f"**Разпознат намерение:** {intent_label}\n\n"
                f"Получих вашето съобщение: *\"{message}\"*\n\n"
                "Мога да помогна с:\n"
                "- Генериране на строителен график\n"
                "- Въпроси за методология и правила\n"
                "- Експорт в PDF или XML\n\n"
                "*Тази функция ще бъде свързана с Claude API в следваща стъпка.*"
            )
