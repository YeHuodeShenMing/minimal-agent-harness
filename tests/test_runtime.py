import copy
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_harness.model import ChatModel, ModelError, ProtocolError, parse
from agent_harness.runtime import Runtime, SYSTEM, size
from agent_harness.store import Store, BusyError
from agent_harness.tools import default_registry, Tool


def response(text=None, calls=None):
    return {'choices': [{'finish_reason': 'tool_calls' if calls else 'stop',
                         'message': {'content': text, 'tool_calls': calls or []}}]}


def call(name, args, id='c1'):
    return {'id': id, 'type': 'function', 'function': {'name': name,
            'arguments': args if isinstance(args, str) else json.dumps(args)}}


class ScriptedModel:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []

    def complete(self, messages, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        result = self.outputs.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def try_lock(path, queue):
    try:
        with Store(path).lock('A', 'one'):
            queue.put('acquired')
    except BusyError:
        queue.put('busy')


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        self.store = Store(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def run_agent(self, outputs, text='test', **kwargs):
        model = ScriptedModel(*outputs)
        result = Runtime(model, self.store, **kwargs).chat('A', 'one', text)
        return result, model


class RuntimeTests(RuntimeFixture):
    def test_direct_and_pure_follow_up_after_restart(self):
        self.run_agent([response('你好，小林')], '我叫小林')
        result, model = self.run_agent([response('你叫小林')], '我叫什么？')
        self.assertEqual(result, '你叫小林')
        self.assertIn('我叫小林', str(model.requests))
        self.assertFalse(self.store.load('A', 'one')['active'])

    def test_multi_tool_and_follow_up(self):
        _, model = self.run_agent([response(calls=[call('calculator', {'expression': '12*3'}),
            call('todo', {'action': 'add', 'text': '交周报'}, 'c2')]), response('36；已添加')])
        self.assertEqual(len([m for m in model.requests[1][0] if m['role'] == 'tool']), 2)
        id = self.store.load('A', 'one')['todos'][0]['id']
        self.run_agent([response(calls=[call('todo', {'action': 'list'})]),
            response(calls=[call('todo', {'action': 'complete', 'id': id}, 'c2')]), response('完成')],
            '把刚才的待办标为完成')
        self.assertTrue(self.store.load('A', 'one')['todos'][0]['done'])

    def test_user_and_window_isolation(self):
        self.run_agent([response(calls=[call('todo', {'action': 'add', 'text': '秘密'})]), response('ok')])
        model = ScriptedModel(response('无待办'), response('无待办'))
        runtime = Runtime(model, self.store)
        runtime.chat('A', 'two', '有待办吗')
        runtime.chat('B', 'one', '有待办吗')
        self.assertNotIn('秘密', str(model.requests))
        self.assertEqual(self.store.load('A', 'two')['todos'], [])
        self.assertEqual(self.store.load('B', 'one')['todos'], [])

    def test_tool_errors_are_results_and_model_can_repair(self):
        self.run_agent([response(calls=[call('calculator', '{bad')]),
            response(calls=[call('calculator', {'expression': '1/0'}, 'c2')]),
            response(calls=[call('missing', {}, 'c3')]),
            response(calls=[call('calculator', {'expression': '2+2'}, 'c4')]), response('4')])
        tools = [e for e in self.store.events('A', 'one') if e['kind'] == 'tool']
        self.assertEqual([e['payload']['result']['ok'] for e in tools], [False, False, False, True])
        self.assertTrue(all('duration_ms' in e['payload'] for e in tools))

    def test_max_steps_closes_all_tool_calls(self):
        result, _ = self.run_agent([response(calls=[call('todo', {'action': 'list'})])] * 2, max_steps=2)
        self.assertIn('上限', result)
        state = self.store.load('A', 'one')
        self.assertFalse(state['active'])
        self.assertEqual(len([m for m in state['messages'] if m['role'] == 'tool']), 2)

    def test_protocol_repair_then_success(self):
        result, _ = self.run_agent([{}, response('修复成功')])
        self.assertEqual(result, '修复成功')

    def test_repeated_protocol_error_stops(self):
        result, _ = self.run_agent([{}, {}])
        self.assertIn('未完成', result)
        self.assertEqual(self.store.events('A', 'one')[-1]['kind'], 'error')

    def test_model_failure_keeps_todos(self):
        self.run_agent([response(calls=[call('todo', {'action': 'add', 'text': '保留'})]), ModelError('offline')])
        self.assertEqual(len(self.store.load('A', 'one')['todos']), 1)
        self.run_agent([response('恢复')], '继续')

    def test_resume_does_not_repeat_committed_side_effect(self):
        model = ScriptedModel(response(calls=[call('todo', {'action': 'add', 'text': '一次'})]), KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            Runtime(model, self.store).chat('A', 'one', '添加')
        with self.assertRaisesRegex(ValueError, 'Interrupted'):
            Runtime(model, self.store).chat('A', 'one', '新消息')
        Runtime(ScriptedModel(response('完成')), Store(self.path)).chat('A', 'one')
        self.assertEqual(len(self.store.load('A', 'one')['todos']), 1)

    def test_resume_before_tool_execution(self):
        original = self.store.save
        def interrupt(user, session, state, kind, payload):
            original(user, session, state, kind, payload)
            if kind == 'decision':
                raise KeyboardInterrupt()
        with patch.object(self.store, 'save', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_agent([response(calls=[call('todo', {'action': 'add', 'text': '恢复'})])])
        Runtime(ScriptedModel(response('已恢复')), self.store).chat('A', 'one')
        self.assertEqual(len(self.store.load('A', 'one')['todos']), 1)

    def test_context_compaction_recalls_summary(self):
        self.run_agent([response('收到' + 'x' * 6000)], '我的项目叫星河')
        self.run_agent([response('继续' + 'x' * 6000)], '再聊一轮')
        budget = size([{'role': 'system', 'content': SYSTEM}]) + size(default_registry().specs()) + 8500
        _, model = self.run_agent([response('用户项目叫星河；没有未完成任务。'), response('项目是星河')],
                                 '项目叫什么', budget=budget)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(model.requests[0][1], [])
        self.assertIn('星河', model.requests[1][0][1]['content'])
        self.assertIn('compaction', [e['kind'] for e in self.store.events('A', 'one')])

    def test_compaction_failure_retains_history(self):
        self.run_agent([response('x' * 8000)], '保留我')
        before = self.store.load('A', 'one')['messages']
        self.run_agent([ModelError('summary unavailable')], '继续', budget=5000)
        self.assertEqual(self.store.load('A', 'one')['messages'][:len(before)], before)

    def test_single_turn_budget_does_not_call_model(self):
        answer, model = self.run_agent([], text='x' * 5000, budget=4000)
        self.assertIn('未完成', answer)
        self.assertEqual(model.requests, [])

    def test_invalid_inputs_never_persist(self):
        runtime = Runtime(ScriptedModel(), self.store)
        for text in (' ', 'x' * 12001):
            with self.assertRaises(ValueError):
                runtime.chat('A', 'one', text)
        self.assertEqual(self.store.events('A', 'one'), [])

    def test_busy_same_session_other_session_available(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        with self.store.lock('A', 'one'), self.store.lock('A', 'two'):
            proc = context.Process(target=try_lock, args=(self.path, queue))
            proc.start()
            proc.join(10)
            self.assertEqual(proc.exitcode, 0)
            self.assertEqual(queue.get(timeout=2), 'busy')
        queue.close()

    def test_oversize_output_rolls_back_mutation(self):
        registry = default_registry()
        def oversized(args, state):
            state['todos'].append({'text': 'must rollback'})
            return 'x' * 13000
        registry.register(Tool('oversized', '', {'type': 'object'}, oversized))
        self.run_agent([response(calls=[call('oversized', {})]), response('失败')], registry=registry)
        self.assertEqual(self.store.load('A', 'one')['todos'], [])


class ToolsAndParserTests(unittest.TestCase):
    def test_calculator_safe_arithmetic(self):
        r = default_registry()
        self.assertEqual(r.call('calculator', {'expression': '(128+72)*3'}, {})['result'], 600)
        for text in ("__import__('os').system('id')", '2**100000', '1/0', 'True', '[1]', '(-1)**0.5'):
            with self.assertRaises((ValueError, ZeroDivisionError)):
                r.call('calculator', {'expression': text}, {})

    def test_schema_validation(self):
        for args in ({}, {'action': 'erase'}, {'action': 1}, {'action': 'add', 'oops': 1}, []):
            with self.assertRaises(ValueError):
                default_registry().call('todo', args, {})

    def test_todo_crud(self):
        r, state = default_registry(), {}
        item = r.call('todo', {'action': 'add', 'text': 'test'}, state)
        self.assertEqual(len(r.call('todo', {'action': 'list'}, state)['items']), 1)
        r.call('todo', {'action': 'delete', 'id': item['id']}, state)
        self.assertEqual(state['todos'], [])

    def test_duplicate_registry(self):
        r = default_registry()
        with self.assertRaises(ValueError):
            r.register(r.tools['todo'])

    def test_malformed_outputs(self):
        for value in (None, [], {}, {'choices': []}, {'choices': [None]}, response(calls=[None]),
                      response(calls=[call('a', {}, 'same'), call('a', {}, 'same')]), response(),
                      response(calls=[call('a', {})] * 9)):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse(value)
        incomplete = response('truncated')
        incomplete['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(ProtocolError):
            parse(incomplete)

    def test_reasoning_not_replayed(self):
        raw = response('answer')
        raw['choices'][0]['message'].update(reasoning_content='private', reasoning_summary='brief decision')
        decision = parse(raw)
        self.assertEqual(decision.reasoning_summary, 'brief decision')
        self.assertNotIn('private', str(decision))
        self.assertNotIn('reasoning', str(decision.message()))

    def test_transport_retries_transient_failure_not_auth(self):
        import urllib.error
        with patch.dict('os.environ', {'LLM_API_KEY': 'test', 'LLM_MODEL': 'test'}):
            model = ChatModel()
        for status, count in ((429, 3), (503, 3), (401, 1)):
            with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
                    'https://example.test', status, '', {}, None)) as request, patch('time.sleep'):
                with self.assertRaises(ModelError):
                    model.complete([], [])
                self.assertEqual(request.call_count, count)

class ExtendedRegressionTests(RuntimeFixture):
    def test_200_turn_compaction_keeps_tool_protocol_and_business_state(self):
        state = self.store.load('A', 'one')
        state['todos'] = [{'id': 't1', 'text': '验收星河', 'done': False}]
        for i in range(200):
            state['messages'].extend([
                {'role': 'user', 'content': f'第{i}轮讨论星河项目。'},
                {'role': 'assistant', 'content': None,
                 'tool_calls': [call('todo', {'action': 'list'}, f'c{i}')]},
                {'role': 'tool', 'tool_call_id': f'c{i}', 'content': '{"items":[]}'},
                {'role': 'assistant', 'content': f'第{i}轮已完成。'}])
        self.store.save('A', 'one', state, 'fixture', {})
        class SummaryModel:
            def complete(self, messages, tools):
                return response('仍然是星河。' if tools else '项目是星河，保留待办t1。')
        answer = Runtime(SummaryModel(), self.store).chat('A', 'one', '继续讨论星河')
        self.assertEqual(answer, '仍然是星河。')
        state = self.store.load('A', 'one')
        self.assertEqual(state['todos'][0]['id'], 't1')
        self.assertLess(len(state['messages']), 20)
        for index, message in enumerate(state['messages']):
            if message.get('tool_calls'):
                expected = {c['id'] for c in message['tool_calls']}
                actual = {m['tool_call_id'] for m in state['messages'][index+1:index+1+len(expected)]}
                self.assertEqual(expected, actual)
        archives = [e['payload']['archived_messages'] for e in self.store.events('A', 'one')
                    if e['kind'] == 'compaction']
        self.assertGreater(len(archives[0]), 700)

    def test_invalid_summary_never_replaces_original(self):
        self.run_agent([response('x' * 8000)], '原始内容')
        before = self.store.load('A', 'one')['messages']
        answer, _ = self.run_agent([response('x' * 6001)], '继续', budget=5000)
        self.assertIn('未完成', answer)
        self.assertEqual(self.store.load('A', 'one')['messages'][:len(before)], before)

    def test_failed_mutation_rolls_back_on_tool_exception(self):
        registry = default_registry()
        def bad(args, state):
            state['todos'].append({'text': '不应提交'})
            raise ValueError('failed before commit')
        registry.register(Tool('bad', '', {'type': 'object'}, bad))
        self.run_agent([response(calls=[call('bad', {})]), response('失败')], registry=registry)
        self.assertEqual(self.store.load('A', 'one')['todos'], [])




class ReviewRegressionTests(RuntimeFixture):
    def test_full_todo_store_remains_readable_by_pages(self):
        r, state = default_registry(), {}
        for i in range(100):
            r.call('todo', {'action': 'add', 'text': str(i) + ('中' if i % 2 else chr(0)) * 490}, state)
        ids, offset = [], 0
        while offset is not None:
            result = r.call('todo', {'action': 'list', 'offset': offset}, state)
            self.assertLess(size({'ok': True, 'data': result}), 12000)
            ids.extend(t['id'] for t in result['items'])
            offset = result['next_offset']
        self.assertEqual(len(set(ids)), 100)
        r.call('todo', {'action': 'complete', 'id': ids[-1]}, state)
        self.assertTrue(state['todos'][-1]['done'])
        for args in ({'action': 'list', 'offset': -1}, {'action': 'list', 'limit': 100}):
            with self.assertRaises(ValueError):
                r.call('todo', args, state)

    def test_unexpected_tool_exception_is_returned_and_rolled_back(self):
        r = default_registry()
        def fail(args, state):
            state['todos'].append({'text': 'rollback'})
            raise RuntimeError('worker unavailable')
        r.register(Tool('fail', '', {'type': 'object'}, fail))
        answer, model = self.run_agent([response(calls=[call('fail', {})]), response('已报告错误')], registry=r)
        self.assertEqual(answer, '已报告错误')
        self.assertEqual(self.store.load('A', 'one')['todos'], [])
        self.assertIn('RuntimeError', str(model.requests[-1]))

    def test_summary_calls_are_bounded_and_rolling(self):
        model = ScriptedModel(*[response('保留项目名星河') for _ in range(5)])
        runtime = Runtime(model, self.store)
        result = runtime.summarize([{'role': 'assistant', 'content': 'x' * 20000}] * 5, '')
        self.assertEqual(result, '保留项目名星河')
        self.assertEqual(len(model.requests), 5)
        self.assertTrue(all(size(messages) <= 64000 for messages, _ in model.requests))
        self.assertIn('保留项目名星河', str(model.requests[1]))

    def test_summary_budget_fails_before_network_and_keeps_history(self):
        from agent_harness.runtime import ContextError
        model = ScriptedModel()
        runtime = Runtime(model, self.store)
        with self.assertRaises(ContextError):
            runtime.summarize([{'role': 'assistant', 'content': 'x' * 20000}] * 17, '')
        self.assertEqual(model.requests, [])

    def test_partial_batch_recovery_does_not_repeat_first_write(self):
        original = self.store.save
        def interrupt(user, session, state, kind, payload):
            original(user, session, state, kind, payload)
            if kind == 'tool':
                raise KeyboardInterrupt()
        with patch.object(self.store, 'save', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_agent([response(calls=[call('todo', {'action': 'add', 'text': '一'}, 'a'),
                                               call('todo', {'action': 'add', 'text': '二'}, 'b')])])
        Runtime(ScriptedModel(response('恢复完成')), self.store).chat('A', 'one')
        self.assertEqual([t['text'] for t in self.store.load('A', 'one')['todos']], ['一', '二'])

    def test_falsey_malformed_outputs_and_inconsistent_finish_rejected(self):
        for field, value in [('content', False), ('content', 0), ('content', []), ('tool_calls', {}),
                             ('tool_calls', False), ('role', 'user')]:
            raw = response('answer')
            raw['choices'][0]['message'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ProtocolError):
                parse(raw)
        for reason in ('tool_calls', None, 'unknown'):
            raw = response('answer')
            raw['choices'][0]['finish_reason'] = reason
            with self.assertRaises(ProtocolError):
                parse(raw)
        with self.assertRaises(ProtocolError):
            parse(response('   '))

    def test_connection_reset_and_truncated_http_are_model_errors(self):
        import http.client
        with patch.dict('os.environ', {'LLM_API_KEY': 'test', 'LLM_MODEL': 'test'}):
            model = ChatModel()
        for error in (ConnectionResetError(), http.client.IncompleteRead(b'partial')):
            with patch('urllib.request.urlopen', side_effect=error) as request, patch('time.sleep'):
                with self.assertRaises(ModelError):
                    model.complete([], [])
                self.assertEqual(request.call_count, 3)


if __name__ == '__main__':
    unittest.main()
