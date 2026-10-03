"""ConversationMemory – retains recent user/assistant turns for context.

Each session writes JSON lines to a file ``memory/conversation_<session_id>.jsonl``.
The simple implementation below uses an in‑memory list and appends to the file
asynchronously.  Pronoun resolution is rudimentary: it looks for the most recent
symbol name (capital letters) when the pronoun ``it`` or ``them`` appears.
"""

import asyncio
import json
import pathlib
import aiofiles
from typing import List, Dict, Any, Optional

DEFAULT_TURNS = 20


class ConversationMemory:
    def __init__(self, session_id: str, project_root: pathlib.Path | None = None):
        self.session_id = session_id
        self.project_root = project_root or pathlib.Path(__file__).resolve().parents[3]
        self.file_path = self.project_root / "memory" / f"conversation_{session_id}.jsonl"
        self._entries: List[Dict[str, Any]] = []
        # Load existing file if present.
        if self.file_path.exists():
            with self.file_path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        self._entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        # Ensure the directory exists.
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def add_entry(self, role: str, text: str, intent: Optional[Dict[str, Any]] = None) -> None:
        """Append a new turn to the conversation.
        ``role`` is ``"user"`` or ``"assistant"``.
        """
        entry = {"role": role, "text": text, "timestamp": asyncio.get_event_loop().time()}
        if intent:
            entry["intent"] = intent
        async with self._lock:
            self._entries.append(entry)
            # Persist immediately (append line).
            async with aiofiles.open(self.file_path, "a", encoding="utf-8") as af:
                await af.write(json.dumps(entry) + "\n")

    def recent(self, count: int = 5) -> List[Dict[str, Any]]:
        """Return the most recent ``count`` entries (oldest first)."""
        return self._entries[-count:]

    def resolve_pronoun(self, pronoun: str) -> Optional[str]:
        """Very simple pronoun resolution.
        Looks backward for a capitalised symbol (e.g., ``XAUUSD``) when the pronoun
        is ``it`` or ``them``. Returns ``None`` if no candidate is found.
        """
        pronoun = pronoun.lower()
        if pronoun not in {"it", "them", "he", "she", "they"}:
            return None
        import re
        symbol_pat = re.compile(r"\b[A-Z]{3,5}\b")
        for entry in reversed(self._entries):
            matches = symbol_pat.findall(entry["text"])
            if matches:
                return matches[-1]
        return None

__all__ = ["ConversationMemory"]
