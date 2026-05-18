#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志配置模块
统一配置所有脚本的日志记录
"""

import logging
import os
from datetime import datetime

def setup_logger(name='fund_tracker', log_level=logging.INFO):
    """
    配置并返回 logger 实例
    只配置文件日志，控制台输出用 print()
    """
    # 创建 logger
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    # 只创建文件 handler (按日期命名)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f'fund_tracker_{datetime.now().strftime("%Y%m%d")}.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建 formatter（详细格式）
    file_format = '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] - %(message)s'
    file_formatter = logging.Formatter(file_format, datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)
    
    # 只添加文件 handler
    logger.addHandler(file_handler)
    
    return logger


# 创建默认 logger
logger = setup_logger()


def log(message, level='info'):
    """
    兼容旧代码的 log 函数
    输出到控制台（print）和日志文件
    """
    # 同时输出到控制台（确保可见）
    print(message)
    
    if level == 'debug':
        logger.debug(message)
    elif level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        logger.info(message)
