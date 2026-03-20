"""
Angels Online dialog manager.

Loads NPC dialog trees from:
  - server/data/game_xml/msg.xml    (8,595 text strings, tag <字串>)
  - server/data/game_xml/spmsg.xml  (703 global dialog trees, tag <對話>)
  - MapData.local_dialogs           (per-map dialog trees from MPC event XML)

spmsg.xml dialog node attributes (Traditional Chinese):
  編號  = dialog ID
  訊息  = text string ID (looked up in msg.xml via 文字 attribute)
  臉譜  = NPC portrait/face sprite ID
  RefEvent, FirstMsg = client-side event hints (ignored server-side)

Children:
  <選項 訊息="M" 下一句="N"/>   = player option: text=msg[M], next=N (0=close)
  <觸發器 ...>                   = conditional branches (server evaluates conditions)
    <條件 編號="T"><參數 數值="V"/>  = condition type T with parameter V
  <成立 下一句="N"/>             = unconditional branch target (after trigger block)

Dialog traversal:
  1. NPC entity is interacted with → look up dialog_id in map's npc_dialogs
  2. start_dialog(dialog_id) → DialogState
  3. Send current node text + options to client
  4. Player selects option K → select_option(state, K) → next DialogState (or None = close)
  5. Nodes with no options use advance() to follow unconditional_next chain
"""

import xml.etree.ElementTree as ET
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger('dialog_manager')


@dataclass
class DialogAction:
    """An action attached to a dialog trigger (e.g. action 37 = zone warp)."""
    action_type: int            # 37 = warp, 25 = start dialog, etc.
    params: list[int] = field(default_factory=list)


@dataclass
class DialogOption:
    msg_id: int
    text: str       # Resolved from msg.xml
    next_id: int    # 0 = close dialog


@dataclass
class DialogNode:
    dialog_id: int
    msg_id: int
    text: str       # Resolved NPC speech text
    face: int       # Portrait sprite ID

    options: list[DialogOption] = field(default_factory=list)
    # Conditional branches — [{conditions: [{type, params}]}]
    triggers: list[dict] = field(default_factory=list)
    # Unconditional next dialog ID (used by trigger-gated nodes, no options)
    unconditional_next: int = 0
    # Actions from trigger blocks (e.g. action 37 = zone warp)
    actions: list[DialogAction] = field(default_factory=list)


@dataclass
class DialogState:
    dialog_id: int
    node: DialogNode
    npc_entity_id: int = 0     # Runtime entity ID of the NPC this session is with

    @property
    def is_closed(self) -> bool:
        return self.dialog_id == 0


class DialogManager:
    """
    Loads and traverses NPC dialog trees.

    Usage:
        dm = DialogManager()
        dm.load(Path('server/data/game_xml'))
        dm.merge_local_dialogs(map_data.local_dialogs)  # per-map dialogs

        state = dm.start_dialog(dialog_id, npc_entity_id=0x12345678)
        # state.node.text  →  NPC speech
        # state.node.options  →  player choices
        state = dm.select_option(state, 0)  # player picks option 0
    """

    def __init__(self):
        self._texts: dict[int, str] = {}          # msg_id → text string
        self._raw_nodes: dict[int, dict] = {}      # dialog_id → raw parsed dict
        self._node_cache: dict[int, DialogNode] = {}  # dialog_id → built DialogNode

    def load(self, data_dir: str | Path):
        """Load msg.xml, spmsg.xml, and EVENT.XML from the given directory."""
        data_dir = Path(data_dir)
        self._load_msg_xml(data_dir / 'msg.xml')
        self._load_spmsg_xml(data_dir / 'spmsg.xml')
        # Global EVENT.XML (setting/ subdirectory) — same 對話 format as spmsg.xml
        self._load_event_xml(data_dir / 'setting' / 'EVENT.XML')
        log.info(f"DialogManager: {len(self._texts)} text strings, "
                 f"{len(self._raw_nodes)} dialog nodes")

    def _load_msg_xml(self, path: Path):
        try:
            root = ET.parse(str(path)).getroot()
        except (ET.ParseError, OSError) as e:
            log.error(f"Failed to load msg.xml from {path}: {e}")
            return
        count = 0
        for elem in root:
            if elem.tag == '字串':
                mid = elem.attrib.get('編號')
                text = elem.attrib.get('文字', '')
                if mid:
                    self._texts[int(mid)] = text
                    count += 1
        log.debug(f"msg.xml: {count} strings loaded")

    def _load_spmsg_xml(self, path: Path):
        try:
            root = ET.parse(str(path)).getroot()
        except (ET.ParseError, OSError) as e:
            log.error(f"Failed to load spmsg.xml from {path}: {e}")
            return
        count = 0
        for elem in root:
            if elem.tag == '對話':
                node = self._parse_dialog_elem(elem)
                if node:
                    self._raw_nodes[node['id']] = node
                    count += 1
        log.debug(f"spmsg.xml: {count} dialog nodes loaded")

    def _load_event_xml(self, path: Path):
        """Load global dialog nodes from setting/EVENT.XML.

        Uses the same 對話 (dialog) format as spmsg.xml.
        IDs are in range 1-10726, non-overlapping with spmsg.xml (500001+).
        """
        try:
            root = ET.parse(str(path)).getroot()
        except (ET.ParseError, OSError) as e:
            log.warning(f"Failed to load EVENT.XML from {path}: {e}")
            return
        count = 0
        for elem in root:
            if elem.tag == '對話':
                node = self._parse_dialog_elem(elem)
                if node:
                    self._raw_nodes[node['id']] = node
                    count += 1
        log.debug(f"EVENT.XML: {count} dialog nodes loaded")

    def _parse_dialog_elem(self, elem) -> Optional[dict]:
        did_str = elem.attrib.get('編號')
        if not did_str:
            return None
        did = int(did_str)
        msg_id = int(elem.attrib.get('訊息', 0))
        face = int(elem.attrib.get('臉譜', 0))

        node = {
            'id': did,
            'msg_id': msg_id,
            'face': face,
            'options': [],
            'triggers': [],
            'actions': [],
            'unconditional_next': 0,
        }

        for child in elem:
            if child.tag == '選項':
                node['options'].append({
                    'msg_id': int(child.attrib.get('訊息', 0)),
                    'next_id': int(child.attrib.get('下一句', 0)),
                })
            elif child.tag == '觸發器':
                conditions = []
                actions = []
                for tchild in child:
                    if tchild.tag == '條件':
                        ctype = int(tchild.attrib.get('編號', 0))
                        params = [int(p.attrib.get('數值', 0))
                                  for p in tchild if p.tag == '參數']
                        conditions.append({'type': ctype, 'params': params})
                    elif tchild.tag == '動作':
                        atype = int(tchild.attrib.get('編號', 0))
                        aparams = [int(p.attrib.get('數值', 0))
                                   for p in tchild if p.tag == '參數']
                        actions.append({'action_type': atype, 'params': aparams})
                node['triggers'].append({'conditions': conditions, 'actions': actions})
                node['actions'].extend(actions)
            elif child.tag == '成立':
                node['unconditional_next'] = int(child.attrib.get('下一句', 0))

        return node

    def merge_local_dialogs(self, local_dialogs: dict[int, dict]):
        """Merge per-map dialog nodes from MapData.local_dialogs.

        Local dialogs (IDs like 21901, 23101) are specific to one map.
        They reference the same msg.xml text strings as global dialogs.
        """
        for did, raw in local_dialogs.items():
            self._raw_nodes[did] = raw
            self._node_cache.pop(did, None)  # invalidate any cached build
        if local_dialogs:
            log.debug(f"Merged {len(local_dialogs)} local map dialog nodes")

    def get_text(self, msg_id: int) -> str:
        """Look up display text for a message ID."""
        return self._texts.get(msg_id, f'[msg:{msg_id}]')

    def get_node(self, dialog_id: int) -> Optional[DialogNode]:
        """Get a built DialogNode with resolved text, or None if not found."""
        if dialog_id == 0:
            return None
        if dialog_id in self._node_cache:
            return self._node_cache[dialog_id]

        raw = self._raw_nodes.get(dialog_id)
        if raw is None:
            return None

        options = [
            DialogOption(
                msg_id=opt['msg_id'],
                text=self.get_text(opt['msg_id']),
                next_id=opt['next_id'],
            )
            for opt in raw['options']
        ]
        actions = [
            DialogAction(action_type=a['action_type'], params=a['params'])
            for a in raw.get('actions', [])
        ]
        node = DialogNode(
            dialog_id=dialog_id,
            msg_id=raw['msg_id'],
            text=self.get_text(raw['msg_id']),
            face=raw['face'],
            options=options,
            triggers=raw.get('triggers', []),
            unconditional_next=raw.get('unconditional_next', 0),
            actions=actions,
        )
        self._node_cache[dialog_id] = node
        return node

    def start_dialog(self, dialog_id: int,
                     npc_entity_id: int = 0) -> Optional[DialogState]:
        """Begin a dialog session at the given dialog node.

        Returns a DialogState, or None if dialog_id is not found.
        """
        node = self.get_node(dialog_id)
        if node is None:
            log.warning(f"Dialog {dialog_id} not found (npc={npc_entity_id:#010x})")
            return None

        log.info(f"Dialog start: id={dialog_id} npc={npc_entity_id:#010x} "
                 f'face={node.face} text="{node.text[:80]}"')
        if node.options:
            for i, opt in enumerate(node.options):
                log.info(f"  Option {i}: [{opt.next_id}] \"{opt.text[:60]}\"")

        return DialogState(dialog_id=dialog_id, node=node,
                           npc_entity_id=npc_entity_id)

    def select_option(self, state: DialogState,
                      option_index: int) -> Optional[DialogState]:
        """Player selects option_index. Returns the next DialogState, or None to close.

        Returns None if:
          - option_index is out of range
          - the selected option has next_id == 0 (dialog ends)
        """
        options = state.node.options
        if option_index < 0 or option_index >= len(options):
            log.warning(f"Dialog {state.dialog_id}: invalid option {option_index} "
                        f"(have {len(options)})")
            return None

        opt = options[option_index]
        log.info(f"Dialog option selected: {option_index} "
                 f'"{opt.text[:50]}" → dialog {opt.next_id}')

        if opt.next_id == 0:
            log.info(f"Dialog closed by option selection")
            return None

        return self.start_dialog(opt.next_id, state.npc_entity_id)

    def advance(self, state: DialogState) -> Optional[DialogState]:
        """Advance through a node with no player options (follows unconditional_next).

        Used when the dialog node has no 選項 children — the server
        automatically moves to the next node.
        """
        next_id = state.node.unconditional_next
        if next_id == 0:
            return None
        return self.start_dialog(next_id, state.npc_entity_id)

    def close(self, state: DialogState):
        """Log dialog closure."""
        log.info(f"Dialog closed: id={state.dialog_id} "
                 f"npc={state.npc_entity_id:#010x}")
