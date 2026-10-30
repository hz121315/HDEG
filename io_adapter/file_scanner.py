# -*- coding: utf-8 -*-
"""
文件扫描器（IO适配层）

扫描作品目录，识别各类文件：
- WAV/MP3 音频文件
- 台本文档（已翻译和未翻译）
- 世界观文档
- 术语表
"""

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field


# ==================== RJ 作品根目录识别 ====================

import re as _re_fs


def extract_main_rj_number(folder_name: str) -> str | None:
    """从文件夹名提取主 RJ 号（不含子编号）

    例如: "RJ01048637_030" → "RJ01048637"
    """
    m = _re_fs.search(r'(RJ\d{6,})', folder_name, _re_fs.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


# RJ 号向上查找的默认父目录层数（不含文件自身所在目录之上的“文件本身”那一次）
# 由 PipelineContext 按 config app.rj_lookup_depth 覆盖；单独调用时用默认值。
_DEFAULT_RJ_PARENT_DEPTH = 4
_rj_parent_depth = _DEFAULT_RJ_PARENT_DEPTH


def set_rj_lookup_depth(depth: int) -> None:
    """设置 RJ 号向上查找的父目录层数（进程级，由管道启动时写入）。"""
    global _rj_parent_depth
    try:
        _rj_parent_depth = max(0, int(depth))
    except (TypeError, ValueError):
        _rj_parent_depth = _DEFAULT_RJ_PARENT_DEPTH


def get_rj_lookup_depth() -> int:
    return _rj_parent_depth


def find_rj_work_root(path: Path,
                      max_parent_depth: int | None = None) -> tuple[Path, str] | tuple[None, None]:
    """向上查找 RJ 作品根目录

    从给定路径向上遍历，找到名字里带 RJ 号的上级目录。
    用于将同一 RJ 号下的子目录（如 MP3、02_MP3）归组。

    参数:
        max_parent_depth: 向上查找的父目录层数；None 表示使用进程级默认
            （config app.rj_lookup_depth，默认 4）

    返回: (rj_root, rj_number) 或 (None, None)
    """
    depth = _rj_parent_depth if max_parent_depth is None else max(0, int(max_parent_depth))
    current = path.resolve()
    for _ in range(depth + 1):  # 第 1 次是文件自身，其余是各级父目录
        rj = extract_main_rj_number(current.name)
        if rj:
            return current, rj
        if current.parent == current:
            break
        current = current.parent
    return None, None
