import json
import os
import time
from pathlib import Path
from typing import Any


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
	trace_dir = os.environ.get('OPTEXITY_BROWSER_USE_TRACE_DIR')
	if not trace_dir:
		return

	index = _target_index(action_data)
	node = cached_selector_map.get(index) if index is not None else None
	row = {
		'schema_version': 1,
		'created_at': time.time(),
		'task': task,
		'step_number': step_number,
		'action_number': action_number,
		'total_actions': total_actions,
		'action_name': action_name,
		'action': action_data,
		'target_index': index,
		'target': _node_snapshot(node),
		'result': _safe_model_dump(result),
		'elapsed_seconds': round(elapsed_seconds, 6),
	}

	path = Path(trace_dir)
	path.mkdir(parents=True, exist_ok=True)
	with (path / 'browser_use_trace.jsonl').open('a') as f:
		f.write(json.dumps(row, default=str) + '\n')
