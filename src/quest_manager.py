"""
Angels Online quest manager.

Loads quest definitions from quest.xml and tracks per-player quest progress.

quest.xml attributes (Traditional Chinese):
  任務   = quest element
  編號   = quest ID
  任務名稱 = quest name
  任務類型 = quest type/category
  任務前言 = description text
  可否重接 = repeatable ("可重接" = yes)
  步驟01..08 = step descriptions
  地點01..08 = step locations
  承接01..08 = step NPC names
  刪除道具01 = item to delete on completion

Step descriptions contain embedded references:
  /c$9%ITEM_ID%/c* = item/monster name placeholder
  /N = required count (e.g., /10 = need 10)
"""

import xml.etree.ElementTree as ET
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger('quest_manager')


@dataclass
class QuestStep:
    index: int          # 1-based step number
    description: str    # Raw step text (with formatting codes)
    location: str       # Area name
    npc_name: str       # NPC to talk to for this step

    # Parsed objectives from the step description
    objectives: list[dict] = field(default_factory=list)
    # Each objective: {'ref_id': int, 'count': int, 'type': 'kill'|'collect'|'talk'}


@dataclass
class QuestDef:
    quest_id: int
    name: str
    category: str
    description: str
    repeatable: bool
    steps: list[QuestStep]
    delete_item_id: int = 0  # Item removed on completion


@dataclass
class QuestState:
    quest_id: int
    current_step: int = 1      # 1-based step index
    counters: dict = field(default_factory=dict)  # ref_id → current count
    completed: bool = False


# Parse objectives from step text like "Defeat Slarm: /c$9%1400%/c* /10"
_OBJ_PATTERN = re.compile(r'/c\$9%(\d+)%/c\*\s*/(\d+)')


def _parse_step_objectives(text: str) -> list[dict]:
    """Extract kill/collect objectives from a step description."""
    objectives = []
    for m in _OBJ_PATTERN.finditer(text):
        ref_id = int(m.group(1))
        count = int(m.group(2))
        objectives.append({
            'ref_id': ref_id,
            'count': count,
            'type': 'kill',  # Could be kill or collect; context-dependent
        })
    return objectives


def _clean_text(text: str) -> str:
    """Strip formatting codes from quest text for display."""
    text = re.sub(r'/c\$\d+', '', text)
    text = re.sub(r'/c\*', '', text)
    text = re.sub(r'%(\d+)%', r'[\1]', text)
    text = text.replace('\\n', '\n').strip()
    return text


class QuestManager:
    """Loads quest definitions and tracks per-player quest state."""

    def __init__(self):
        self._quests: dict[int, QuestDef] = {}

    def load(self, quest_xml_path: str | Path):
        """Load quest definitions from quest.xml."""
        path = Path(quest_xml_path)
        try:
            root = ET.parse(str(path)).getroot()
        except (ET.ParseError, OSError) as e:
            log.error(f"Failed to load quest.xml: {e}")
            return

        count = 0
        for elem in root:
            if elem.tag != '任務':
                continue
            qid_str = elem.attrib.get('編號')
            if not qid_str:
                continue
            qid = int(qid_str)
            name = elem.attrib.get('任務名稱', f'Quest {qid}')
            category = elem.attrib.get('任務類型', '')
            desc = elem.attrib.get('任務前言', '')
            repeatable = elem.attrib.get('可否重接', '') == '可重接'
            delete_item = int(elem.attrib.get('刪除道具01', 0) or 0)

            steps = []
            for i in range(1, 9):
                step_key = f'步驟{i:02d}'
                step_text = elem.attrib.get(step_key, '').strip()
                if not step_text:
                    continue
                location = elem.attrib.get(f'地點{i:02d}', '')
                npc_name = elem.attrib.get(f'承接{i:02d}', '')
                objectives = _parse_step_objectives(step_text)
                steps.append(QuestStep(
                    index=i,
                    description=step_text,
                    location=location,
                    npc_name=npc_name,
                    objectives=objectives,
                ))

            if not name or name == f'Quest {qid}':
                continue  # Skip empty/placeholder quests

            self._quests[qid] = QuestDef(
                quest_id=qid,
                name=name,
                category=category,
                description=desc,
                repeatable=repeatable,
                steps=steps,
                delete_item_id=delete_item,
            )
            count += 1

        log.info(f"QuestManager: loaded {count} quests from {path.name}")

    def get_quest(self, quest_id: int) -> Optional[QuestDef]:
        return self._quests.get(quest_id)

    @property
    def quest_count(self) -> int:
        return len(self._quests)

    def get_tutorial_quests(self) -> list[QuestDef]:
        """Return Angel Lyceum tutorial quests (IDs 100-126)."""
        return [q for qid, q in sorted(self._quests.items())
                if 100 <= qid <= 130]

    def accept_quest(self, player_quests: dict[int, QuestState],
                     quest_id: int) -> Optional[QuestState]:
        """Player accepts a quest. Returns QuestState or None if invalid."""
        qdef = self._quests.get(quest_id)
        if not qdef:
            log.warning(f"Quest {quest_id} not found")
            return None
        if quest_id in player_quests and not qdef.repeatable:
            log.info(f"Quest {quest_id} already active/completed and not repeatable")
            return None

        state = QuestState(quest_id=quest_id, current_step=1)
        player_quests[quest_id] = state
        log.info(f"Quest accepted: {qdef.name} (ID {quest_id})")
        return state

    def advance_step(self, player_quests: dict[int, QuestState],
                     quest_id: int) -> Optional[QuestState]:
        """Advance quest to next step. Returns updated state or None if done."""
        state = player_quests.get(quest_id)
        if not state:
            return None
        qdef = self._quests.get(quest_id)
        if not qdef:
            return None

        if state.current_step >= len(qdef.steps):
            state.completed = True
            log.info(f"Quest completed: {qdef.name} (ID {quest_id})")
            return state

        state.current_step += 1
        log.info(f"Quest {qdef.name} advanced to step {state.current_step}")
        return state

    def get_step_text(self, quest_id: int, step_index: int) -> str:
        """Get display text for a quest step."""
        qdef = self._quests.get(quest_id)
        if not qdef or step_index < 1 or step_index > len(qdef.steps):
            return ""
        return _clean_text(qdef.steps[step_index - 1].description)

    def format_quest_info(self, quest_id: int,
                          state: Optional[QuestState] = None) -> str:
        """Format quest info for display to the player."""
        qdef = self._quests.get(quest_id)
        if not qdef:
            return f"Unknown quest {quest_id}"

        lines = [f"[{qdef.name}]"]
        lines.append(_clean_text(qdef.description))
        if state:
            step_idx = state.current_step
            if step_idx <= len(qdef.steps):
                step = qdef.steps[step_idx - 1]
                lines.append(f"Current: {_clean_text(step.description)}")
        return '\n'.join(lines)
