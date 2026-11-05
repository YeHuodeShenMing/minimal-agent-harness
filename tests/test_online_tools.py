import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from agent_harness.config import load_env
from agent_harness.online_tools import OnlineToolError, request_json
from agent_harness.tools import default_registry


PLACE = {'results': [{'name': '广州', 'admin1': '广东', 'country': '中国',
                      'latitude': 23.1, 'longitude': 113.25}]}
FORECAST = {'timezone': 'Asia/Shanghai', 'current': {'time': '2026-11-05T15:00',
    'temperature_2m': 24.2, 'weather_code': 2}, 'current_units': {'temperature_2m': '°C'},
    'daily': {'time': ['2026-11-05', '2026-11-06'], 'weather_code': [2, 61],
              'temperature_2m_max': [26.2, 25.1], 'temperature_2m_min': [20.1, 19.3],
              'precipitation_probability_max': [10, 70]},
    'daily_units': {'temperature_2m_max': '°C', 'precipitation_probability_max': '%'}}


class OnlineToolsTests(unittest.TestCase):
    def setUp(self):
        self.registry = default_registry()

    @patch('agent_harness.online_tools.request_json', side_effect=[PLACE, FORECAST])
    def test_weather_units_location_date_and_attribution(self, request):
        data = self.registry.call('weather', {'city': '广州', 'country_code': 'cn'}, {})
        self.assertNotIn('mock', data)
        self.assertEqual(data['current']['temperature_2m'], 24.2)
        self.assertEqual(data['current']['condition'], '多云')
        self.assertEqual(data['forecast'][1]['precipitation_probability_max'], 70)
        self.assertEqual(data['timezone'], 'Asia/Shanghai')
        self.assertEqual(data['location']['admin1'], '广东')
        self.assertIn('open-meteo.com', data['source_url'])
        self.assertIn('geonames.org', data['location_source'])
        self.assertIn('countryCode=CN', request.call_args_list[0].args[0])
        self.assertIn('timezone=auto', request.call_args_list[1].args[0])
        self.assertLess(len(json.dumps(data).encode()), 12000)

    @patch('agent_harness.online_tools.request_json', side_effect=[PLACE, FORECAST])
    def test_specific_future_date_does_not_mislabel_current_weather(self, request):
        data = self.registry.call('weather', {'city': '广州', 'date': '2026-11-06'}, {})
        self.assertNotIn('current', data)
        self.assertEqual([x['date'] for x in data['forecast']], ['2026-11-06'])
        self.assertEqual(data['forecast'][0]['condition'], '小雨')

    def test_invalid_weather_input_and_missing_city(self):
        with patch('agent_harness.online_tools.request_json') as request:
            for args in ({'city': ' '}, {'city': '广州', 'date': '2026-02-30'},
                         {'city': '广州', 'date': 'tomorrow'}, {'city': '广州', 'country_code': '1x'}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    self.registry.call('weather', args, {})
            request.assert_not_called()
        with patch('agent_harness.online_tools.request_json', return_value={}):
            with self.assertRaisesRegex(ValueError, '没有找到'):
                self.registry.call('weather', {'city': '不存在的城市'}, {})
        with patch('agent_harness.online_tools.request_json', side_effect=[PLACE, FORECAST]):
            with self.assertRaisesRegex(ValueError, '当前支持'):
                self.registry.call('weather', {'city': '广州', 'date': '2027-01-01'}, {})

    @patch.dict(os.environ, {'TAVILY_API_KEY': ''})
    def test_search_requires_key_without_fake_fallback(self):
        with patch('agent_harness.online_tools.request_json') as request:
            with self.assertRaisesRegex(OnlineToolError, '尚未启用'):
                self.registry.call('search', {'query': 'agent'}, {})
            request.assert_not_called()

    @patch.dict(os.environ, {'TAVILY_API_KEY': 'test-secret'})
    @patch('agent_harness.online_tools.request_json')
    def test_search_sources_basic_cost_and_bounded_output(self, request):
        request.return_value = {'results': [
            {'title': 'Search', 'url': 'https://example.com/docs', 'content': 'Real snippet'},
            {'title': 'unsafe', 'url': 'javascript:alert(1)', 'content': 'ignored'},
            *[{'title': '长' * 500, 'url': 'https://example.com/中文', 'content': '文' * 5000} for _ in range(3)]]}
        data = self.registry.call('search', {'query': 'agent', 'time_range': 'week'}, {})
        self.assertEqual(data['results'][0]['snippet'], 'Real snippet')
        self.assertTrue(all(r['url'].startswith('https://') for r in data['results']))
        self.assertNotIn('mock', data)
        self.assertNotIn('test-secret', json.dumps(data))
        self.assertLess(len(json.dumps(data, ensure_ascii=False).encode()), 12000)
        payload = request.call_args.kwargs['payload']
        self.assertEqual(payload['search_depth'], 'basic')
        self.assertFalse(payload['auto_parameters'])
        self.assertFalse(payload['include_raw_content'])
        self.assertEqual(payload['time_range'], 'week')

    @patch.dict(os.environ, {'TAVILY_API_KEY': 'test-secret'})
    def test_empty_search_and_invalid_provider_data(self):
        with patch('agent_harness.online_tools.request_json', return_value={'results': []}):
            self.assertEqual(self.registry.call('search', {'query': 'not found'}, {})['results'], [])
        with patch('agent_harness.online_tools.request_json', return_value={}):
            with self.assertRaises(OnlineToolError):
                self.registry.call('search', {'query': 'agent'}, {})
        with patch('agent_harness.online_tools.request_json', side_effect=OnlineToolError('超时')):
            with self.assertRaisesRegex(OnlineToolError, '超时'):
                self.registry.call('weather', {'city': '广州'}, {})

    @patch('agent_harness.online_tools.urllib.request.build_opener')
    def test_http_errors_hide_upstream_secrets(self, build):
        for status in (401, 429, 500, 302):
            build.return_value.open.side_effect = urllib.error.HTTPError(
                'https://example.com?secret=hidden', status, 'private-body', {}, io.BytesIO(b'private-body'))
            with self.subTest(status=status), self.assertRaises(OnlineToolError) as raised:
                request_json('https://example.com', api_key='test-secret')
            self.assertNotIn('hidden', str(raised.exception))
            self.assertNotIn('private-body', str(raised.exception))
        build.return_value.open.side_effect = TimeoutError('private details')
        with self.assertRaisesRegex(OnlineToolError, '超时'):
            request_json('https://example.com')

    @patch('agent_harness.online_tools.urllib.request.build_opener')
    def test_transport_bounded_json_and_timeout(self, build):
        for content in (b'not-json', b'[]', b'x' * 512001):
            build.return_value.open.return_value = io.BytesIO(content)
            with self.assertRaises(OnlineToolError):
                request_json('https://example.com')
        build.return_value.open.return_value = io.BytesIO(b'{"ok":true}')
        self.assertEqual(request_json('https://example.com', payload={'query': 'test'}, api_key='secret'), {'ok': True})
        req = build.return_value.open.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'), 'Bearer secret')
        self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 15)

    @patch.dict(os.environ, {}, clear=True)
    def test_env_loads_search_key_without_overriding_existing(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / '.env'
            p.write_text('TAVILY_API_KEY="test-key"\nUNRELATED=ignored\n')
            load_env(p)
            self.assertEqual(os.environ['TAVILY_API_KEY'], 'test-key')
            self.assertNotIn('UNRELATED', os.environ)
            os.environ['TAVILY_API_KEY'] = 'deployment-key'
            load_env(p)
            self.assertEqual(os.environ['TAVILY_API_KEY'], 'deployment-key')


if __name__ == '__main__':
    unittest.main()
