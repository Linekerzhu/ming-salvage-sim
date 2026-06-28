"""统一日志工具：模块级 logger 工厂 + 受控异常吞咽。

G2 改进：此前 ming_sim/ 有 ~95 处 except Exception: pass，零日志——生产环境
静默失败无任何痕迹。本模块提供：
- get_logger(name)：返回 logging.getLogger(f"ming_sim.{name}")，统一命名空间
- log_swallow(context, *, level=logging.WARNING)：上下文管理器，捕获异常并记日志
  （不 re-raise，保持原有"容错"语义，但留下痕迹）
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator


def get_logger(name: str) -> logging.Logger:
    """返回 ming_sim.<name> logger，与 web_app._LOG('ming_sim.web') 同族。"""
    return logging.getLogger(f"ming_sim.{name}")


@contextmanager
def log_swallow(context: str, *, level: int = logging.WARNING) -> Iterator[None]:
    """捕获代码块内异常并记日志，不 re-raise。

    用法（替代 except Exception: pass）：
        with log_swallow("计算 NPC 能力覆盖"):
            ability = foundation.ability100(name)

    语义与 except Exception: pass 一致（容错、继续执行），但失败时有日志痕迹。
    """
    try:
        yield
    except Exception:
        logging.getLogger("ming_sim.swallow").log(
            level, "容错吞咽（%s）", context, exc_info=True
        )
