#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 GitHub API 推送更改到仓库
Token 从环境变量 GITHUB_TOKEN 读取（安全）
支持从 .env 文件自动加载（方便开发）
其他配置从 github_config.json 读取

改进点（2026-06-05）：
1. 添加 SSL/网络错误重试机制（指数退避）
2. 推送完成后自动同步本地仓库（git fetch + git reset）
"""

import os
import json
import base64
import requests
import sys
import time
import argparse
import subprocess
from logger_config import log, load_env_file, get_beijing_time


def get_file_sha(file_path, owner, repo, token, max_retries=1):
    """
    获取文件的当前 SHA（支持重试和指数退避）

    重试场景（max_retries 默认 1）：
    - SSL 错误（SSLEOFError 等）
    - 网络连接超时
    - 5xx 服务器错误
    - 其他临时网络错误

    不重试场景（直接返回特殊标记）：
    - "__AUTH_ERROR__"：401/403（认证失败，外层不应重试）
    - "__ERROR__"：其他错误（外层可重试）
    - None：404（文件不存在，正常情况）

    注意：外层 upload_file 已包含重试循环，内层建议 max_retries=0 避免嵌套重试。
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        'Authorization': 'token ' + token,
        'Accept': 'application/vnd.github.v3+json'
    }

    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                return response.json().get('sha')
            elif response.status_code == 404:
                return None  # 文件不存在，正常
            elif response.status_code in [401, 403]:
                # 认证失败：外层无需重试，直接返回特殊标记
                log(f"[WARN] get_file_sha 认证失败 {response.status_code}: {response.text[:200]}", "warning")
                return "__AUTH_ERROR__"
            elif response.status_code >= 500 and attempt < max_retries:
                # 5xx 错误，重试
                wait_time = 2 ** attempt  # 指数退避：1s, 2s
                log(f"[WARN] {file_path} - 服务器错误 {response.status_code}，{wait_time}s 后重试 ({attempt + 1}/{max_retries})...", "warning")
                time.sleep(wait_time)
                continue
            elif attempt == max_retries:
                # 已达最大重试次数
                log(f"[WARN] get_file_sha 返回异常状态码 {response.status_code}: {response.text[:200]}", "warning")
                return "__ERROR__"
            else:
                # 其他 4xx 错误，不重试
                log(f"[WARN] get_file_sha 返回异常状态码 {response.status_code}: {response.text[:200]}", "warning")
                return "__ERROR__"

        except requests.exceptions.SSLError as e:
            if attempt < max_retries:
                wait_time = 2 ** attempt
                log(f"[WARN] {file_path} - SSL 错误，{wait_time}s 后重试 ({attempt + 1}/{max_retries})...", "warning")
                time.sleep(wait_time)
                continue
            else:
                log(f"[ERROR] {file_path} - SSL 错误（已达最大重试次数）: {e}", "error")
                return "__ERROR__"

        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait_time = 2 ** attempt
                log(f"[WARN] {file_path} - 网络错误，{wait_time}s 后重试 ({attempt + 1}/{max_retries})...", "warning")
                time.sleep(wait_time)
                continue
            else:
                log(f"[ERROR] {file_path} - 网络错误（已达最大重试次数）: {e}", "error")
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
        # max_retries=0：内层不重试，外层已负责重试，避免嵌套重试产生过多 API 调用
        sha = get_file_sha(file_path, owner, repo, token, max_retries=0)

        if sha == "__AUTH_ERROR__":
            # 认证失败（401/403），外层重试无意义，直接失败
            log(f"[ERROR] 认证失败，停止推送: {file_path}", "error")
            return False

        if sha == "__ERROR__":
            if attempt < max_retries:
                wait_time = 2 ** attempt
                log(f"  ⚠ SHA 获取失败，{wait_time}s 后重试 ({attempt + 1}/{max_retries})...", "warning")
                time.sleep(wait_time)
                continue
            else:
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
                wait_time = 2 ** attempt
                log(f"  ⚠ 推送异常，{wait_time}s 后重试 ({attempt + 1}/{max_retries}): {e}", "warning")
                time.sleep(wait_time)
                continue
            else:
                log(f"[Error] 推送异常: {file_path} - {e}", "error")
                return False


def sync_local_repo(branch='main'):
    """
    同步本地仓库到远程状态
    
    执行步骤：
    1. git fetch origin <branch>
    2. git reset --hard origin/<branch>
    
    注意：此操作会丢弃所有本地修改，请确保已推送所有更改
    """
    log("", "info")
    log("=" * 60, "info")
    log("[INFO] 同步本地仓库到 origin/" + branch + "...", "info")
    
    try:
        # Step 1: git fetch
        log("  > git fetch origin " + branch, "info")
        result = subprocess.run(
            ['git', 'fetch', 'origin', branch],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            log(f"[WARN] git fetch 失败: {result.stderr}", "warning")
            return False
        
        # Step 2: git reset --hard
        log("  > git reset --hard origin/" + branch, "info")
        result = subprocess.run(
            ['git', 'reset', '--hard', f'origin/{branch}'],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            log(f"[WARN] git reset 失败: {result.stderr}", "warning")
            return False
        
        log("[OK] 本地仓库已同步到 origin/" + branch, "info")
        return True
        
    except subprocess.TimeoutExpired:
        log("[ERROR] git 命令超时（30s）", "error")
        return False
    except Exception as e:
        log(f"[ERROR] 同步本地仓库失败: {e}", "error")
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
    parser.add_argument(
        '--no-sync',
        action='store_true',
        help='推送后不同步本地仓库'
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
    failed_files = []
    
    for i, file_path in enumerate(files_to_push):
        if os.path.exists(file_path):
            # 提交信息包含时间戳，便于追溯（使用北京时间）
            beijing_now = get_beijing_time()
            commit_msg = "更新 {} ({})".format(
                file_path,
                beijing_now.strftime("%Y-%m-%d %H:%M")
            )
            if push_file(file_path, commit_msg, owner, repo, token, branch):
                success_count += 1
            else:
                failed_files.append(file_path)
        else:
            log("[Warning] 文件不存在: " + file_path, "warning")
            failed_files.append(file_path)

        # 多文件推送间隔 1 秒，降低 409/速率限制风险
        if i < len(files_to_push) - 1:
            time.sleep(1)

    log("=" * 60, "info")
    log("完成: {}/{} 个文件推送成功".format(success_count, len(files_to_push)), "info")
    log("", "info")
    
    if success_count == len(files_to_push):
        log("[OK] 所有文件推送成功！", "info")
        log("[OK] 请访问 https://jixiang0506.github.io/fund-tracker/ 查看部署结果", "info")
        
        # 自动同步本地仓库
        if not args.no_sync:
            sync_local_repo(branch)
    else:
        log("[Warning] 有 {} 个文件推送失败".format(len(files_to_push) - success_count), "warning")
        if failed_files:
            log("[INFO] 失败文件列表:", "info")
            for f in failed_files:
                log(f"  - {f}", "info")
            log("", "info")
            log("[TIP] 可重试失败的文件: python push_to_github.py " + " ".join(failed_files), "info")


if __name__ == '__main__':
    main()
