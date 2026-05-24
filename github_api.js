/**
 * GitHub API 上传模块
 * 通过 GitHub API 自动上传文件到仓库
 */

const GitHubAPI = {
    /**
     * 上传文件到 GitHub
     * @param {string} owner - 仓库所有者
     * @param {string} repo - 仓库名称
     * @param {string} path - 文件路径（如 data/purchase_records.json）
     * @param {string} content - 文件内容（字符串）
     * @param {string} message - 提交信息
     * @param {string} token - GitHub Personal Access Token
     * @param {string} [sha] - 文件的 SHA（如果文件已存在，需要提供）
     * @returns {Promise<object>} - API 响应
     */
    uploadFile: function(owner, repo, path, content, message, token, sha = null) {
        return new Promise((resolve, reject) => {
            // 将内容转换为 base64
            const contentBase64 = btoa(unescape(encodeURIComponent(content)));

            // 构建请求体
            const body = {
                message: message,
                content: contentBase64
            };

            // 如果文件已存在，需要提供 SHA
            if (sha) {
                body.sha = sha;
            }

            // 调用 GitHub API
            const xhr = new XMLHttpRequest();
            xhr.open('PUT', `https://api.github.com/repos/${owner}/${repo}/contents/${path}`);
            xhr.setRequestHeader('Authorization', `token ${token}`);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('User-Agent', 'FundTracker');

            xhr.onload = function() {
                if (xhr.status === 200 || xhr.status === 201) {
                    resolve(JSON.parse(xhr.responseText));
                } else {
                    reject(new Error(`GitHub API 错误: ${xhr.status} ${xhr.statusText}\n${xhr.responseText}`));
                }
            };

            xhr.onerror = function() {
                reject(new Error('网络错误，无法连接到 GitHub API'));
            };

            xhr.send(JSON.stringify(body));
        });
    },

    /**
     * 获取文件的 SHA（用于更新已存在的文件）
     * @param {string} owner - 仓库所有者
     * @param {string} repo - 仓库名称
     * @param {string} path - 文件路径
     * @param {string} token - GitHub Personal Access Token
     * @returns {Promise<string>} - 文件的 SHA
     */
    getFileSha: function(owner, repo, path, token) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', `https://api.github.com/repos/${owner}/${repo}/contents/${path}`);
            xhr.setRequestHeader('Authorization', `token ${token}`);
            xhr.setRequestHeader('User-Agent', 'FundTracker');

            xhr.onload = function() {
                if (xhr.status === 200) {
                    const data = JSON.parse(xhr.responseText);
                    resolve(data.sha);
                } else if (xhr.status === 404) {
                    // 文件不存在，返回 null
                    resolve(null);
                } else {
                    reject(new Error(`GitHub API 错误: ${xhr.status} ${xhr.statusText}`));
                }
            };

            xhr.onerror = function() {
                reject(new Error('网络错误，无法连接到 GitHub API'));
            };

            xhr.send();
        });
    },

    /**
     * 自动上传文件到 GitHub（智能处理 SHA）
     * @param {string} owner - 仓库所有者
     * @param {string} repo - 仓库名称
     * @param {string} path - 文件路径
     * @param {string} content - 文件内容
     * @param {string} message - 提交信息
     * @param {string} token - GitHub Personal Access Token
     * @returns {Promise<object>} - API 响应
     */
    autoUpload: function(owner, repo, path, content, message, token) {
        return this.getFileSha(owner, repo, path, token)
            .then(sha => {
                return this.uploadFile(owner, repo, path, content, message, token, sha);
            });
    }
};
