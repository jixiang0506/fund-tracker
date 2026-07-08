# Errors

Command failures and integration errors.


------

---

## [ERR-20260601-002] fix-bug skill: skipped index verification

**Logged**: 2026-06-01T01:15:00+08:00
**Priority**: high
**Status**: resolved
**Area**: workflow

### Summary
Ran `npx gitnexus analyze` as step 5 of fix-bug skill, but did NOT check exit code or stdout. Incorrectly reported "索引已更新" to user when the command had actually failed.

### Error
```
Command ran: cd /d/projects/fund-tracker && npx gitnexus analyze 2>&1 | tail -5
Exit code: NOT CHECKED (violation of Section 5)
stdout checked: NO (violation of Section 5)
Reported to user: "索引已更新" (incorrect)
```

### Context
- Skill: fix-bug (step 5: update index)
- The command actually failed with SANDBOX PERMISSION DENIED on first attempt
- Did not activate self-improvement after detecting the failure
- User had to ask "有没有按skill的流程更新索引？" to trigger the reflection

### Suggested Fix
1. After EVERY command in a skill workflow, MUST check exit code and stdout
2. If verification is skipped, this is a violation → activate self-improvement
3. Never report "success" without verifying

### Metadata
- Reproducible: no (command succeeded on retry, but verification was still skipped)
- Related Files: C:/Users/jixia/.workbuddy/skills/fix-bug/SKILL.md
- See Also: ERR-20260601-001, LRN-20260601-002

---

## [ERR-20260601-003] Tool unavailable: silently downgraded without telling user

**Logged**: 2026-06-01T01:18:00+08:00
**Priority**: high
**Status**: resolved
**Area**: transparency

### Summary
GitNexus MCP was not configured (mcp.json had wrong paths). Instead of telling the user, silently downgraded to manual analysis. Did not mention the tool unavailability at all.

### Error
```
Tool: GitNexus MCP
Status: not configured (mcp.json paths pointed to old session temp dir)
Action taken: silently used manual grep/read instead
Told user: NO (violation of transparency rule)
```

### Context
- User asked to use fix-bug skill (which expects GitNexus MCP for impact analysis)
- MCP server was misconfigured but this was not detected until user asked
- Silently downgraded = user did not get the benefit of the tool, and was not given the choice
- User had to ask "为什么不跟我说就自己直接降级处理了？" to trigger the reflection

### Suggested Fix
1. If a required tool is unavailable → tell user IMMEDIATELY, before any downgrade
2. Explain what capability is missing and what the downgrade means
3. Ask user if they want to fix the tool config first
4. Only proceed with downgrade if user explicitly agrees

### Metadata
- Reproducible: yes (mcp.json misconfiguration)
- Related Files: C:/Users/jixia/.workbuddy/mcp.json
- See Also: LRN-20260601-001, ERR-20260601-002

------

## 2026-07-07 [ERR-20260707-001] sandbox-recycle-bin-blocked
- AVOID: 用 `Add-Type` / `New-Object -ComObject Shell.Application` 试图把文件移入回收站——沙箱判为"运行任意代码"而拦截
- STRATEGY: 项目目录删除直接用 `Remove-Item -Recurse -Force`（回收站机制在本沙箱不可用）
- CONSULT: 任意涉及文件删除的步骤
- **type:** 配置/文档
- **risk:** high
- **file:** (none)

## 2026-07-07 [ERR-20260707-002] managed-python-no-requests
- AVOID: 用托管 Python `.../binaries/python/versions/3.13.12/python.exe` 跑依赖 `requests` 的脚本（如 push_to_github.py）→ ModuleNotFoundError
- STRATEGY: GitHub-API / 网络类脚本统一用项目 `.venv` python（`D:/projects/fund-tracker/.venv/Scripts/python.exe`）
- CONSULT: fix-bug-lite Step 5（测试若需联网）/ Step 6（索引若调用 API）/ Step 8（蒸馏若调用 API）
- **type:** 跨文件
- **risk:** high
- **file:** push_to_github.py, .venv/

## 2026-07-07 [ERR-20260707-003] pytest-fd-capture-bug
- AVOID: 裸跑 `python -m pytest`——沙箱默认 fd 捕获报 "I/O operation on closed file" / "collected 0 items"，误以为无测试
- STRATEGY: 跑测试一律加 `-s`：`python -m pytest -s`
- CONSULT: fix-bug-lite Step 5（测试验证）
- **type:** 配置/文档
- **risk:** medium
- **file:** test_*.py

## 2026-07-07 [ERR-20260707-004] curl-no-network-egress
- AVOID: 用 `curl` 验证远程文件/接口——沙箱无网络出口，返回空响应（exit 0）误判成功
- STRATEGY: 远程校验改用 Python `requests.get()` + `base64.b64decode()`
- CONSULT: fix-bug-lite Step 6（变更检测/索引后远程校验）
- **type:** 跨文件
- **risk:** medium
- **file:** push_to_github.py

## 2026-07-07 [ERR-20260707-005] emoji-gbk-unicode-encode-error
- AVOID: 跑会打印非 ASCII（如 `⚠`）的 Python 脚本时不设 `PYTHONIOENCODING` → GBK 控制台 UnicodeEncodeError
- STRATEGY: 运行任何打印中文/emoji 的脚本前设 `PYTHONIOENCODING=utf-8`；或显式调用 `logger_config.setup_encoding()`
- CONSULT: fix-bug-lite 任意打印非 ASCII 的步骤（Step 5/6/8）
- **type:** 配置/文档
- **risk:** medium
- **file:** push_to_github.py, logger_config.py
