"""Discover the independent H5 service without importing its Frida runtime."""
from functools import lru_cache
import json
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import urlencode
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ID = 'dashboard-' + uuid.uuid4().hex
STATE_PATH = Path('/tmp/wechat_rea/h5_status.json')


def _get_json(url):
    with urllib.request.urlopen(url, timeout=0.7) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ValueError('H5 service response is too large')
    return json.loads(body)


def snapshot(port=8780):
    result = {'online': False, 'port': port, 'targets': [], 'reason': '',
              'version': None, 'renderers': {}}
    origin = f'http://127.0.0.1:{port}'
    try:
        health = _get_json(origin + '/health')
        if (not isinstance(health, dict) or health.get('service') != 'wechat-h5-chii'
                or health.get('chiiVersion') != '1.15.5'):
            raise ValueError('Unexpected H5 service identity/version')
        rows = _get_json(origin + '/targets')['targets']
        if not isinstance(rows, list):
            raise ValueError('Invalid H5 target list')
        for row in rows:
            if not isinstance(row, dict):
                continue
            target_id = row.get('id', '')
            if not isinstance(target_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', target_id):
                continue
            url = row.get('url', '')
            if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                continue
            # Build local URLs ourselves; target metadata cannot choose the iframe origin.
            endpoint = f'127.0.0.1:{port}/client/{CLIENT_ID}?target={target_id}'
            result['targets'].append({
                'appid': 'h5:' + target_id, 'targetId': target_id,
                'kind': 'web', 'transport': 'chii',
                'connection_id': str(health.get('pid', '')) + ':' + str(row.get('connectionId', ''))[:128],
                'title': str(row.get('title') or '网页')[:1024], 'url': url[:8192],
                'devtools_url': origin + '/front_end/chii_app.html?' + urlencode({'ws': endpoint}),
            })
        result.update(online=True, version=health['chiiVersion'])
        try:
            state = json.loads(STATE_PATH.read_text())
            if isinstance(state, dict) and state.get('port') == port and time.time() - state.get('updated_at', 0) < 30:
                result['renderers'] = state.get('renderers', {})
                result['reason'] = '; '.join(str(v) for v in state.get('errors', {}).values())[:2000]
        except (OSError, ValueError, TypeError):
            pass
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result['reason'] = '网页服务未连接 · ' + type(exc).__name__
    return result


@lru_cache(maxsize=4)
def launch_config():
    """All services use the launcher's Python environment."""
    if not shutil.which('node'):
        return None, '缺少 Node.js 18+'
    if not (ROOT / 'node_modules/chii/package.json').is_file():
        return None, '缺少网页依赖，请运行 npm ci --ignore-scripts'
    try:
        import frida
        import psutil
        if not frida.__version__.startswith('17.'):
            raise ValueError('Frida 17 is required')
    except (ImportError, ValueError):
        return None, '请在当前 Python 环境运行 python -m pip install -r requirements.txt'
    return sys.executable, ''
