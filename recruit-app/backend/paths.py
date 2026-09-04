"""数据目录解析（单机版 A 方案 spec §4.2）：env RECRUIT_DATA_DIR → <程序根>/data。

程序根 = backend/ 的上一级：WSL 开发 = recruit-app 仓库根；安装版 =
%LOCALAPPDATA%\\RecruitApp\\app。全部数据落点（app.db / secret.key / uploads /
browser_profile / login-profile）都经本模块在**调用时**解析——安装版由 run.bat 注入
RECRUIT_DATA_DIR，WSL 开发不设 env → 落点与现状完全一致（零回归）。
"""
from __future__ import annotations

import os
from pathlib import Path

_PROGRAM_ROOT = Path(__file__).resolve().parent.parent


def program_root() -> Path:
    """程序根：backend/ 的上一级（仓库根或安装版 app 目录）。"""
    return _PROGRAM_ROOT


def data_dir() -> Path:
    """数据目录：env RECRUIT_DATA_DIR 覆盖，未设 → <程序根>/data。

    只计算路径、绝不 mkdir——建目录由各消费者（db/security/uploads/浏览器启动）自理。
    """
    env = os.environ.get("RECRUIT_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return _PROGRAM_ROOT / "data"
