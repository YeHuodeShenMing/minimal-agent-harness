"""Explicit opt-in real API checks; prints/writes only synthetic test data, never credentials."""
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_harness.config import load_env
from agent_harness.model import ChatModel
from agent_harness.runtime import Runtime, size, SYSTEM
from agent_harness.store import Store
from agent_harness.tools import default_registry


def main():
    load_env()
    model = ChatModel()
    checks = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / 'live.db')
        runtime = Runtime(model, store)

        def ask(name, session, text, user='A', agent=None):
            before = store.events(user, session, 1)
            seq = before[-1]['seq'] if before else 0
            t = time.monotonic()
            answer = (agent or runtime).chat(user, session, text)
            events = [e for e in store.events(user, session, 200) if e['seq'] > seq]
            assert events[-1]['kind'] == 'final', (name, answer)
            tools = [e['payload'] for e in events if e['kind'] == 'tool']
            check = {'name': name, 'answer': answer, 'tools': tools,
                     'seconds': round(time.monotonic() - t, 2)}
            checks.append(check)
            print(name + ': completed', flush=True)
            return answer, tools

        answer, tools = ask('weather_and_todo', 'one',
            '我叫小林。请调用weather查广州天气，再调用todo添加“明天带伞”，然后告诉我结果。')
        assert {'weather', 'todo'} <= {t['name'] for t in tools}
        assert store.load('A', 'one')['todos'][0]['text'] == '明天带伞'
        answer, tools = ask('independent_second_window', 'two',
            '请帮我写一句周报开头，并调用todo添加“周五交周报”。')
        assert store.load('A', 'two')['todos'][0]['text'] == '周五交周报'
        assert len(store.load('A', 'one')['todos']) == 1
        answer, tools = ask('pure_followup_after_restart', 'one', '我叫什么？只回答名字，不需要工具。',
                            agent=Runtime(model, Store(store.path)))
        assert '小林' in answer and not tools
        answer, tools = ask('tool_followup', 'one', '把刚才带伞的待办标为完成，请先查列表再修改。')
        assert store.load('A', 'one')['todos'][0]['done'] is True
        assert store.load('A', 'two')['todos'][0]['done'] is False
        answer, tools = ask('other_user_isolation', 'one', '请用todo查询我在当前窗口的所有待办。', user='B')
        assert any(t['name'] == 'todo' and t['result']['data'] == {'items': []} for t in tools)
        answer, tools = ask('search_and_calculator', 'tools',
            '请用search搜索agent runtime，并用calculator计算(128+72)*3，给我搜索信息和计算结果。')
        assert {'search', 'calculator'} <= {t['name'] for t in tools}
        assert any(t['name'] == 'calculator' and t['result']['data']['result'] == 600 for t in tools)
        answer, tools = ask('autonomous_tool_choice', 'natural',
            '帮我算一下(317+683)*7，然后把“周六上午复习Agent协议”记到待办里。')
        assert {'calculator', 'todo'} <= {t['name'] for t in tools}
        assert any(t['name'] == 'calculator' and t['result'].get('data', {}).get('result') == 7000 for t in tools)
        assert len(store.load('A', 'natural')['todos']) == 1
        answer, tools = ask('natural_delete_followup', 'natural', '刚才那个待办取消了，帮我删掉。')
        assert store.load('A', 'natural')['todos'] == []
        state = store.load('A', 'compact')
        state['messages'] = [
            {'role': 'user', 'content': '记住：我的项目代号是星河七号，验收日期12月8日。'},
            {'role': 'assistant', 'content': '记住了。' + '历史讨论背景。' * 900},
            {'role': 'user', 'content': '我们继续讨论测试，暂时没有新增决定。'},
            {'role': 'assistant', 'content': '好的。' + '测试覆盖基本边界。' * 600}]
        store.save('A', 'compact', state, 'fixture', {'synthetic': True})
        budget = size([{'role': 'system', 'content': SYSTEM}]) + size(default_registry().specs()) + 16000
        answer, tools = ask('real_summary_and_followup', 'compact', '我的项目代号和验收日期是什么？',
                            agent=Runtime(model, store, budget=budget))
        assert '星河七号' in answer and ('12月8' in answer or '12 月 8' in answer or '12-08' in answer)
        assert any(e['kind'] == 'compaction' for e in store.events('A', 'compact'))
    report = {'executed_at': datetime.now(timezone.utc).isoformat(), 'model': model.model,
              'provider': model.base, 'status': 'passed', 'checks': checks,
              'total_seconds': round(time.monotonic() - started, 2)}
    destination = Path(__file__).resolve().parents[1] / 'docs' / 'validation-live.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('PASS: ' + str(destination))


if __name__ == '__main__':
    main()
