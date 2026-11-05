import tempfile
import threading
import time
import unittest
import sqlite3
from unittest.mock import patch
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from agent_harness.web import create_app
except ImportError:
    create_app = None


class GatedModel:
    model = 'test-model'

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError('test timed out')
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '你好，网页已连接真实 Runtime'}}]}


@unittest.skipIf(create_app is None, 'Install .[web,test-web] for web tests')
class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = GatedModel()
        self.app = create_app(model=self.model, db_path=str(Path(self.temp.name)/'test.db'),
                              secret='x'*40, secure=False)
        self.client = TestClient(self.app, base_url='http://localhost')

    def tearDown(self):
        self.model.release.set()
        self.app.state.executor.shutdown(wait=True)
        self.client.close()
        self.temp.cleanup()

    def login(self, client=None):
        client = client or self.client
        response = client.post('/api/visitor', json={})
        self.assertEqual(response.status_code, 200)
        return {'X-CSRF-Token': response.json()['csrf']}

    def new(self, headers):
        response = self.client.post('/api/sessions', json={'title':'会话 A'}, headers=headers)
        self.assertEqual(response.status_code, 201)
        return response.json()['id']

    def test_auth_csrf_origin_and_static_boundaries(self):
        self.assertEqual(self.client.get('/api/sessions').status_code, 401)
        headers = self.login()
        self.assertEqual(self.client.post('/api/sessions', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/sessions', json={}, headers={**headers,'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/healthz', headers={'Host':'evil.example'}).status_code, 400)
        self.assertEqual(self.client.get('/').status_code, 200)
        static = self.client.get('/static/app.js')
        self.assertEqual(static.status_code, 200)
        self.assertIn("script-src 'self'", static.headers['content-security-policy'])
        self.assertEqual(self.client.post('/api/visitor', content='x'*21000).status_code, 413)

    def test_anonymous_entry_reuses_identity(self):
        headers = self.login()
        session = self.new(headers)
        cookie = self.client.cookies['agent_session']
        for _ in range(12):
            self.assertEqual(self.client.post('/api/visitor', json={}).status_code, 200)
        self.assertEqual(self.client.cookies['agent_session'], cookie)
        self.assertEqual(self.client.get('/api/sessions').json()[0]['id'], session)
        self.assertEqual(self.client.post('/api/login', json={}).status_code, 404)

    def test_ownership_async_execution_and_busy(self):
        headers = self.login()
        session = self.new(headers)
        with TestClient(self.app, base_url='http://localhost') as other:
            other_headers = self.login(other)
            self.assertEqual(other.get('/api/sessions').json(), [])
            self.assertEqual(other.get('/api/sessions/'+session).status_code, 404)
            self.assertEqual(other.post('/api/sessions/'+session+'/messages', json={'text':'偷看'}, headers=other_headers).status_code, 404)
        url = '/api/sessions/'+session
        self.assertEqual(self.client.post(url+'/messages', json={'text':'你好'}, headers=headers).status_code, 202)
        self.assertTrue(self.model.entered.wait(2))
        self.assertTrue(self.client.get(url).json()['busy'])
        self.assertEqual(self.client.post(url+'/messages', json={'text':'重复'}, headers=headers).status_code, 409)
        self.model.release.set()
        for _ in range(100):
            state = self.client.get(url).json()
            if not state['busy']:
                break
            time.sleep(.01)
        self.assertFalse(state['busy'])
        self.assertFalse(state['active'])
        self.assertIn('网页已连接', state['messages'][-1]['content'])
        self.assertIn('final', [e['kind'] for e in state['events']])

    def test_persistent_quota_and_session_count(self):
        headers = self.login()
        session = self.new(headers)
        user = self.client.cookies['agent_session'].split('.')[0]
        day = time.strftime('%Y-%m-%d', time.gmtime())
        with self.app.state.store.connect() as db:
            db.execute('INSERT INTO web_usage VALUES(?,?,20)', (day,user))
        self.assertEqual(self.client.post('/api/sessions/'+session+'/messages', json={'text':'你好'}, headers=headers).status_code, 429)
        self.assertFalse(self.model.entered.is_set())

    def test_login_rate_limit_and_cookie_signature(self):
        self.login()
        token = self.client.cookies['agent_session']
        self.client.cookies.set('agent_session', token[:-1] + ('a' if token[-1]!='a' else 'b'))
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        for _ in range(9):
            self.client.cookies.clear()
            self.client.post('/api/visitor', json={})
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/visitor', json={}).status_code, 429)


    def wait_for(self, predicate):
        for _ in range(150):
            if predicate():
                return
            time.sleep(.02)
        self.fail('Timed out waiting for background title')

    def test_auto_title_once_then_manual_rename_persists(self):
        headers = self.login()
        session = self.client.post('/api/sessions', json={}, headers=headers).json()['id']
        url = '/api/sessions/' + session
        self.model.release.set()
        with patch('agent_harness.web.summarize_title', return_value='广州周末出行计划') as summarize:
            self.client.post(url+'/messages', json={'text':'帮我安排广州周末出行'}, headers=headers)
            self.wait_for(lambda: self.client.get(url).json()['title'] == '广州周末出行计划')
            self.assertEqual(self.client.get('/api/sessions').json()[0]['title'], '广州周末出行计划')
            self.assertEqual(self.client.patch(url, json={'title':'  我的旅行计划  '}, headers=headers).json()['title'], '我的旅行计划')
            self.client.post(url+'/messages', json={'text':'继续聊'}, headers=headers)
            self.wait_for(lambda: not self.client.get(url).json()['busy'])
            self.assertEqual(self.client.get(url).json()['title'], '我的旅行计划')
            self.assertEqual(summarize.call_count, 1)
        with self.app.state.store.connect() as db:
            row = db.execute('SELECT title,title_mode FROM web_sessions WHERE id=?', (session,)).fetchone()
            self.assertEqual(tuple(row), ('我的旅行计划', 'manual'))

    def test_manual_title_wins_race_with_auto_generation(self):
        headers = self.login()
        session = self.client.post('/api/sessions', json={}, headers=headers).json()['id']
        url = '/api/sessions/' + session
        entered, release = threading.Event(), threading.Event()
        def title(*args):
            entered.set()
            release.wait(3)
            return '模型自动标题'
        self.model.release.set()
        with patch('agent_harness.web.summarize_title', side_effect=title):
            try:
                self.client.post(url+'/messages', json={'text':'计划旅行'}, headers=headers)
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.client.get(url).json()['busy'])
                self.assertEqual(self.client.patch(url, json={'title':'我自己起的标题'}, headers=headers).status_code, 200)
            finally:
                release.set()
            self.app.state.executor.shutdown(wait=True)
        self.assertEqual(self.client.get(url).json()['title'], '我自己起的标题')

    def test_title_failure_keeps_first_message_fallback(self):
        headers = self.login()
        session = self.client.post('/api/sessions', json={}, headers=headers).json()['id']
        url = '/api/sessions/' + session
        self.model.release.set()
        with patch('agent_harness.web.summarize_title', side_effect=RuntimeError('provider failure')):
            self.client.post(url+'/messages', json={'text':'广州明天会下雨吗'}, headers=headers)
            self.app.state.executor.shutdown(wait=True)
        state = self.client.get(url).json()
        self.assertEqual(state['title'], '广州明天会下雨吗')
        self.assertIn('网页已连接', state['messages'][-1]['content'])

    def test_rename_ownership_validation_and_csrf(self):
        headers = self.login()
        session = self.new(headers)
        url = '/api/sessions/' + session
        self.assertEqual(self.client.patch(url, json={'title':'x'}).status_code, 403)
        for title in ('', '   ', 'x'*61):
            self.assertEqual(self.client.patch(url, json={'title':title}, headers=headers).status_code, 422)
        with TestClient(self.app, base_url='http://localhost') as other:
            other_headers = self.login(other)
            self.assertEqual(other.patch(url, json={'title':'别人的标题'}, headers=other_headers).status_code, 404)
        self.assertEqual(self.client.get(url).json()['title'], '会话 A')

    def test_existing_database_title_migration(self):
        path = str(Path(self.temp.name)/'legacy.db')
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE web_sessions (user_id TEXT NOT NULL,id TEXT PRIMARY KEY,title TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)')
            db.executemany('INSERT INTO web_sessions VALUES(?,?,?,?,?)', [('u','a','对话 2',0,0), ('u','b','我的旅行',0,0)])
        migrated = create_app(model=self.model, db_path=path, secret='x'*40, secure=False)
        try:
            with migrated.state.store.connect() as db:
                rows = db.execute('SELECT id,title,title_mode FROM web_sessions ORDER BY id').fetchall()
                self.assertEqual([tuple(r) for r in rows], [('a','对话 2','pending'), ('b','我的旅行','manual')])
        finally:
            migrated.state.executor.shutdown(wait=True)
