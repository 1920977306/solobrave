# SoloBrave 开发与合并流程（交接文档）

> 2026-10-09 立（老大批准）。任何会话（人/AI）改这套系统，照此执行。

## 铁律

- **铁律 16**：本地 prod（`/Users/qichen/solobrave-prod`，分支 `fix/request-level-model`）是唯一事实源。
  GitHub 克隆只走 PR，**禁止在克隆上开发/验证**。
- **prod 只 ff**：所有改动在 worktree 开发，验证通过后快进合并进 prod，不做 merge commit。
- **回归不过不合**（门禁铁律）：`scripts/ff_to_prod.sh` 会自动把关，别绕过。

## 场地

| 环境 | 地址 | 目录 | 用途 |
|---|---|---|---|
| dev | http://127.0.0.1:18210 | `/Users/qichen/sb-dev/side-restore`（worktree） | 开发 + 验证，改完磁盘文件即生效 |
| prod | https://192.168.1.25:8443 | `/Users/qichen/solobrave-prod` | 老大真机，只收 ff 合并 |

## 标准流程

```bash
cd /Users/qichen/sb-dev/side-restore

# 1. 开发（改 index.html / js/inline-*.js / solobrave-server.py）

# 2. 前端改动必须 bump 钉扎（破浏览器缓存）:
#    index.html 里对应 <script src="js/inline-XX.js?v=...> 换个新值, 命名带日期
grep -n 'inline-0[379].js?v=' index.html

# 3. 语法/结构快检
node --check js/inline-XX.js
python3 -c "import re;h=open('index.html').read();print(len(re.findall(r'<div\\b',h)),len(re.findall(r'</div>',h)))"

# 4. 服务管理（不要手动 nohup, 不会起重复份）
scripts/dev_server.sh status
scripts/dev_server.sh restart dev     # server 端代码改动后必须重启才生效; 前端钉扎改了刷新即可

# 5. 验证（playwright 打 18210, 断言 + pageerror 计数; 参考 tests/e2e_r84_regression.py 的写法）
python3 tests/e2e_r84_regression.py   # 现有回归, 新功能往这里加断言

# 6. 过门禁合并（先跑回归, 全过才 变基+ff; DRY_RUN=1 只验不合）
scripts/ff_to_prod.sh

# 7. prod 生效
scripts/dev_server.sh restart prod    # server 端改动; 前端刷新即可
git -C /Users/qichen/solobrave-prod push origin fix/request-level-model
```

## 工具链

| 脚本 | 作用 |
|---|---|
| `scripts/ff_to_prod.sh` | 合并门禁：回归(18断言)全过 → 变基到 prod 最新 → ff 合并；挂则中止 |
| `scripts/dev_server.sh` | 18210/8443 的 status/start/stop/restart，端口占用 + 目录核对，幂等 |
| `tests/e2e_r84_regression.py` | r84 回归：A 块签到 owner 口径（API）+ B 块六注 UI（playwright）。`BASE=`/`VERIFY_TLS=-k`/`TEST_USER/`TEST_PASS` 环境变量可切目标 |

## 口径备忘

- **签到 = 客户级**（r84b）：任一员工签过即整户已签，重复签拒不加钱；钱包落账仍按员工（消费计量同 keying）。服务端 `_get_checkin_owner_agent_ids` / `_owner_checkin_today`。
- **钉扎命名**：`语义+版本+日期`，如 `r84batch1a20261009`；改 inline 文件必 bump。
- **并发**：prod 会被并行会话推进，ff 前以 `git -C /Users/qichen/solobrave-prod rev-parse HEAD` 为准；门禁脚本已内置变基。
- **测试夹具**：回归用例会建「r84回归验证组」，无害，不用清。

## 踩过的坑（别再踩）

- `lsof` 查空端口在 `set -o pipefail` 下会假死退出 —— 脚本里已 `|| true`。
- `(nohup cmd &)` 的 `$!` 是子 shell pid，不是服务 pid —— 按端口 kill，别按 `$!`。
- index.html 里同 class 的 CSS 后定义者赢 —— head 里加覆盖要用提权选择器（后代/id）。
- 多员工时签到/余额都按员工钱包 keying —— 客户视角的展示要做 owner 级聚合。
