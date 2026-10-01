import copy
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

_TRACE_DIR: ContextVar[str | None] = ContextVar('optexity_trace_dir', default=None)
_SENSITIVE_MARKER = '<redacted:sensitive_input>'
_SENSITIVE_HINTS = {
	'password',
	'passwd',
	'pwd',
	'token',
	'secret',
	'api_key',
	'apikey',
	'auth',
	'credential',
	'otp',
	'2fa',
	'totp',
	'card',
	'cvv',
	'cvc',
}


@contextmanager
def trace_actions_to(trace_dir: str | Path) -> Iterator[None]:
	"""Task-local trace target for Optexity action recording."""
	token = _TRACE_DIR.set(str(trace_dir))
	try:
		yield
	finally:
		_TRACE_DIR.reset(token)


def _safe_model_dump(value: Any) -> Any:
	if hasattr(value, 'model_dump'):
		return value.model_dump(exclude_none=True)
	if isinstance(value, dict):
		return {k: _safe_model_dump(v) for k, v in value.items()}
	if isinstance(value, list):
		return [_safe_model_dump(v) for v in value]
	return value


def _node_snapshot(node: Any) -> dict[str, Any] | None:
	if node is None:
		return None

	attrs = getattr(node, 'attributes', None) or getattr(node, 'attrs', None) or {}
	return {
		'tag_name': getattr(node, 'tag_name', None),
		'xpath': getattr(node, 'xpath', None),
		'attributes': attrs,
		'is_interactive': getattr(node, 'is_interactive', None),
		'is_visible': getattr(node, 'is_visible', None),
		'text': getattr(node, 'text', None),
		'parent_branch_hash': node.parent_branch_hash() if hasattr(node, 'parent_branch_hash') else None,
	}


def _target_index(action_data: dict[str, Any]) -> int | None:
	for params in action_data.values():
		if isinstance(params, dict) and isinstance(params.get('index'), int):
			return params['index']
	return None


def _looks_sensitive(target: dict[str, Any] | None) -> bool:
	if not target:
		return False
	attrs = target.get('attributes') or {}
	parts = [target.get('tag_name'), target.get('text')]
	parts.extend(str(value) for value in attrs.values() if value is not None)
	needle = ' '.join(part.lower() for part in parts if isinstance(part, str))
	input_type = str(attrs.get('type', '')).lower()
	return input_type == 'password' or any(hint in needle for hint in _SENSITIVE_HINTS)


def _redact_sensitive_action_data(action_name: str, action_data: dict[str, Any], target: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
	if action_name != 'input' or not _looks_sensitive(target):
		return action_data, []

	redacted = copy.deepcopy(action_data)
	params = redacted.get(action_name)
	if isinstance(params, dict) and 'text' in params:
		params['text'] = _SENSITIVE_MARKER
		return redacted, [f'{action_name}.text']
	return redacted, []


def record_action_trace(
	*,
	task: str,
	step_number: int,
	action_number: int,
	total_actions: int,
	action_name: str,
	action_data: dict[str, Any],
	result: Any,
	elapsed_seconds: float,
	cached_selector_map: dict[int, Any],
) -> None:
	trace_dir = _TRACE_DIR.get() or os.environ.get('OPTEXITY_BROWSER_USE_TRACE_DIR')
	if not trace_dir:
		return

	index = _target_index(action_data)
	node = cached_selector_map.get(index) if index is not None else None
	target = _node_snapshot(node)
	stored_action_data, redacted_fields = _redact_sensitive_action_data(action_name, action_data, target)
	row = {
		'schema_version': 1,
		'created_at': time.time(),
		'task': task,
		'step_number': step_number,
		'action_number': action_number,
		'total_actions': total_actions,
		'action_name': action_name,
		'action': stored_action_data,
		'target_index': index,
		'target': target,
		'redacted_fields': redacted_fields,
		'result': _safe_model_dump(result),
		'elapsed_seconds': round(elapsed_seconds, 6),
	}

	path = Path(trace_dir)
	path.mkdir(parents=True, exist_ok=True)
	with (path / 'browser_use_trace.jsonl').open('a') as f:
		f.write(json.dumps(row, default=str) + '\n')
