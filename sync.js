/**
 * 同步管理器 - 保存并同步功能
 * 包含：确认对话框、GitHub 保存、Workflow 触发、数据轮询
 */

/**
 * 快速配置 GitHub PAT
 * 保存 Token / Owner / Repo 到 localStorage
 */
async function saveQuickConfig() {
    const tokenInput = document.getElementById('quickTokenInput');
    const token = tokenInput.value.trim();

    if (!token) {
        showMessage('请先粘贴 GitHub Token', 'error');
        return;
    }

    // 格式验证
    if (!token.startsWith('ghp_') && !token.startsWith('github_pat_')) {
        showMessage('⚠️ Token 格式可能不正确（应以 ghp_ 或 github_pat_ 开头）', 'error');
        return;
    }

    // 显示验证中
    const statusEl = document.getElementById('quickConfigStatus');
    statusEl.textContent = '⏳ 正在验证 Token...';
    statusEl.style.color = '#f57c00';

    try {
        // 验证 Token（调用 GitHub API）
        const resp = await fetch('https://api.github.com/user', {
            headers: {
                'Authorization': `token ${token}`,
                'Accept': 'application/vnd.github.v3+json'
            }
        });

        if (!resp.ok) {
            throw new Error(`Token 验证失败: ${resp.status}`);
        }

        const userData = await resp.json();
        const username = userData.login;

        // 检查用户名是否匹配
        if (username !== 'jixiang0506') {
            statusEl.textContent = `❌ Token 属于 ${username}，但预期是 jixiang0506`;
            statusEl.style.color = '#f44336';
            showMessage(`⚠️ Token 属于 ${username}，但预期是 jixiang0506。请确认 Token 正确。`, 'error');
            return;
        }

        // 验证通过，保存到 localStorage
        localStorage.setItem('github_token', token);
        localStorage.setItem('repo_owner', 'jixiang0506');
        localStorage.setItem('repo_name', 'fund-tracker');

        // 更新 state
        state.github.token = token;
        state.github.owner = 'jixiang0506';
        state.github.repo = 'fund-tracker';

        // 更新主表单
        document.getElementById('githubToken').value = token;
        document.getElementById('repoOwner').value = 'jixiang0506';
        document.getElementById('repoName').value = 'fund-tracker';

        // 显示成功
        statusEl.textContent = '✅ 配置已保存';
        statusEl.style.color = '#4caf50';
        showMessage('✅ GitHub 配置已保存，Token 验证通过', 'success');

        // 清空输入框（安全考虑）
        tokenInput.value = '';

        // 3秒后清除状态提示
        setTimeout(() => { statusEl.textContent = ''; }, 3000);

    } catch (error) {
        statusEl.textContent = '❌ 验证失败';
        statusEl.style.color = '#f44336';
        showMessage('Token 验证失败: ' + error.message, 'error');
    }
}

// ============ 确认对话框 ============

/**
 * 显示保存确认对话框
 */
async function showConfirmDialog() {
    if (!state.github.token || !state.github.owner || !state.github.repo) {
        showMessage('请先在「⚙️ GitHub 配置」标签页填写 Token 和仓库信息', 'error');
        return;
    }

    const dialogBody = document.getElementById('confirmDialogBody');
    let html = '';

    // 1. 检查基金配置变更（fund_config.json）
    const configChanged = getConfigChanges();
    if (configChanged.added.length > 0 || configChanged.removed.length > 0) {
        html += '<div class="confirm-section">';
        html += '<h4>📊 基金配置变更</h4>';
        if (configChanged.added.length > 0) {
            html += '<div class="confirm-item"><span class="label">新增基金：</span>';
            configChanged.added.forEach(f => {
                html += `<div class="value change-add">+ ${escapeHtml(f.code)} ${escapeHtml(f.name)}（${escapeHtml(f.platform)}）</div>`;
            });
            html += '</div>';
        }
        if (configChanged.removed.length > 0) {
            html += '<div class="confirm-item"><span class="label">删除基金：</span>';
            configChanged.removed.forEach(f => {
                html += `<div class="value change-del">- ${escapeHtml(f.code)} ${escapeHtml(f.name)}（${escapeHtml(f.platform)}）</div>`;
            });
            html += '</div>';
        }
        html += '</div>';
    }

    // 2. 显示交易记录摘要
    const recordsSummary = getRecordsSummary();
    if (recordsSummary.length > 0) {
        html += '<div class="confirm-section">';
        html += '<h4>📋 交易记录</h4>';
        recordsSummary.forEach(s => {
            html += `<div class="confirm-item">`;
            html += `<span class="label">${escapeHtml(s.platform)} - ${escapeHtml(s.name)}（${escapeHtml(s.code)}）：</span>`;
            html += `<div class="value">${escapeHtml(s.summary)}</div>`;
            html += '</div>';
        });
        html += '</div>';
    }

    // 3. 如果没有变更
    if (!html) {
        html = '<div style="text-align:center;padding:20px;color:var(--color-text-muted);">没有检测到需要保存的变更</div>';
        setConfirmBtnState(false);
    } else {
        setConfirmBtnState(true);
    }

    // 4. 添加操作说明
    html += '<div class="confirm-section" style="background:#fff3cd;padding:12px 15px;border-radius:8px;margin-top:15px;">';
    html += '<div style="font-size:13px;color:#856404;line-height:1.8;">';
    html += '✅ 确认后将会：<br>';
    html += '1. 保存 <code>purchase_records.json</code> 和 <code>fund_config.json</code> 到 GitHub<br>';
    html += '2. 自动触发 GitHub Actions 更新基金数据<br>';
    html += '3. 更新完成后自动刷新页面数据';
    html += '</div></div>';

    dialogBody.innerHTML = html;

    // 显示对话框
    document.getElementById('confirmDialog').classList.add('show');
}

/**
 * 关闭确认对话框
 */
function closeConfirmDialog() {
    document.getElementById('confirmDialog').classList.remove('show');
}

/**
 * 设置确认按钮状态
 */
function setConfirmBtnState(enabled) {
    const btn = document.getElementById('confirmSaveBtn');
    btn.disabled = !enabled;
    btn.style.opacity = enabled ? '1' : '0.5';
    btn.style.cursor = enabled ? 'pointer' : 'not-allowed';
}

/**
 * 获取基金配置变更
 */
function getConfigChanges() {
    const added = [];
    const removed = [];

    // 当前 fundConfig 中的基金
    const configFunds = {};
    if (state.fundConfig) {
        for (const [platform, funds] of Object.entries(state.fundConfig)) {
            funds.forEach(f => {
                const key = `${platform}::${f.code}`;
                configFunds[key] = { code: f.code, name: f.name, platform: platform };
            });
        }
    }

    // purchaseRecords 中有记录的基金
    const recordFunds = {};
    for (const [platform, records] of Object.entries(state.purchaseRecords)) {
        if (records && records.length > 0) {
            const codeSet = new Set();
            records.forEach(r => {
                if (r.code) codeSet.add(r.code);
            });
            codeSet.forEach(code => {
                const key = `${platform}::${code}`;
                recordFunds[key] = true;
            });
        }
    }

    // 新增：有记录但没有在配置中
    for (const key of Object.keys(recordFunds)) {
        if (!configFunds[key]) {
            const [platform, code] = key.split('::');
            const fundName = getFundNameFromConfigSync(code, platform) || code;
            added.push({ code: code, name: fundName, platform: platform });
        }
    }

    // 删除：有配置但没有记录
    for (const key of Object.keys(configFunds)) {
        if (!recordFunds[key]) {
            const [platform, code] = key.split('::');
            const fundName = configFunds[key].name || code;
            removed.push({ code: code, name: fundName, platform: platform });
        }
    }

    return { added: added, removed: removed };
}

/**
 * 从 fundConfig 获取基金名称
 */
function getFundNameFromConfigSync(code, platform) {
    if (!state.fundConfig || !state.fundConfig[platform]) return '';
    const fund = state.fundConfig[platform].find(f => f.code === code);
    return fund ? fund.name : '';
}

/**
 * 获取交易记录摘要
 */
function getRecordsSummary() {
    const summary = [];

    for (const [platform, records] of Object.entries(state.purchaseRecords)) {
        if (!records || records.length === 0) continue;

        // 按基金代码分组
        const fundRecords = {};
        records.forEach((r, i) => {
            const code = r.code || 'unknown';
            if (!fundRecords[code]) fundRecords[code] = [];
            fundRecords[code].push({ ...r, index: i });
        });

        for (const [code, recs] of Object.entries(fundRecords)) {
            const totalAmount = recs.reduce((sum, r) => sum + (r.amount || 0), 0);
            const totalShares = recs.reduce((sum, r) => sum + (r.shares || 0), 0);
            const name = getFundNameFromConfigSync(code, platform) || code;
            let s = '';
            if (totalAmount > 0) s += `买入 ¥${totalAmount.toFixed(2)}`;
            if (totalShares > 0) s += (s ? '，' : '') + `卖出 ${totalShares.toFixed(2)} 份`;
            s += `（共 ${recs.length} 笔）`;
            summary.push({ platform: platform, code: code, name: name, summary: s });
        }
    }

    return summary;
}

// ============ 执行保存并同步 ============

/**
 * 执行保存并同步（确认对话框点击确认后调用）
 */
async function executeSaveAndSync() {
    closeConfirmDialog();

    // 显示同步状态
    showSyncStatus('loading', '⏳ 正在保存数据到 GitHub...');

    try {
        // 1. 保存 purchase_records.json
        showSyncStatus('loading', '⏳ 正在保存交易记录...');
        await saveFileToGitHub(
            'data/purchase_records.json',
            state.purchaseRecords,
            `📝 更新交易记录 ${new Date().toLocaleString('zh-CN')}`
        );

        // 2. 保存 fund_config.json
        showSyncStatus('loading', '✅ 交易记录已保存，正在保存基金配置...');
        const config = { funds: {} };
        if (state.fundConfig) {
            for (const [platform, funds] of Object.entries(state.fundConfig)) {
                config.funds[platform] = funds.map(f => ({
                    code: f.code,
                    name: f.name,
                    benchmark: f.benchmark || ''
                }));
            }
        }
        await saveFileToGitHub(
            'fund_config.json',
            config,
            `⚙️ 更新基金配置 ${new Date().toLocaleString('zh-CN')}`
        );

        // 3. 触发 GitHub Actions
        showSyncStatus('loading', '✅ 基金配置已保存，正在触发数据更新...');
        await triggerWorkflow();

        // 4. 开始轮询
        showSyncStatus('loading', '⏳ 数据更新中，正在等待完成...<div class="sync-progress">预计需要 1-3 分钟</div>');
        startPollingForUpdate();

    } catch (error) {
        console.error('保存并同步失败:', error);
        showSyncStatus('error', '❌ 同步失败：' + error.message);
    }
}

/**
 * 保存文件到 GitHub
 */
async function saveFileToGitHub(path, contentObj, message) {
    const jsonStr = JSON.stringify(contentObj, null, 2);
    const encoder = new TextEncoder();
    const bytes = encoder.encode(jsonStr);
    const content = btoa(String.fromCharCode(...bytes));

    // 获取文件 SHA
    let sha = null;
    try {
        const resp = await fetch(
            `https://api.github.com/repos/${state.github.owner}/${state.github.repo}/contents/${path}`,
            {
                headers: {
                    'Authorization': `token ${state.github.token}`,
                    'Accept': 'application/vnd.github.v3+json'
                }
            }
        );
        if (resp.ok) {
            const data = await resp.json();
            sha = data.sha;
        }
    } catch (e) {
        // 文件不存在，忽略
    }

    const body = {
        message: message,
        content: content,
        branch: 'main'
    };
    if (sha) body.sha = sha;

    const resp = await fetch(
        `https://api.github.com/repos/${state.github.owner}/${state.github.repo}/contents/${path}`,
        {
            method: 'PUT',
            headers: {
                'Authorization': `token ${state.github.token}`,
                'Accept': 'application/vnd.github.v3+json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(body)
        }
    );

    if (!resp.ok) {
        const errText = await resp.text();
        throw new Error(`保存 ${path} 失败: ${resp.status} ${errText}`);
    }
}

/**
 * 触发 GitHub Actions workflow
 */
async function triggerWorkflow() {
    const resp = await fetch(
        `https://api.github.com/repos/${state.github.owner}/${state.github.repo}/actions/workflows/update.yml/dispatches`,
        {
            method: 'POST',
            headers: {
                'Authorization': `token ${state.github.token}`,
                'Accept': 'application/vnd.github.v3+json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ref: 'main' })
        }
    );

    // 204 No Content 是成功
    if (resp.status !== 204 && !resp.ok) {
        const errText = await resp.text();
        throw new Error(`触发数据更新失败: ${resp.status} ${errText}`);
    }
}

// ============ 同步状态提示 ============

/**
 * 显示同步状态
 */
function showSyncStatus(type, message) {
    const el = document.getElementById('syncStatus');
    el.className = `sync-status ${type}`;
    el.innerHTML = message;
    el.classList.add('show');

    if (type !== 'loading') {
        setTimeout(() => hideSyncStatus(), 5000);
    }
}

/**
 * 隐藏同步状态
 */
function hideSyncStatus() {
    const el = document.getElementById('syncStatus');
    el.classList.remove('show');
    setTimeout(() => { el.className = 'sync-status'; }, 300);
}

// ============ 数据更新轮询 ============

/**
 * 开始轮询数据更新
 */
function startPollingForUpdate() {
    // 清除之前的轮询
    if (state.dataUpdate.pollingTimer) {
        clearTimeout(state.dataUpdate.pollingTimer);
    }

    state.dataUpdate.pollCount = 0;
    state.dataUpdate.lastUpdateTime = state.fundsData ? state.fundsData.update_time : null;

    pollForUpdate();
}

/**
 * 轮询检查数据是否更新
 */
async function pollForUpdate() {
    if (state.dataUpdate.pollCount >= state.dataUpdate.maxPollCount) {
        showSyncStatus('error', '⏰ 轮询超时，请手动刷新页面');
        return;
    }

    state.dataUpdate.pollCount++;

    try {
        const resp = await fetch(`data/funds_data.json?t=${new Date().getTime()}`);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);

        const buffer = await resp.arrayBuffer();
        const text = new TextDecoder('utf-8').decode(buffer);
        const data = JSON.parse(text);

        // 检查数据是否已更新
        const hasNewData = checkIfDataUpdated(data);

        if (hasNewData) {
            // 数据已更新，刷新页面
            showSyncStatus('success', '✅ 数据更新完成！正在刷新页面...');
            setTimeout(() => {
                loadData();
                hideSyncStatus();
            }, 1000);
        } else {
            // 继续轮询
            const elapsed = state.dataUpdate.pollCount * 10;
            const remaining = state.dataUpdate.maxPollCount - state.dataUpdate.pollCount;
            showSyncStatus('loading',
                `⏳ 数据更新中（${elapsed}s）...<div class="sync-progress">还需约 ${Math.max(1, Math.round(remaining * 10 / 60))} 分钟，已轮询 ${state.dataUpdate.pollCount} 次</div>`
            );

            state.dataUpdate.pollingTimer = setTimeout(pollForUpdate, 10000); // 10秒轮询一次
        }
    } catch (err) {
        console.error('轮询失败:', err);
        // 继续轮询，不中断
        state.dataUpdate.pollingTimer = setTimeout(pollForUpdate, 10000);
    }
}

/**
 * 检查数据是否已更新
 */
function checkIfDataUpdated(newData) {
    if (!state.dataUpdate.lastUpdateTime) return true; // 首次加载，直接刷新

    // 比较更新时间
    if (newData.update_time !== state.dataUpdate.lastUpdateTime) {
        return true;
    }

    // 检查是否有新基金
    if (state.dataUpdate.newFundCode) {
        const found = Object.values(newData.funds || {}).some(funds =>
            funds.some(f => f.code === state.dataUpdate.newFundCode)
        );
        if (found) return true;
    }

    return false;
}
