import unittest
from unittest.mock import Mock
from agent_harness.titles import summarize_title, fallback_title

class TitleTests(unittest.TestCase):
    def test_title_is_bounded_data_only_and_tool_free(self):
        model = Mock()
        model.complete.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': '「广州天气与出行」'}}]}
        self.assertEqual(summarize_title(model, '查广州天气', '明天有雨'), '广州天气与出行')
        messages, tools = model.complete.call_args.args
        self.assertEqual(tools, [])
        self.assertIn('不可信', messages[0]['content'])
        self.assertIn('查广州天气', messages[1]['content'])
        for title in ('x'*25, '第一行\n第二行'):
            model.complete.return_value['choices'][0]['message']['content'] = title
            with self.assertRaises(ValueError):
                summarize_title(model, 'test', 'answer')
        self.assertEqual(fallback_title('  广州\n 天气  '), '广州 天气')
        self.assertLessEqual(len(fallback_title('x'*100)), 25)
