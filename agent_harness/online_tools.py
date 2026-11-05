"""Live tools using fixed HTTPS providers; credentials never enter tool results."""
import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone


class OnlineToolError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, *, payload=None, api_key=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'MinimalAgent/0.1'}
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode()
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(512_001)
        if len(raw) > 512_000:
            raise OnlineToolError('接口响应过大，请缩小查询范围')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            message = '接口认证失败，请检查服务端 API 密钥或访问权限'
        elif status in (429, 432, 433):
            message = '接口调用额度已用完或请求过快，请稍后重试'
        else:
            message = f'接口暂时不可用（HTTP {status}）'
        raise OnlineToolError(message) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise OnlineToolError('接口连接失败或超时，请稍后重试') from None
    except (ValueError, UnicodeError):
        raise OnlineToolError('接口返回了无效数据，请稍后重试') from None


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def text(value, length):
    return value[:length] if isinstance(value, str) else ''


def public_url(value):
    if not isinstance(value, str) or len(value) > 1500:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def search(args, state):
    query = args['query'].strip()
    if not query:
        raise ValueError('请输入搜索内容')
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    if not key:
        raise OnlineToolError('联网搜索尚未启用：请在服务端配置 TAVILY_API_KEY（app.tavily.com 可免费注册）')
    payload = {'query': query, 'search_depth': 'basic', 'max_results': 5,
               'topic': 'general', 'include_answer': False, 'include_raw_content': False,
               'include_images': False, 'auto_parameters': False}
    if args.get('time_range'):
        payload['time_range'] = args['time_range']
    data = request_json('https://api.tavily.com/search', payload=payload, api_key=key)
    rows = data.get('results')
    if not isinstance(rows, list):
        raise OnlineToolError('搜索接口返回了无效结果')
    result = {'provider': 'Tavily', 'query': query, 'retrieved_at': timestamp(), 'results': []}
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        url = public_url(row.get('url'))
        if not url:
            continue
        item = {'title': text(row.get('title'), 160), 'url': url,
                'snippet': text(row.get('content'), 600)}
        if row.get('published_date'):
            item['published_date'] = text(row['published_date'], 40)
        # Keep the complete tool response below the runtime's 12 KB budget.
        candidate = {**result, 'results': result['results'] + [item]}
        if len(json.dumps(candidate, ensure_ascii=False).encode()) <= 10_500:
            result['results'].append(item)
    if rows and not result['results']:
        raise OnlineToolError('搜索接口没有返回可用的网页链接')
    return result


CONDITIONS = {
    0: '晴', 1: '大部晴朗', 2: '多云', 3: '阴', 45: '雾', 48: '雾凇',
    51: '小毛毛雨', 53: '中毛毛雨', 55: '大毛毛雨', 56: '轻微冻毛毛雨', 57: '较强冻毛毛雨',
    61: '小雨', 63: '中雨', 65: '大雨', 66: '轻微冻雨', 67: '较强冻雨',
    71: '小雪', 73: '中雪', 75: '大雪', 77: '雪粒',
    80: '小阵雨', 81: '中阵雨', 82: '强阵雨', 85: '小阵雪', 86: '强阵雪',
    95: '雷雨', 96: '雷雨伴小冰雹', 99: '雷雨伴大冰雹',
}


def weather(args, state):
    city = args['city'].strip()
    if not city:
        raise ValueError('请输入城市名')
    wanted = args.get('date')
    if wanted:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', wanted):
                raise ValueError()
            date.fromisoformat(wanted)
        except ValueError:
            raise ValueError('日期须为 YYYY-MM-DD') from None
    params = {'name': city, 'count': 3, 'language': 'zh', 'format': 'json'}
    if args.get('country_code'):
        country = args['country_code'].upper()
        if not re.fullmatch('[A-Z]{2}', country):
            raise ValueError('country_code 须为两位国家代码，如 CN、US')
        params['countryCode'] = country
    places = request_json('https://geocoding-api.open-meteo.com/v1/search?' + urllib.parse.urlencode(params))
    matches = places.get('results', [])
    if not isinstance(matches, list):
        raise OnlineToolError('城市接口返回了无效数据')
    if not matches:
        raise ValueError('没有找到该城市，请提供城市英文名或“城市, 省/国家”')
    location = matches[0]
    if not isinstance(location, dict) or not all(isinstance(location.get(k), (int, float)) for k in ('latitude', 'longitude')):
        raise OnlineToolError('城市接口返回了无效坐标')
    params = {'latitude': location['latitude'], 'longitude': location['longitude'],
              'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m',
              'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
              'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh', 'timezone': 'auto', 'forecast_days': 7}
    forecast_url = 'https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(params)
    data = request_json(forecast_url)
    current, daily = data.get('current'), data.get('daily')
    if not isinstance(current, dict) or not isinstance(daily, dict) or not current.get('time'):
        raise OnlineToolError('天气接口返回了无效数据')
    days = daily.get('time')
    if not isinstance(days, list) or not days:
        raise OnlineToolError('天气接口缺少预报日期')
    if wanted and wanted not in days:
        raise ValueError(f'当前支持 {days[0]} 至 {days[-1]} 的天气预报，请在此范围内选择日期')
    result = {'provider': 'Open-Meteo', 'source_url': 'https://open-meteo.com/',
              'location_source': 'https://www.geonames.org/', 'retrieved_at': timestamp(),
              'location': {k: location.get(k) for k in ('name', 'admin1', 'country', 'latitude', 'longitude')},
              'timezone': data.get('timezone'), 'forecast': [],
              'data_note': '来自天气模型的当前天气估算及预报，并非现场观测；请展示匹配城市、当地日期和来源。'}
    if not wanted or wanted == days[0]:
        result['current'] = {**current, 'condition': CONDITIONS.get(current.get('weather_code'), '未知'),
                             'units': data.get('current_units', {})}
    for i, day in enumerate(days):
        if wanted and day != wanted:
            continue
        item = {'date': day}
        for field in ('weather_code', 'temperature_2m_max', 'temperature_2m_min', 'precipitation_probability_max'):
            values = daily.get(field)
            if not isinstance(values, list) or len(values) != len(days):
                raise OnlineToolError('天气接口返回了不完整的逐日预报')
            item[field] = values[i]
        item['condition'] = CONDITIONS.get(item['weather_code'], '未知')
        result['forecast'].append(item)
    result['forecast_units'] = data.get('daily_units', {})
    return result
