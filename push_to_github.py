#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 GitHub API 推送更改到仓库
Token 从环境变量 GITHUB_TOKEN 读取（安全）
支持从 .env 文件自动加载（方便开发）
其他配置从 github_config.json 读取
"""

import os
import json
import base64
import requests
import sys
from logger_config import log

# 尝试从 .env 文件加载环境变量（如果存在）
def load_env_file():
    """从 .env 文件加载环境变量"""
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_file):
        log("[OK] 读取 .env 文件...")
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # 去除值两端的引号
                    value = value.strip().strip('"').strip("'")
                    os.environ[key.strip()] = value
        log("[OK] 已加载 .env 配置")

load_env_file()

# 从环境变量读取 Token（安全方式）
TOKEN = os.environ.get('GITHUB_TOKEN', '')
if not TOKEN:
    log("[Error] 错误: 未设置环境变量 GITHUB_TOKEN", "error")
    log("", "info")
    log("解决方法（任选一种）：", "info")
    log("  方法1: 创建 .env 文件，内容：", "info")
    log('    GITHUB_TOKEN=ghp_your_token_here', "info")
    log("", "info")
    log("  方法2: 设置系统环境变量：", "info")
    log("    Windows CMD: setx GITHUB_TOKEN your_token", "info")
    log("    PowerShell: $env:GITHUB_TOKEN='your_token'", "info")
    log("", "info")
    log("[Warning] 不要将 Token 直接写在代码中或提交到 Git！", "warning")
    sys.exit(1)

# 读取其他配置（不含 Token）
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'github_config.json')

if not os.path.exists(CONFIG_FILE):
    log("[Error] 错误: 配置文件不存在: " + CONFIG_FILE, "error")
    log("请先创建 github_config.json 文件（不需要包含 token 字段）", "info")
    sys.exit(1)

with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    config = json.load(f)

OWNER = config.get('owner', '')
REPO = config.get('repo', '')
FILES_TO_PUSH = config.get('files', [])

def get_file_sha(file_path):
    """获取文件的当前 SHA"""
    url = "https://api.github.com/repos/{}/{}/contents/{}".format(OWNER, REPO, file_path)
    headers = {
        'Authorization': 'token ' + TOKEN,
        'Accept': 'application/vnd.github.v3+json'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json().get('sha')
    except Exception as e:
        # 获取SHA失败不影响推送（如果是新文件，SHA不存在）
        pass
    return None

def push_file(file_path, message):
    """推送单个文件到 GitHub"""
    url = "https://api.github.com/repos/{}/{}/contents/{}".format(OWNER, REPO, file_path)
    headers = {
        'Authorization': 'token ' + TOKEN,
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json'
    }
    
    # 读取文件内容
    try:
        with open(file_path, 'rb') as f:
            file_content = f.read()
    except Exception as e:
        log("[Error] 读取文件失败: " + file_path + " - " + str(e), "error")
        return False
    
    # Base64 编码
    content_b64 = base64.b64encode(file_content).decode('utf-8')
    
    # 获取当前文件的 SHA
    sha = get_file_sha(file_path)
    
    # 准备请求数据
    data = {
        'message': message,
        'content': content_b64,
        'branch': 'main'
    }
    
    if sha:
        data['sha'] = sha
        msg = "更新 " + file_path
    else:
        msg = "添加 " + file_path
    
    # 发送请求
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        
        if response.status_code in [200, 201]:
            log("[OK] 成功推送: " + file_path, "info")
            return True
        else:
            log("[Error] 推送失败: " + file_path, "error")
            log("   错误: " + str(response.status_code), "error")
            log("   返回: " + response.text[:200], "error")
            return False
    except Exception as e:
        log("[Error] 推送异常: " + file_path + " - " + str(e), "error")
        return False

def main():
    log("[OK] 准备推送到 " + OWNER + "/" + REPO, "info")
    log("=" * 60, "info")
    
    success_count = 0
    for file_path in FILES_TO_PUSH:
        if os.path.exists(file_path):
            if push_file(file_path, "更新 " + file_path):
                success_count += 1
        else:
            log("[Warning] 文件不存在: " + file_path, "warning")
    
    log("=" * 60, "info")
    log("完成: {}/{} 个文件推送成功".format(success_count, len(FILES_TO_PUSH)), "info")
    
    if success_count == len(FILES_TO_PUSH):
        log("", "info")
        log("[OK] 所有文件推送成功！", "info")
        log("[OK] 请访问 https://jixiang0506.github.io/fund-tracker/ 查看部署结果", "info")
    else:
        log("", "info")
        log("[Warning] 有 {} 个文件推送失败".format(len(FILES_TO_PUSH) - success_count), "warning")

if __name__ == '__main__':
    main()
