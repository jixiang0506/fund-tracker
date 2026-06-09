# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice | self_reflection

---

## [LRN-20260602-001] best_practice

**Logged**: 2026-06-02T23:44:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
Edit 工具在文件有 BOM（utf-8-sig）时从 Read 输出复制的字符串无法精确匹配；Edit 多次执行同一处会产生重复代码；PowerShell `>>` 追写 UTF-8 文件会产生 BOM。应改用 Python 脚本直接读写。

### Details
- **旧方法**: 用 Edit 工具的 old_string/new_string 精确匹配
- **问题**: fetch_fund_data.py 有 BOM，`read_file` 输出与文件实际字节不一致，Edit 一直报 "String to replace not found"
- **新方法**: 写 Python 脚本（fix_all_issues.py），用 `open(..., 'r', encoding='utf-8-sig')` 读取，字符串替换后写回

### Suggested Action
当 Edit 工具连续失败时，立即换用 Python 脚本方案，不要反复尝试 Edit。

### Metadata
- Source: conversation
- Related Files: fetch_fund_data.py, push_to_github.py, logger_config.py
- Tags: edit-tool, bom, encoding, workaround
- Recurrence-Count: 3
- First-Seen: 2026-06-02

---

## [LRN-20260602-002] knowledge_gap

**Logged**: 2026-06-02T23:50:00+08:00
**Priority**: high
**Status**: promoted
**Area**: infra

### Summary
GitNexus MCP 工具在 WorkBuddy 沙箱中有多个相关问题：
1. `analyze` 报 `SANDBOX PERMISSION DENIED`（`~/.gitnexus` 被拦截）
2. `detect_changes()` 报 `spawnSync git ENOENT`（git 不在 Node.js spawn PATH 中）
3. Edit 工具对 BOM 文件不友好，反复失败

### Details
- **问题**: `npx gitnexus analyze` 报错 `EPERM: operation not permitted, open 'C:\Users\jixia\.gitnexus\registry.json'`
- **原因**: 沙箱默认阻止访问用户主目录，`~/.gitnexus/` 被拦截
- **解决方法**: 设置环境变量 `GITNEXUS_HOME=D:/projects/fund-tracker/.workbuddy/gitnexus-home`，将索引存储到项目目录
- **验证**: stdout 必须同时包含 `"Repository indexed successfully"` 且 exit code = 0

### Suggested Action
在 Windows + WorkBuddy 沙箱环境中，每次运行 `npx gitnexus analyze` 前先设置 `GITNEXUS_HOME` 到项目目录。

### Metadata
- Source: conversation
- Related Files: (none)
- Tags: gitnexus, sandbox, windows, workbuddy
- Recurrence-Count: 4
- First-Seen: 2026-06-02


------

## [LRN-20260602-004] self_reflection

**Logged**: 2026-06-03T00:02:00+08:00
**Priority**: low
**Status**: resolved
**Area**: backend

### Resolution
- **Resolved**: 2026-06-03
- **Notes**: 已记录教训：Edit 工具第一次失败立即换 Python 脚本；GitNexus 沙箱需设置 GITNEXUS_HOME

### Context
多步骤任务（≥8 tool calls）：修复 6 个代码问题（P0/P1/P2），涉及 4 个文件。

### Reflection
1. **Did it meet expectations?** 基本满足。6 个问题全部修复，验证通过，推送成功。但过程中 Edit 工具反复失败，浪费了较多轮次。
2. **What could be better?** 应该在 Edit 第一次失败时（而不是第 3 次后）就切换到 Python 脚本方案。fix-bug 技能要求"验证索引更新成功"，这一步在沙箱环境中需要特殊的 `GITNEXUS_HOME` 配置，应提前告知用户。
3. **Is this a pattern?** 是的。Edit 工具对 BOM 文件不友好，这是一个重复性风险。

### Lesson
- Edit 工具第一次失败 → 立即换 Python 脚本，不要反复尝试
- 沙箱中运行 GitNexus → 提前设置 `GITNEXUS_HOME` 到项目目录
- fix-bug 流程的"更新索引"步骤在沙箱中有特殊要求，应提前检查

### Metadata
- Source: self_reflection
- Recurrence-Count: 1
- First-Seen: 2026-06-02



------

## [LRN-20260603-003] correction

**Logged**: 2026-06-03T01:31:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
`logger_config.py` 的 `setup_encoding()` 中，stdout 已设置时直接 `return` 会导致 stderr 编码设置被跳过。

### Details
- **旧代码**:
  ```python
  if getattr(sys.stdout, '_encoding_setup_done', False):
      return
  # ... setup stdout ...
  if getattr(sys.stderr, '_encoding_setup_done', False):
      return
  # ... setup stderr ...
  ```
- **问题**: 如果 stdout 已设置，第一个 `return` 会直接退出函数，stderr 的设置永远不执行
- **修复后**: 改为 `pass` + `elif` 结构，stdout/stderr 各自独立判断

### Suggested Action
在"如果已设置则跳过"模式中，不要用 `return`，要用 `if/elif` 或 `if/pass` 结构，确保后续逻辑有机会执行。

### Metadata
- Source: code_review
- Related Files: logger_config.py
- Tags: early-return, encoding, stderr, bug
- Recurrence-Count: 1
- First-Seen: 2026-06-03

---

## [LRN-20260603-004] self_reflection

**Logged**: 2026-06-03T10:55:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Context
修复"业绩比较基准尾部暴跌到 -100%" BUG（15+ tool calls）：
- 从截图入手，识别出基准线尾端异常
- 深入分析 benchmark_index_data.json、funds_data.json、index.html 数据流
- 发现 benchmark_index_data.json 从不更新（静态文件），而 funds_data.json 每日更新
- 修复一：index.html 改用"向前填充"策略替代 return null
- 修复二：fetch_fund_data.py 新增 update_benchmark_index_data()，自动从东财 API 拉取指数数据

### Reflection

1. **Did it meet expectations?**
   基本满足。双管齐下修复了根因（数据管道缺失）和症状（前端 null 渲染）。但诊断过程效率偏低。

2. **What could be better?**
   - **诊断方向偏了**：前 50% 时间都在检查数据是否合法（有无 0 值、NaN、缺失），花了大量轮次做了"数据清洁度验证"，但根本问题不是数据脏，而是数据管道不完整
   - **应该先看数据流架构**：先回答"benchmark_index_data.json 从哪里来？"这个问题会立刻定位到"它是静态文件，从不更新"这个根因，跳过冗长的数据值分析
   - **数据管道意识不足**：当发现前端有数据，后端也有数据，但两者用不同脚本更新时，应该立即怀疑同步问题
   
3. **Is this a pattern?**
   是。之前修 fund-tracker 的 bug 时也有过类似情况：
   - 上次 QDII 显示 ¥0.00 问题：花了很长时间分析基金净值计算逻辑，最后根因是 GitHub Actions 双 job 竞态条件（第二个 job 读取了第一个还没写完的数据）
   - 这次的 benchmark 暴跌：花了很长时间检查数据值合法性，最后根因是数据管道缺失
   - **共同模式**：数据相关的 bug，先排查管道完整性，再排查数值计算

### Lesson
**数据管道排查优先于数据值排查**：
- 当遇到数据展示异常时，先回答两个问题：
  1. "数据从哪里来？"（哪个脚本/流程生成这个文件？）
  2. "数据多久更新一次？"（是否与其他数据文件的更新频率一致？）
- 如果发现数据管道不对称（一个文件每天更新，另一个是静态文件），**这就是根因**，无需继续分析数据值
- 只有管道确认完整后，才需要深入检查数据值是否合法（0、NaN、极端值等）

### Metadata
- Source: self_reflection
- Recurrence-Count: 1
- First-Seen: 2026-06-03
- Tags: data-pipeline, debugging, diagnostics, self-reflection

---

## [LRN-20260603-005] correction

**Logged**: 2026-06-03T11:07:00+08:00
**Priority**: low
**Status**: resolved
**Area**: config

### Summary
Edit 工具报 EBUSY（文件被锁定）后重试成功，但 diff 视图会显示两次相同的编辑记录，造成"重复编辑"的假象。

### Details
- **触发场景**：编辑 `MEMORY.md` 时第一次操作返回 `EBUSY: resource busy or locked`
- **操作**：Read 文件重新获取最新内容后，再次发起 Edit，第二次成功
- **问题**：WorkBuddy 的 diff UI 同时显示了两次 Edit 操作的记录（第一次失败的 + 第二次成功的），看起来像"两边编辑了同样的内容"
- **实际情况**：第一次 EBUSY 并没有实际修改文件，文件内容只有一份，无重复
- **对用户的影响**：用户看到 diff 视图中有两处相同的 `+7-0` 记录，误以为内容被重复写入了

### Suggested Action
- EBUSY 失败后，Read 确认文件状态再重试——这个流程是正确的
- 如果用户问"为什么编辑了两遍"，解释：第一次 EBUSY 实际没改文件，是 diff 视图同时显示了失败记录
- 如果可能，在 EBUSY 后先 Verify 文件内容确认无变化，再重试

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: edit-tool, ebusy, file-lock, diff-view, workbuddy
- Recurrence-Count: 1
- First-Seen: 2026-06-03

---

## [LRN-20260603-006] best_practice

**Logged**: 2026-06-03T15:45:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
后端字段重命名时，必须同步前端读取的字段名，否则前端用 `|| 0` 兜底会静默显示 0。

### Details
- **Bug 场景**: 后端 `fetch_fund_data.py` 将 `yesterday_profit_loss` 重命名为 `latest_trading_day_profit_loss`，但前端 `index.html` 仍然读取 `yesterday_profit_loss`
- **根因**: JavaScript `const x = summary.yesterday_profit_loss || 0` 对 undefined 静默返回 0，没有任何报错
- **修复**: 在后端加别名映射，同时输出新旧字段名
- **更好的做法**: 重命名时应前后端同步修改，或者在 API 层做兼容

### Suggested Action
修改字段名前先 grep 所有引用方（前后端都查），确保所有字段名同步更新。
如果无法同步（如不同步发布），在旧代码中加别名映射兜底。

### Metadata
- Source: conversation
- Related Files: fetch_fund_data.py, index.html
- Tags: field-rename, data-pipeline, silent-error, compatibility
- Recurrence-Count: 1
- First-Seen: 2026-06-03

---

## [LRN-20260603-008] correction

**Logged**: 2026-06-03T23:45:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
`loadPurchaseRecords()` 从 GitHub 加载数据后，只更新 `state.purchaseRecords`，没有写入 `localStorage`。用户刷新页面后数据丢失，需要重新从 GitHub 加载。

### Resolution
- **Resolved**: 2026-06-04T00:49:00+08:00
- **Commit**: pushed to github.com/jixiang0506/fund-tracker
- **Notes**: 已在 `index.html:4549-4554` 添加 `localStorage.setItem('purchase_records', ...)` + try/catch

### Details
- **位置**: `index.html:4536-4564`（`loadPurchaseRecords()` 函数）
- **问题**: `applyPurchaseRecords(JSON.parse(content))` 后没有 `localStorage.setItem('purchase_records', ...)`
- **对比**: `loadPurchaseRecordsFromFile()` (第2723行) 有 `localStorage.setItem('purchase_records', ...)`，但 `loadPurchaseRecords()` 遗漏了
- **修复**: 在 `applyPurchaseRecords(JSON.parse(content))` 后添加 `localStorage.setItem(...)` + try/catch

### Suggested Action
所有修改 `state.purchaseRecords` 的路径（GitHub 加载、文件加载、手动编辑）都应同步写入 `localStorage`，避免刷新后数据丢失。

### Metadata
- Source: screenshot_review
- Related Files: index.html
- Tags: localStorage, data-persistence, purchase-records
- Recurrence-Count: 1
- First-Seen: 2026-06-03

---

## [LRN-20260603-009] best_practice

**Logged**: 2026-06-03T23:50:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
`process_fund()` 实时 API 失败时回退使用陈旧数据（`prev_fund_map[code]`），但 `holdings` 是基于旧 `purchase_records` 计算的。如果用户已更新交易记录，回退数据会与实际情况不一致。

### Resolution
- **Resolved**: 2026-06-04T00:49:00+08:00
- **Commit**: pushed to github.com/jixiang0506/fund-tracker
- **Notes**: 已在 `fetch_fund_data.py:1058-1079` 添加 `copy.deepcopy()` + 用新交易记录重新计算 holdings

### Metadata

### Details
- **位置**: `fetch_fund_data.py:1057-1063`（`process_fund()` 回退逻辑）
- **问题**: `return (old_fund, old_fund["holdings"]["total_invested"], ...)` 直接返回整个旧 `fund_data`，包括旧的 `holdings`
- **后果**: 用户新增一笔买入后，如果实时 API 失败，回退数据会显示"未买入"状态
- **修复**: 回退时用新的 `purchase_records` 重新计算 `holdings`，保留旧的 `nav`（因为实时 API 失败，无法获取新 nav）

### Suggested Action
API 失败回退时，应区分"能重新计算的部分"（holdings、累计收益率）和"必须用旧数据的部分"（nav、实时涨跌幅）。不要直接返回整个旧对象。

### Metadata
- Source: screenshot_review
- Related Files: fetch_fund_data.py
- Tags: fallback-logic, data-freshness, process-fund
- Recurrence-Count: 1
- First-Seen: 2026-06-03

---

## [LRN-20260603-010] self_reflection

**Logged**: 2026-06-03T15:46:00+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Context
本会话完成了 2 个独立任务（共 ~30 tool calls）：
1. Bug 修复：昨日收益显示为 0（字段名不匹配 + 别名映射）
2. 新功能：平台切换时统计卡片跟随变化（新增 computePlatformSummary + 修改 filterByPlatform）
3. 2 次 GitHub 推送

### Reflection

1. **Did it meet expectations?**
   基本满足。两个任务都按 fix-bug 流程完整执行（影响分析→确认→修改→复查→变更检测→索引更新），修复正确、功能可用。

2. **What could be better?**
   - **第一个任务的 GitNexus impact 没命中**：`_compute_summary` 作为函数名没有在 GitNexus 索引中被识别。但因为这只是一个别名追加（不改逻辑），实际风险为 LOW，对结果无影响。但如果下次改真正的函数，应该先用 `codegraph_context` 或手动 grep 补上影响分析。
   - **fields 清单确认环节**：用户明确列出 8 个指标名称时，这个确认很清晰，避免了实现偏差。

3. **Is this a pattern?**
   - 每次多任务并行完成时，我应当将修复和新功能分开呈现给用户
   - fix-bug 技能适用于任何代码变更（不只是修复 bug），这个判断是对的

### Lesson
- `impact()` 找不到符号时，先用 `codegraph_context` 或 grep 手动分析，不要直接跳过
- 代码变更需求（不管是 bug 还是 feature）都可以用 fix-bug 流程做安全网关
- 用户明确列出期望清单时，按清单逐项核对，避免遗漏

### Metadata
- Source: self_reflection
- Recurrence-Count: 1
- First-Seen: 2026-06-03
- Tags: fix-bug, workflow, impact-analysis, self-reflection

---

## [LRN-20260604-001] correction

**Logged**: 2026-06-04T00:45:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary

`--skip-summary` 模式会备份旧 `summary` 并在跳过时恢复，导致 `summary` 与 `funds` 数据不一致。`summary` 应始终基于当前 `funds` 计算。

### Details

- **位置**: `fetch_fund_data.py:1157-1167`（备份）+ `:1398-1403`（恢复）
- **问题**: `all_data["summary"]` 被旧数据覆盖，`summary` 中的 totals 与 `funds` 中实际数据不匹配
- **后果**: 前端显示的总收益、总投入等指标与基金明细不一致
- **修复**: 删除 `old_summary` 备份和恢复逻辑，让 `summary` 始终由 `update_summary()` 基于当前 `funds` 计算

### Suggested Action

任何 `--skip-*` 参数都应只跳过耗时操作（如 API 调用），不应保留旧的计算结果。计算类函数（`update_summary`、`calculate_holdings`）应始终基于最新输入重新计算。

### Metadata

- Source: screenshot_review
- Related Files: fetch_fund_data.py
- Tags: skip-summary, data-consistency, summary
- Recurrence-Count: 1
- First-Seen: 2026-06-04

---


---

## [LRN-20260609-001] best_practice

**Logged**: 2026-06-09T20:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Edit 工具第一次 EBUSY（文件被锁定）后立即换用 Python 脚本，不要重试第二次。

### Details
- **旧方法**: 遇到 EBUSY 后重新 Read 再 Edit
- **问题**: Edit 工具对 BOM 文件不友好，反复失败浪费轮次
- **新方法**: 第一次 EBUSY 后，立即写脚本用 `open(..., 'r', encoding='utf-8-sig')` 读取并修改

### Suggested Action
Edit 工具第一次失败 → 立即换 Python 脚本（utf-8-sig 读取，utf-8 写回），不要反复尝试。

### Metadata
- Source: conversation
- Related Files: (any file that triggered EBUSY)
- Tags: edit-tool, ebussy, bom, encoding, workaround
- Recurrence-Count: 2
- First-Seen: 2026-06-02

---

## [LRN-20260609-002] best_practice

**Logged**: 2026-06-09T20:05:00+08:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
临时推送脚本必须在开头加载 `.env` 文件，否则 GITHUB_TOKEN 为空会导致 401 错误。

### Details
- **问题**: `push_cleanup.py` 没有调用 `load_env_file()`，导致 `os.environ.get('GITHUB_TOKEN')` 返回空字符串
- **后果**: GitHub API 返回 401 Bad credentials
- **修复**: 在脚本开头显式加载 `.env`：
  ```python
  def load_env_file():
      env_path = os.path.join(os.path.dirname(__file__), '.env')
      if os.path.exists(env_path):
          with open(env_path, 'r', encoding='utf-8') as f:
              for line in f:
                  line = line.strip()
                  if line and not line.startswith('#') and '=' in line:
                      k, v = line.split('=', 1)
                      os.environ[k.strip()] = v.strip()
  load_env_file()
  token = os.environ.get('GITHUB_TOKEN', '')
  if not token:
      raise RuntimeError("GITHUB_TOKEN not found in .env")
  ```

### Suggested Action
任何需要 Token/密钥的临时脚本，开头必须加载 `.env` 或直接用 `push_to_github.py`（已内置加载逻辑）。

### Metadata
- Source: conversation
- Related Files: push_to_github.py, .env
- Tags: github-token, env-file, 401-error, push-script
- Recurrence-Count: 1
- First-Seen: 2026-06-09

---

## [LRN-20260609-003] best_practice

**Logged**: 2026-06-09T20:10:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
Windows 控制台默认编码是 GBK (cp936)，跨平台脚本中不在 stdout 打印 emoji（U+2705 等），否则会触发 UnicodeEncodeError。

### Details
- **问题**: `print("[OK] 推送完成！")` 在 Windows 控制台输出正常，但 `print("✅ 推送完成！")` 报 `UnicodeEncodeError: 'gbk' codec can't encode character`
- **原因**: Windows `sys.stdout.encoding` 通常是 `cp936` (GBK)，不支持 emoji 字符
- **修复**: 跨平台脚本用纯文本输出：
  - ✅ `print("[OK] 推送完成")`
  - ✅ `print("[ERROR] 推送失败")`
  - ❌ `print("✅ 推送完成")`

### Suggested Action
跨平台脚本的 stdout 输出只用 ASCII 方括号标记（[OK]、[ERROR]、[WARN]），不打印 emoji。

### Metadata
- Source: conversation
- Related Files: push_cleanup.py
- Tags: windows, gbk, emoji, unicode-error, cross-platform
- Recurrence-Count: 1
- First-Seen: 2026-06-09

---

## [LRN-20260610-001] correction

**Logged**: 2026-06-10T01:10:00+08:00
**Priority**: medium
**Status**: pending
**Area**: workflow

### Summary
截图静态分析可能不准确，不能直接作为"该文件可删除"的依据，必须实际 Grep 验证引用。

### Details
- **触发场景**: 截图分析说 `validate_records.py` "未被引用"，建议删除
- **实际情况**: Grep 搜索发现 `.github/workflows/update.yml:56` 明确调用 `python validate_records.py`
- **后果**: 如果直接按截图建议删除，CI 校验环节会断裂
- **根本原因**: 静态分析工具可能只扫描 `*.py` 文件，遗漏 `.yml` 中的脚本调用

### Suggested Action
在评估"某文件是否可删除"时：
1. 先用 Grep 搜索文件名（不限文件类型），确认项目中无任何引用
2. 特别检查 `.github/workflows/*.yml` 中的脚本调用
3. 截图分析作为**线索**，不作为**结论**

### Metadata
- Source: screenshot_review + grep_verification
- Related Files: validate_records.py, .github/workflows/update.yml
- Tags: screenshot-analysis, grep-verify, false-positive, ci-safety
- Recurrence-Count: 1
- First-Seen: 2026-06-10

---

## [LRN-20260610-002] best_practice

**Logged**: 2026-06-10T01:12:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
删除重复/废弃脚本时，需同步更新 `github_config.json` 的 `files` 列表，否则 `push_to_github.py` 会尝试推送不存在的文件导致 404 错误。

### Details
- **问题**: 删除 `fetch_benchmark_data.py` 后，`github_config.json` 中仍包含该文件名
- **后果**: `push_to_github.py` 读取 `files` 列表，对不存在的文件调用 GitHub API PUT，返回 404
- **修复**: 删除脚本后，立即用 Python 脚本同步更新 `github_config.json`

### Suggested Action
任何文件删除操作（尤其是 `github_config.json` 中列出的文件）后，必须同步更新 `github_config.json` 的 `files` 列表。

### Metadata
- Source: conversation
- Related Files: github_config.json, push_to_github.py
- Tags: file-deletion, github-config, sync-config
- Recurrence-Count: 1
- First-Seen: 2026-06-10

---
