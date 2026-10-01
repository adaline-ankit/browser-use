import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from browser_use.agent.optexity_step_cache import record_action_trace, trace_actions_to


class TraceTests(unittest.TestCase):
	def test_disabled_metadata_preserves_existing_contract(self):
		from unittest.mock import patch

		from browser_use.agent.optexity_step_cache import execution_metadata

		with patch('browser_use.agent.optexity_step_cache.tracing_enabled', return_value=False):
			self.assertIsNone(execution_metadata(None, None))
			self.assertEqual(execution_metadata({'x': 1}, None), {'x': 1})

	def record(self, value='test-secret'):
		record_action_trace(
			task='enter ' + value,
			step_number=1,
			action_number=1,
			total_actions=1,
			action_name='input',
			action_data={'input': {'index': 1, 'text': value}},
			result={
				'extracted_content': 'Typed ' + value,
				'metadata': {
					'optexity_trace': {
						'target': {'tag_name': 'input', 'attributes': {'type': 'password', 'value': value}, 'scope': 'document'},
						'executed': True,
					}
				},
			},
			elapsed_seconds=0.1,
		)

	def test_context_restores_after_exception(self):
		from browser_use.agent.optexity_step_cache import _RUN_ID, _TRACE_DIR

		with tempfile.TemporaryDirectory() as tmp:
			with trace_actions_to(tmp):
				outer = _RUN_ID.get()
				with self.assertRaises(RuntimeError):
					with trace_actions_to(Path(tmp) / 'inner'):
						raise RuntimeError('interrupted')
				self.assertEqual(_TRACE_DIR.get(), tmp)
				self.assertEqual(_RUN_ID.get(), outer)
			self.assertIsNone(_RUN_ID.get())

	def test_frame_and_shadow_scope_detected(self):
		from browser_use.agent.optexity_step_cache import _node_snapshot

		for parent, scope in [
			(SimpleNamespace(tag_name='iframe'), 'frame'),
			(SimpleNamespace(shadow_root_type='open'), 'shadow'),
		]:
			node = SimpleNamespace(tag_name='input', attributes={'name': 'x'}, parent_node=parent)
			self.assertEqual(_node_snapshot(node)['scope'], scope)

	def test_snapshot_drops_dom_value(self):
		from browser_use.agent.optexity_step_cache import _node_snapshot

		node = SimpleNamespace(tag_name='input', attributes={'name': 'x', 'value': 'private'})
		self.assertNotIn('value', _node_snapshot(node)['attributes'])

	def test_secret_absent_from_entire_row(self):
		with tempfile.TemporaryDirectory() as tmp:
			with trace_actions_to(tmp):
				self.record()
			text = (Path(tmp) / 'browser_use_trace.jsonl').read_text()
			self.assertNotIn('test-secret', text)
			self.assertTrue(json.loads(text)['redacted_fields'])

	def test_contexts_isolate_concurrent_tasks(self):
		async def run(root):
			async def one(name):
				with trace_actions_to(root / name):
					await asyncio.sleep(0)
					self.record(name)

			await asyncio.gather(one('a'), one('b'))

		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			asyncio.run(run(root))
			for name in ('a', 'b'):
				self.assertEqual(len((root / name / 'browser_use_trace.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
	unittest.main()
