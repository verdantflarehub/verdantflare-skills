"""Captured Codex goal integrity, not permission or approval.

Provider readback and independent scope review remain mandatory. This module is
consumed by plan_lint through delegated_policy; local captures are not signed attestations.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from native_receipts import ReceiptError, _json, identifier, require

MAX_CAPTURE_BYTES = 16 * 1024 * 1024
MAX_GOAL_BYTES = 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}\Z')
GOAL_WRAPPER = re.compile(
    r'<codex_internal_context source="user_goal">\n'
    r'(?:User set the goal: "([^\r\n]+)"\n)?'
    r'User set goal status: "([a-z_]+)"\.\n'
    r'</codex_internal_context>\Z')
# Verified full native continuation template; only decimal budget counters vary.
AUTO_TEMPLATE_SHA256 = 'a2d94b9cedd2fa711c88408de39886fdc24f9ef8c372b7b758954e4c28f2da97'

@dataclass(frozen=True)
class GoalEvidence:
    session_id: str
    authorization_event_sha256: str
    user_history_sha256: str
    user_event_count: int
    observation_sha256: str
    automatic_continuation_count: int


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: str) -> None:
    require(isinstance(value, str) and bool(HEX.fullmatch(value)), 'Invalid expected evidence hash')


def _user_blocks(row: dict) -> list:
    require(row.get('type') == 'response_item', 'Not a native response item')
    p = row.get('payload')
    require(isinstance(p, dict) and p.get('type') == 'message' and p.get('role') == 'user',
            'Not a native user message')
    blocks = p.get('content')
    require(isinstance(blocks, list) and bool(blocks)
            and all(isinstance(b, dict) and isinstance(b.get('type'), str) for b in blocks),
            'Invalid user message content')
    return blocks


def _user_text(row: dict) -> str:
    blocks = _user_blocks(row)
    require(len(blocks) == 1 and blocks[0].get('type') == 'input_text'
            and isinstance(blocks[0].get('text'), str), 'Unsupported goal message content')
    return blocks[0]['text']


def _timestamp(row: dict) -> float:
    value = row.get('timestamp')
    require(isinstance(value, str), 'Missing native event timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None, 'Naive event timestamp')
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise ReceiptError('Invalid native event timestamp') from exc


def _goal(goal: dict, sid: str) -> None:
    require(isinstance(goal, dict) and goal.get('threadId') == sid, 'Goal session mismatch')
    require(isinstance(goal.get('objective'), str) and bool(goal['objective'].strip()), 'Missing objective')
    require(goal.get('status') in {'active', 'paused', 'blocked', 'complete', 'budget_limited', 'usage_limited'},
            'Unknown goal state')
    require(type(goal.get('createdAt')) is int and type(goal.get('updatedAt')) is int
            and 0 <= goal['createdAt'] <= goal['updatedAt'], 'Invalid goal timestamps')


def _automatic(text: str) -> bool:
    if not text.startswith('<codex_internal_context source="goal">\n'):
        return False
    normalized = text
    for field in ('Tokens used', 'Tokens remaining', 'Token budget'):
        normalized = re.sub(rf'(?m)^- {field}: \d+$', f'- {field}: <count>', normalized)
    return _sha(normalized.encode()) == AUTO_TEMPLATE_SHA256


def validate_goal_evidence(capture: bytes, observation: bytes, *, session_id: str,
                           authorization_event_sha256: str, user_history_sha256: str,
                           observation_sha256: str) -> GoalEvidence:
    """Capture = one session_meta + all user and goal/user-message event lines.

    Preserve original bytes/order. The policy-history digest covers every user
    record except a recognized automatic continuation without a user_message
    companion. Goal updates remain in the capture and are validated separately.
    A new digest alone cannot rehabilitate a revoked authorization selector.
    """
    try:
        identifier(session_id)
        for value in (authorization_event_sha256, user_history_sha256, observation_sha256):
            _hash(value)
        require(isinstance(capture, bytes) and 0 < len(capture) <= MAX_CAPTURE_BYTES,
                'Missing or oversized native capture')
        require(isinstance(observation, bytes) and 0 < len(observation) <= MAX_GOAL_BYTES,
                'Missing or oversized goal observation')
        require(capture.endswith(b'\n') and b'\r' not in capture, 'Incomplete or non-LF capture')
        lines = capture.splitlines(keepends=True)
        require(len(lines) >= 3 and all(line.strip() for line in lines), 'Incomplete native capture')
        rows = [_json(raw) for raw in lines]
        require(all(isinstance(r, dict) for r in rows), 'Invalid native event')
        meta = rows[0]
        require(meta.get('type') == 'session_meta' and isinstance(meta.get('payload'), dict)
                and meta['payload'].get('id') == session_id, 'Native session metadata mismatch')
        policy_lines = []; selected = None; selected_count = 0; latest = None
        previous_time = float('-inf'); previous_update = -1; latest_state_time = -1; users = 0; automatic = 0
        expected_companion = None
        for i in range(1, len(rows)):
            row, raw = rows[i], lines[i]
            p = row.get('payload')
            require(isinstance(p, dict), 'Invalid native payload')
            for obj in (row, p):
                for key in ('session_id', 'sessionId', 'thread_id', 'threadId'):
                    if key in obj:
                        require(obj[key] == session_id, 'Native event session mismatch')
            stamp = _timestamp(row)
            require(stamp >= previous_time, 'Native chronology is reversed')
            previous_time = stamp
            is_update = row.get('type') == 'event_msg' and p.get('type') == 'thread_goal_updated'
            if expected_companion is not None:
                require(is_update, 'Goal wrapper lacks adjacent native state event')
            if is_update:
                require(expected_companion is not None, 'Goal state lacks adjacent native wrapper')
                wrapper, is_selected = expected_companion; expected_companion = None
                current = p.get('goal'); _goal(current, session_id)
                require(p.get('threadId') == session_id and current['updatedAt'] >= previous_update
                        and current['updatedAt'] <= stamp, 'Goal state chronology mismatch')
                require((wrapper.group(1) is None or wrapper.group(1) == current['objective'])
                        and wrapper.group(2) == current['status'], 'Wrapper and goal state differ')
                previous_update = current['updatedAt']; latest_state_time = int(stamp); latest = current
                if is_selected:
                    require(current['status'] == 'active', 'Selected goal is not active')
                    selected = current
                elif selected is not None:
                    require(current['status'] == 'active'
                            and current['objective'] == selected['objective']
                            and current['createdAt'] == selected['createdAt'],
                            'Selected authorization was paused, replaced or terminated')
                continue
            if row.get('type') == 'event_msg':
                require(p.get('type') == 'user_message', 'Unsupported companion event')
                require(i > 1 and rows[i - 1].get('type') == 'response_item',
                        'Human event lacks its native user message')
                _user_blocks(rows[i - 1])
                # User-message bodies remain private; their native origin is
                # independently checked. They do not authorize anything here.
                continue
            _user_blocks(row); users += 1
            text = None
            try:
                text = _user_text(row)
            except ReceiptError:
                pass
            is_selected = _sha(raw) == authorization_event_sha256
            if is_selected: selected_count += 1
            wrapper = GOAL_WRAPPER.fullmatch(text) if text is not None else None
            if is_selected or (text and text.startswith('<codex_internal_context source="user_goal">')):
                require(wrapper is not None, 'Unsupported goal event wrapper')
            if wrapper is not None:
                expected_companion = (wrapper, is_selected)
            next_row = rows[i + 1] if i + 1 < len(rows) else {}
            human_companion = (next_row.get('type') == 'event_msg'
                               and next_row.get('payload', {}).get('type') == 'user_message')
            if text and _automatic(text) and not human_companion:
                require(latest is not None and latest['status'] == 'active', 'Continuation lacks active native goal')
                automatic += 1
            else:
                policy_lines.append(raw)
        require(expected_companion is None, 'Truncated goal state pair')
        require(selected_count == 1 and selected is not None, 'Authorization event missing or duplicated')
        require(_sha(b''.join(policy_lines)) == user_history_sha256, 'Reviewed user history changed')
        require(_sha(observation) == observation_sha256, 'Goal observation hash mismatch')
        readback = _json(observation)
        require(isinstance(readback, dict), 'Invalid goal service readback')
        goal = readback.get('goal'); _goal(goal, session_id)
        require(goal['status'] == 'active' and goal['objective'] == selected['objective']
                and goal['createdAt'] == selected['createdAt'], 'Goal readback changed or is inactive')
        # Usage accounting also advances updatedAt, so equality with the last
        # state event is not required; an older observation is always rejected.
        require(latest is not None and goal['updatedAt'] >= max(latest['updatedAt'], latest_state_time),
                'Stale goal observation')
        return GoalEvidence(session_id, authorization_event_sha256, user_history_sha256,
                            users, observation_sha256, automatic)
    except (TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ReceiptError('Malformed goal evidence') from exc
