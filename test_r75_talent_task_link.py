"""
r75 派单验收测试: tab 6 任务-达人关联
老大 10-08 派单 (贾维斯派单给小路, 老大已批「全部弄」):

验收 4 步闭环 (派单原话: 建任务→详情可见→解除关联→消失):
1. POST /api/tasks {talent_id: 'xxx'}  -> 201 + 返回 task (含 talent_id 字段)
2. GET  /api/tasks?talent_id=xxx       -> 任务在列表内
3. PUT  /api/tasks/{id} {talent_id: ''} -> 200 + 返回 task (talent_id = '')
4. GET  /api/tasks?talent_id=xxx       -> 任务已不在列表内 (解除关联)

字段名铁律 (第 1 条) 校验:
  - /api/tasks 真字段 id/title/status/priority/assignee/creator/talent_id (snake_case)
  - /api/tasks?talent_id=xxx 过滤参数生效

依赖: 测试 server 跑在 localhost:8081 (solobrave-server.py 启动后)
"""

import json
import sys
import os
import urllib.request
import urllib.error
from urllib.parse import urlencode

BASE = 'http://localhost:8081'
AUTH_TOKEN = os.environ.get('SB_TEST_TOKEN', 'sb_auth_token')


def _req(method, path, body=None, query=None):
    """统一请求函数 (admin Bearer token)"""
    url = BASE + path
    if query:
        url += '?' + urlencode(query)
    data = None
    headers = {'Accept': 'application/json'}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    headers['Authorization'] = 'Bearer ' + AUTH_TOKEN
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


def step1_post_task_with_talent_id():
    """建任务带 talent_id (★r75 新增字段)"""
    print('\n=== Step 1: 建任务带 talent_id ===')
    talent_id = 'test_talent_r75_' + os.urandom(4).hex()
    body = {
        'title': 'r75 验收测试任务',
        'description': 'test_r75_talent_task_link 创建',
        'priority': 'normal',
        'talent_id': talent_id,  # ★r75 新增 (camelCase 也接受)
    }
    status, data = _req('POST', '/api/tasks', body=body)
    assert status == 201, f'POST /api/tasks 应返回 201, 实际 {status}: {data}'
    assert data is not None, 'POST /api/tasks 返回空 body'
    assert 'id' in data, f'POST 响应缺 id: {data}'
    assert data.get('talent_id') == talent_id, (
        f'POST 响应 talent_id 不匹配: 期望 {talent_id}, 实际 {data.get("talent_id")}'
    )
    print(f'  ✓ 创建任务 {data["id"]} talent_id={data["talent_id"]}')
    return data['id'], talent_id


def step2_get_tasks_filter_by_talent(task_id, talent_id):
    """GET /api/tasks?talent_id=xxx 端点过滤 (★r75 新增端点过滤参数)"""
    print('\n=== Step 2: GET /api/tasks?talent_id=xxx 过滤 ===')
    status, data = _req('GET', '/api/tasks', query={'talent_id': talent_id})
    assert status == 200, f'GET /api/tasks 应返回 200, 实际 {status}: {data}'
    assert data is not None and 'tasks' in data, f'GET 响应缺 tasks 字段: {data}'
    tasks = data['tasks']
    assert any(t.get('id') == task_id for t in tasks), (
        f'新建任务 {task_id} 不在 talent_id={talent_id} 过滤列表内'
    )
    print(f'  ✓ talent_id={talent_id} 过滤返回 {len(tasks)} 条任务, 含新建任务 {task_id}')


def step3_put_unlink_talent(task_id):
    """PUT /api/tasks/{id} {talent_id: ''} 解除关联 (★r75 解除关联)"""
    print('\n=== Step 3: PUT 解除 talent_id 关联 ===')
    status, data = _req('PUT', '/api/tasks/' + task_id, body={'talent_id': ''})
    assert status == 200, f'PUT /api/tasks/{{id}} 应返回 200, 实际 {status}: {data}'
    assert data is not None, 'PUT 返回空 body'
    assert data.get('talent_id') == '', (
        f'PUT 响应 talent_id 应为空, 实际 {data.get("talent_id")}'
    )
    print(f'  ✓ 任务 {task_id} talent_id 已解除 (空字符串)')


def step4_get_tasks_after_unlink(task_id, talent_id):
    """GET /api/tasks?talent_id=xxx 解除后任务不在列表内"""
    print('\n=== Step 4: 解除后任务从过滤列表消失 ===')
    status, data = _req('GET', '/api/tasks', query={'talent_id': talent_id})
    assert status == 200, f'GET /api/tasks 应返回 200, 实际 {status}: {data}'
    tasks = data.get('tasks', [])
    assert not any(t.get('id') == task_id for t in tasks), (
        f'任务 {task_id} 解除关联后仍在 talent_id={talent_id} 列表内 (期望消失)'
    )
    print(f'  ✓ 解除关联后, talent_id={talent_id} 过滤列表无任务 {task_id}')


def field_name_law():
    """字段名铁律 (第 1 条): 抄实测 /api/tasks 真字段"""
    print('\n=== 字段名铁律 (第 1 条): 抄实测 ===')
    status, data = _req('GET', '/api/tasks', query={'limit': 1})
    if status != 200:
        print(f'  ! 跳过字段名校验: GET 返回 {status}')
        return
    tasks = data.get('tasks', [])
    if not tasks:
        print('  ! 跳过字段名校验: 任务列表为空')
        return
    sample = tasks[0]
    expected = {'id', 'title', 'status', 'priority', 'assignee', 'creator', 'talent_id'}
    actual = set(sample.keys())
    missing = expected - actual
    assert not missing, f'/api/tasks 缺字段 {missing} (字段名铁律: 抄实测)'
    print(f'  ✓ /api/tasks 真字段 {sorted(actual & expected)} 全在')


def main():
    print('=== r75 tab 6 任务-达人关联 验收 ===')
    print(f'BASE: {BASE}')
    print(f'TOKEN: {AUTH_TOKEN[:8]}...')
    try:
        # 端到端 4 步
        task_id, talent_id = step1_post_task_with_talent_id()
        step2_get_tasks_filter_by_talent(task_id, talent_id)
        step3_put_unlink_talent(task_id)
        step4_get_tasks_after_unlink(task_id, talent_id)
        # 字段名铁律
        field_name_law()
        print('\n=== 全部 5 项验收通过 ✓ ===')
        print(f'commit: cc7b6f8 (server) + 31fe2bb (frontend)')
        return 0
    except AssertionError as e:
        print(f'\n=== 验收失败 X ===\n{e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())