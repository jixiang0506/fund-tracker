#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志配置模块
统一配置所有脚本的日志记录
控制台和文件使用统一格式，通过 StreamHandler + FileHandler 双通道输出
"""

import logging
import os
import sys
import io
import json
from datetime import datetime

# 统一日志格式
LOG_FORMAT = '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# 北京时区（UTC+8），供全项目统一调用
# Python 3.9+ 内置 zoneinfo，项目最低版本为 3.13
from zoneinfo import ZoneInfo
_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def get_beijing_time():
    """获取当前北京时间（供全项目统一调用）"""
    return datetime.now(_BEIJING_TZ)


def safe_load_json(filepath, default=None, filter_keys=None):
    """
    统一的安全 JSON 加载函数。
    供全项目复用，避免各文件重复实现 try/except + json.load 模式。

    Args:
        filepath: JSON 文件路径
        default: 加载失败时返回的默认值（默认 None）
        filter_keys: 可选，过滤掉符合条件的键（如 lambda k: k.startswith("_")）
    Returns:
        解析后的 Python 对象，或 default（文件不存在/解析失败）
    """
    if not os.path.exists(filepath):
        log(f"[Warning] 文件不存在: {filepath}", "warning")
        return default
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if filter_keys:
            data = {k: v for k, v in data.items() if not filter_keys(k)}
        return data
    except Exception as e:
        log(f"[Error] 读取失败 {filepath}: {e}", "error")
        return default


def setup_encoding():
    """强制 UTF-8 stdout/stderr，避免 Windows 控制台 GBK 编码报错。"""
    if not getattr(sys.stdout, '_encoding_setup_done', False) and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdout._encoding_setup_done = True
    if not getattr(sys.stderr, '_encoding_setup_done', False) and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        sys.stderr._encoding_setup_done = True


def setup_logger(name='fund_tracker', log_level=logging.INFO):
    """
    配置并返回 logger 实例
    同时添加 StreamHandler（控制台）和 FileHandler（文件），使用统一格式
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler（按日期命名）
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    # 使用统一的北京时间函数
    beijing_now = get_beijing_time()
    log_file = os.path.join(log_dir, f'fund_tracker_{beijing_now.strftime("%Y%m%d")}.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 清理30天前的旧日志文件
    _cleanup_old_logs(log_dir, days=30)

    return logger


def _cleanup_old_logs(log_dir, days=30):
    """清理 logs/ 目录中超过 days 天的日志文件"""
    cutoff = get_beijing_time().timestamp() - days * 86400
    for fname in os.listdir(log_dir):
        if fname.startswith("fund_tracker_") and fname.endswith(".log"):
            fpath = os.path.join(log_dir, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.unlink(fpath)
            except OSError:
                pass  # 清理失败不阻塞主流程


# 创建默认 logger
logger = setup_logger()


def log(message, level='info'):
    """
    兼容旧代码的 log 函数
    通过 logger 统一输出到控制台和文件（不再使用 print）
    """
    if level == 'debug':
        logger.debug(message)
    elif level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        logger.info(message)

# .env parser limitations (simple implementation, no python-dotenv dependency):
# - Multi-line values (e.g. private keys) are not supported
# - Inline comments (# must be at line start) are not supported
# - Escaped quotes are not supported
# For full .env support, install python-dotenv and replace this function.
def load_env_file():
    """
    从 .env 文件加载环境变量到 os.environ。
    .env 文件位于 logger_config.py 同级目录（项目根目录）。

    限制（简化实现，不引入 python-dotenv 依赖）：
    - 不支持多行值（如私钥、证书）
    - 不支持行内注释（# 必须独占行首）
    - 不支持转义引号
    如需完整 .env 支持，请安装 python-dotenv 并替换此函数。
    """
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(env_file):
        return
    log("[OK] 读取 .env 文件...", "info")
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                value = value.strip().strip('"').strip("'")
                os.environ[key.strip()] = value
    log("[OK] 已加载 .env 配置", "info")
