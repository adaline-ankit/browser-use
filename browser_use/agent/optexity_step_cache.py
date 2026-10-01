"""Opt-in execution evidence for the companion Optexity cache compiler.

This trace is not a credential store. Full conversations remain governed by the
caller's logging policy; only this module's JSONL output is sanitized here.
"""

import copy
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

_TRACE_DIR: ContextVar[str | None] = ContextVar('optexity_trace_dir', default=None)
_RUN_ID: ContextVar[str | None] = ContextVar('optexity_trace_run', default=None)
_SENSITIVE_MARKER = '<redacted:sensitive_input>'
_SENSITIVE_HINTS = (
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
)
_LOCATOR_ATTRIBUTES = {'id', 'data-testid', 'name', 'aria-label', 'placeholder', 'type', 'title', 'href'}


@contextmanager
def trace_actions_to(trace_dir: str | Path) -> Iterator[None]:
	"""Isolate async tasks and restore the previous trace context on any exit."""
	token = _TRACE_DIR.set(str(trace_dir))
	run_token = _RUN_ID.set(str(uuid.uuid4()))
	try:
		yield
	finally:
		_RUN_ID.reset(run_token)
		_TRACE_DIR.reset(token)


def tracing_enabled() -> bool:
	return bool(_TRACE_DIR.get() or os.environ.get('OPTEXITY_BROWSER_USE_TRACE_DIR'))


def _node_snapshot(node: Any) -> dict[str, Any] | None:
	if node is None:
		return None
	# A document-level XPath cannot address shadow roots or child frames.
	scope = 'document'
	ancestor = node
	visited = set()
	while ancestor is not None and id(ancestor) not in visited:
		visited.add(id(ancestor))
		if getattr(ancestor, 'shadow_root_type', None):
			scope = 'shadow'
		if str(getattr(ancestor, 'tag_name', '')).lower() in {'iframe', 'frame'}:
			scope = 'frame'
		ancestor = getattr(ancestor, 'parent_node', None)
	attrs = getattr(node, 'attributes', None) or {}
	return {
		'tag_name': getattr(node, 'tag_name', None),
		'xpath': getattr(node, 'xpath', None),
		'attributes': {k: v for k, v in attrs.items() if k in _LOCATOR_ATTRIBUTES},
		'scope': scope,
	}


def capture_target(node: Any) -> dict[str, Any] | None:
	"""Called by the tool after resolving its actual node, before dispatch."""
	return _node_snapshot(node) if tracing_enabled() else None


def execution_metadata(metadata: Any, target: dict[str, Any] | None, *, sensitive: bool = False) -> dict[str, Any]:
	result = dict(metadata) if isinstance(metadata, dict) else {}
	if tracing_enabled():
		result['optexity_trace'] = {'target': target, 'executed': True, 'sensitive': sensitive}
	return result


def _looks_sensitive(target: dict[str, Any] | None) -> bool:
	if target is None:
		return True  # Missing evidence must not make an input safe to store.
	attrs = target.get('attributes') or {}
	text = ' '.join(str(v).lower() for v in attrs.values())
	return any(hint in text for hint in _SENSITIVE_HINTS)


def _scrub(value: Any, secret: str) -> Any:
	if isinstance(value, str):
		return value.replace(secret, _SENSITIVE_MARKER) if secret else value
	if isinstance(value, dict):
		return {k: _scrub(v, secret) for k, v in value.items() if k != 'value'}
	if isinstance(value, list):
		return [_scrub(v, secret) for v in value]
	return value


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
	"""Append returned action evidence. Recorder errors deliberately fail learning.

	Never promote a run whose trace could not be written. The cached selector
	map argument remains for API compatibility; actual target comes from tool
	metadata, not the potentially stale model snapshot.
	"""
	trace_dir = _TRACE_DIR.get() or os.environ.get('OPTEXITY_BROWSER_USE_TRACE_DIR')
	if not trace_dir:
		return
	result_data = result.model_dump(exclude_none=True) if hasattr(result, 'model_dump') else result
	metadata = (result_data.get('metadata') or {}).get('optexity_trace') or {}
	target = copy.deepcopy(metadata.get('target'))
	params = action_data.get(action_name) or {}
	# Do not duplicate model prose, errors, task text, or arbitrary DOM values.
	allowed = {'input': {'index', 'text', 'clear'}, 'click': {'index'}, 'wait': {'seconds'}, 'done': {'success'}}.get(
		action_name, set()
	)
	stored = {k: v for k, v in params.items() if k in allowed}
	redacted_fields = []
	if action_name == 'input' and (metadata.get('sensitive') or _looks_sensitive(target)):
		secret = str(stored.get('text', ''))
		stored['text'] = _SENSITIVE_MARKER
		target = _scrub(target, secret)
		redacted_fields = ['input.text']
	row = {
		'schema_version': 2,
		'run_id': _RUN_ID.get(),
		'created_at': time.time(),
		'task_sha256': hashlib.sha256(task.encode()).hexdigest(),
		'step_number': step_number,
		'action_number': action_number,
		'total_actions': total_actions,
		'action_name': action_name,
		'action': {action_name: stored},
		'target': target,
		'executed': metadata.get('executed', False),
		'redacted_fields': redacted_fields,
		'result': {k: result_data[k] for k in ('is_done', 'success') if k in result_data},
		'elapsed_seconds': round(elapsed_seconds, 6),
	}
	if result_data.get('error'):
		row['result']['error'] = True
	path = Path(trace_dir)
	path.mkdir(parents=True, exist_ok=True)
	with (path / 'browser_use_trace.jsonl').open('a') as f:
		f.write(json.dumps(row) + '\n')
