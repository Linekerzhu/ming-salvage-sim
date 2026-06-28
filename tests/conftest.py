"""pytest 全局 fixture：每个测试前重置进程级全局状态。

G2 改进：此前无 conftest.py，TOKEN_STATS（token 计数全局 dict）和 METRICS
（LLM 调用计数）从不重置——未来有测试断言这些值时会 order-dependent flaky。

本 fixture autouse=True，每个测试自动运行，无需手动调 reset。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_global_state():
    """每个测试前重置进程级全局状态，保证测试隔离。"""
    # 重置 token 统计
    try:
        from ming_sim.token_stats import TOKEN_STATS
        TOKEN_STATS.clear()
    except ImportError:
        pass

    # 重置 metrics 计数器
    try:
        from ming_sim.metrics import reset_metrics
        reset_metrics()
    except ImportError:
        pass

    yield  # 测试运行

    # 测试后也清理（防止测试内写入泄漏到下一个）
    try:
        from ming_sim.token_stats import TOKEN_STATS
        TOKEN_STATS.clear()
    except ImportError:
        pass
    try:
        from ming_sim.metrics import reset_metrics
        reset_metrics()
    except ImportError:
        pass
