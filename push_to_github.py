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
import time
import argparse
from datetime import datetime, timezone, timedelta
from logger_config import log, load_env_file


def get_file_sha(file_path, owner, repo, token):
    """获取文件的当前 SHA（区分 404 和其他错误）"""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        'Authorization': 'token ' + token,
        'Accept': 'application/vnd.github.v3+json'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json().get('sha')
        elif response.status_code == 404:
            return None  # 文件确实不存在，正常
        else:
            # 401/403/500 等异常，记录错误并返回特殊值触发重试
            log(f"[WARN] get_file_sha 返回异常状态码 {response.status_code}: {response.text[:200]}", "warning")
            return "__ERROR__"
    except Exception as e:
        log(f"[WARN] get_file_sha 请求异常: {e}", "warning")
        return "__ERROR__"


def push_file(file_path, message, owner, repo, token, branch='main', max_retries=3):
    """推送单个文件到 GitHub，自动处理 409 冲突并重试"""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        'Authorization': 'token ' + token,
        'Accept': 'application/vnd.github.v3+json',
    }

    # 读取文件内容
    try:
        with open(file_path, 'rb') as f:
            file_content = f.read()
    except Exception as e:
        log(f"[Error] 读取文件失败: {file_path} - {e}", "error")
        return False

    content_b64 = base64.b64encode(file_content).decode('utf-8')

    for attempt in range(max_retries + 1):
        # 每次重试前重新获取 SHA，防止 409 冲突
        sha = get_file_sha(file_path, owner, repo, token)
        if sha == "__ERROR__":
            log(f"[ERROR] 获取 SHA 失败，跳过此次推送: {file_path}", "error")
            return False

        data = {
            'message': message,
            'content': content_b64,
            'branch': branch
        }
        if sha:
            data['sha'] = sha

        try:
            response = requests.put(url, headers=headers, json=data, timeout=120)

            if response.status_code in [200, 201]:
                log(f"[OK] 成功推送: {file_path}", "info")
                return True
            elif response.status_code == 409 and attempt < max_retries:
                log(f"  ⚠ 409 冲突，正在重试 ({attempt + 1}/{max_retries})...", "warning")
                time.sleep(2)
                continue
            else:
                log(f"[Error] 推送失败: {file_path}", "error")
                log(f"   错误: {response.status_code}", "error")
                log(f"   返回: {response.text[:200]}", "error")
                return False
        except Exception as e:
            if attempt < max_retries:
                log(f"  ⚠ 推送异常，正在重试 ({attempt + 1}/{max_retries}): {e}", "warning")
                time.sleep(2)
                continue
            else:
                log(f"[Error] 推送异常: {file_path} - {e}", "error")
                return False

    return False


def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='推送文件到 GitHub 仓库',
        epilog='示例: python push_to_github.py index.html fetch_fund_data.py'
    )
    parser.add_argument(
        'files',
        nargs='*',
        help='要推送的文件列表（不指定则使用 github_config.json 中的 files）'
    )
    parser.add_argument(
        '--config',
        default='github_config.json',
        help='配置文件路径（默认: github_config.json）'
    )
    args = parser.parse_args()

    # 加载 .env 环境变量
    load_env_file()

    # 从环境变量读取 Token（安全方式）
    token = os.environ.get('GITHUB_TOKEN', '')
    if not token:
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

    # 读取配置文件（获取 owner 和 repo）
    config_file = args.config if os.path.isabs(args.config) else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), args.config)

    if not os.path.exists(config_file):
        log("[Error] 错误: 配置文件不存在: " + config_file, "error")
        log("请先创建 github_config.json 文件（不需要包含 token 字段）", "info")
        sys.exit(1)

    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    owner = config.get('owner', '')
    repo = config.get('repo', '')
    branch = config.get('branch', 'main')

    # 确定要推送的文件列表
    if args.files:
        # 命令行参数指定的文件
        files_to_push = args.files
        log("[OK] 使用命令行参数指定的文件", "info")
    else:
        # 回退到配置文件
        files_to_push = config.get('files', [])
        if not files_to_push:
            log("[Error] 错误: 未指定文件，且配置文件中 files 为空", "error")
            log("请通过命令行指定文件，或在配置文件中设置 files", "info")
            sys.exit(1)
        log("[OK] 使用配置文件中的文件列表", "info")

    log("[OK] 准备推送到 " + owner + "/" + repo, "info")
    log("=" * 60, "info")
    log("待推送文件: " + ", ".join(files_to_push), "info")
    log("=" * 60, "info")

    success_count = 0
    for i, file_path in enumerate(files_to_push):
        if os.path.exists(file_path):
            # 提交信息包含时间戳，便于追溯（使用北京时间）
            beijing_now = datetime.now(timezone(timedelta(hours=8)))
            commit_msg = "更新 {} ({})".format(
                file_path,
                beijing_now.strftime("%Y-%m-%d %H:%M")
            )
            if push_file(file_path, commit_msg, owner, repo, token, branch):
                success_count += 1
        else:
            log("[Warning] 文件不存在: " + file_path, "warning")

        # 多文件推送间隔 1 秒，降低 409/速率限制风险
        if i < len(files_to_push) - 1:
            time.sleep(1)

    log("=" * 60, "info")
    log("完成: {}/{} 个文件推送成功".format(success_count, len(files_to_push)), "info")

    if success_count == len(files_to_push):
        log("", "info")
        log("[OK] 所有文件推送成功！", "info")
        log("[OK] 请访问 https://jixiang0506.github.io/fund-tracker/ 查看部署结果", "info")
    else:
        log("", "info")
        log("[Warning] 有 {} 个文件推送失败".format(len(files_to_push) - success_count), "warning")


if __name__ == '__main__':
    main()
