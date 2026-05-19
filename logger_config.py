#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志配置模块
统一配置所有脚本的日志记录
控制台和文件使用统一格式，通过 StreamHandler + FileHandler 双通道输出
"""

import logging
import os
from datetime import datetime

# 统一日志格式
LOG_FORMAT = '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


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

    log_file = os.path.join(log_dir, f'fund_tracker_{datetime.now().strftime("%Y%m%d")}.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


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
