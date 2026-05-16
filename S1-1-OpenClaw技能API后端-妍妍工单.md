# S1-1: OpenClaw技能API后端

## 背景
SoloBrave需要对接OpenClaw的技能系统。当前员工详情面板的"技能"Tab只是手动添加标签（emoji+名字+等级），没有实际功能。我们要在Python后端添加技能管理API，让前端可以搜索、安装、卸载OpenClaw技能。

## OpenClaw技能CLI参考
有两套CLI可用：

**1. openclaw skill（内置CLI）**
```bash
openclaw skill install <name>       # 安装技能
openclaw skill install <name>@1.2.0 # 安装指定版本
openclaw skill install --path ./local-skill  # 安装本地技能
```

**2. clawhub（社区注册表CLI，推荐用于搜索和安装）**
```bash
clawhub search <keyword>     # 搜索技能
clawhub install <slug>       # 安装技能
clawhub inspect <slug>       # 查看技能详情
clawhub list                 # 列出已安装技能
clawhub uninstall <slug> --yes  # 卸载技能
clawhub update --all         # 更新所有技能
```

技能安装到 `./skills/<slug>` 目录或 `~/.openclaw/skills/` 目录。

## 修改文件
`solobrave-server.py`（约2260行）

## 需要添加的4个API

### 1. GET /api/openclaw/skills/list
列出已安装的技能。

实现方式：
```python
def _handle_openclaw_list_skills(self):
    """GET /api/openclaw/skills/list"""
    # 用 _run_openclaw 执行 clawhub list
    # 如果clawhub不可用，尝试读取 ~/.openclaw/skills/ 目录
    # 返回格式: { "skills": [{ "name": "xxx", "slug": "xxx", "version": "1.0.0", "description": "xxx" }] }
```

备选实现：如果 `clawhub list` 不可用，直接扫描 `~/.openclaw/skills/` 目录，读取每个子目录下的 `SKILL.md` 文件来获取技能信息。

### 2. GET /api/openclaw/skills/search?q=<keyword>
搜索ClawHub技能注册表。

实现方式：
```python
def _handle_openclaw_search_skills(self):
    """GET /api/openclaw/skills/search?q=xxx"""
    query = params.get('q', '')
    # 执行 clawhub search <query>
    # 解析输出返回结果列表
    # 返回格式: { "results": [{ "slug": "xxx", "name": "xxx", "description": "xxx", "author": "xxx" }] }
```

### 3. POST /api/openclaw/skills/install
安装一个技能。

请求body：
```json
{ "slug": "weather-now" }
```

实现方式：
```python
def _handle_openclaw_install_skill(self):
    """POST /api/openclaw/skills/install"""
    data = self._read_json()
    slug = data.get('slug', '')
    # 执行 clawhub install <slug>
    # 或 openclaw skill install <slug>
    # 返回: { "success": true, "message": "技能安装成功" }
```

### 4. POST /api/openclaw/skills/remove
卸载一个技能。

请求body：
```json
{ "slug": "weather-now" }
```

实现方式：
```python
def _handle_openclaw_remove_skill(self):
    """POST /api/openclaw/skills/remove"""
    data = self._read_json()
    slug = data.get('slug', '')
    # 执行 clawhub uninstall <slug> --yes
    # 返回: { "success": true, "message": "技能已卸载" }
```

## 路由注册
在 `do_GET` 和 `do_POST` 方法中添加路由匹配，参考现有的 `/api/openclaw/agents` 模式：

```python
# do_GET 中添加:
if path == '/api/openclaw/skills/list':
    self._handle_openclaw_list_skills()
    return
if path.startswith('/api/openclaw/skills/search'):
    self._handle_openclaw_search_skills()
    return

# do_POST 中添加:
if path == '/api/openclaw/skills/install':
    self._handle_openclaw_install_skill()
    return
if path == '/api/openclaw/skills/remove':
    self._handle_openclaw_remove_skill()
    return
```

## 参考代码模式
完全照搬现有的 `_handle_openclaw_list_agents` 模式：
```python
def _handle_openclaw_list_agents(self):
    success, stdout, stderr, rc = _run_openclaw(['agents', 'list', '--json'])
    if not success:
        self._send_json(200, { 'agents': [], 'warning': stderr })
        return
    # 解析输出...
```

## 错误处理
- CLI不存在时返回 `warning` 字段，不报错
- CLI超时（30秒）时返回友好提示
- JSON解析失败时返回空数组

## 约束
- 不要修改 `_run_openclaw` 函数
- 不要修改其他API端点
- CLAWHUB_CLI路径先硬编码为 `/opt/homebrew/bin/clawhub`，和 `OPENCLAW_CLI` 一样的模式
- 如果clawhub不可用，list接口回退到扫描 `~/.openclaw/skills/` 目录

## 验证
完成后提交，commit message格式：`feat: OpenClaw技能管理API — list/search/install/remove`
