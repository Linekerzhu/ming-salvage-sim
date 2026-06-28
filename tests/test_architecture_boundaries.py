"""架构分层机器检查：把 engineering-architecture.md 的散文不变量变成可执行的契约。

此前分层只是 mermaid + 散文，无任何机器检查——重构可静默破坏层边界。
本测试用 ast.parse 扫 ming_sim/*.py 的 import 图，断言可机器化的不变量：

- TR-LAYER-02：机制层（ming_sim 多数模块）不得 import fastapi/starlette（Web 入口是 *_api.py 的专属层）
- TR-LAYER-01：L0 基础层（models/exceptions/paths/constants）不得 import 上层 ming_sim 机制模块
- 无循环 import：module_dependency_order() 不抛 RuntimeError

零 LLM，零 DB。纯静态分析。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

MING_SIM = Path(__file__).resolve().parent.parent / "ming_sim"

# Web 入口层白名单：这些 *_api.py 是 Web 路由 shim，允许 import fastapi。
# 它们是分层图中 WebRoutes 层在 ming_sim/ 内的唯一合法落点（见 architecture-review C2）。
_WEB_LAYER_ALLOWLIST = {"assignment_api", "quest_api"}

# L0 基础层：最底层，不应 import 任何上层 ming_sim 机制/服务/管道模块。
# 仅允许 L0 内部互相引用（constants→paths, models→constants）。
_L0_FOUNDATION = {"models", "exceptions", "paths", "constants"}


def _imports_in(source: str) -> list[tuple[str, str]]:
    """提取 (模块, 名称) 对：返回所有 import 语句的顶层模块路径。

    返回 [(full_module, first_segment), ...]：full_module 是 'ming_sim.foo.bar'，
    first_segment 是 'foo'（用于快速判断是否跨层）。
    """
    tree = ast.parse(source)
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append((node.module, node.module.split(".")[0]))
    return out


class WebLayerBoundaryTests(unittest.TestCase):
    """TR-LAYER-02：机制层不得 import fastapi/starlette（Web 入口是 *_api.py 专属）。"""

    def test_only_api_shims_import_fastapi(self):
        offenders: list[str] = []
        for py in MING_SIM.glob("*.py"):
            mod = py.stem
            if mod in _WEB_LAYER_ALLOWLIST:
                continue
            if mod == "__init__":
                continue
            source = py.read_text(encoding="utf-8")
            for full, _seg in _imports_in(source):
                if full.split(".")[0] in ("fastapi", "starlette"):
                    offenders.append(f"{mod}.py imports {full}（机制层不得依赖 Web 框架）")
        self.assertEqual(offenders, [],
                         "机制层不得 import fastapi/starlette——Web 入口是 *_api.py 专属层：\n"
                         + "\n".join(offenders))


class FoundationLayerBoundaryTests(unittest.TestCase):
    """TR-LAYER-01：L0 基础层不得 import 上层 ming_sim 机制/服务/管道模块。"""

    def test_l0_modules_do_not_import_upper_layers(self):
        offenders: list[str] = []
        for name in _L0_FOUNDATION:
            py = MING_SIM / f"{name}.py"
            if not py.exists():
                continue
            source = py.read_text(encoding="utf-8")
            for full, _seg in _imports_in(source):
                # ming_sim.xxx：xxx 不得是上层模块（只能是 L0 内部）
                if full.startswith("ming_sim."):
                    target = full.split(".")[1]
                    if target not in _L0_FOUNDATION:
                        offenders.append(
                            f"{name}.py imports ming_sim.{target}（L0 基础层不得依赖上层）"
                        )
        self.assertEqual(offenders, [],
                         "L0 基础层（models/exceptions/paths/constants）不得 import 上层模块：\n"
                         + "\n".join(offenders))


class RegistryDependencyCycleTests(unittest.TestCase):
    """无循环 import：module_dependency_order() 必须不抛 RuntimeError（检测依赖环）。"""

    def test_module_dependency_graph_is_acyclic(self):
        from ming_sim.module_registry import module_dependency_order
        try:
            order = module_dependency_order(enabled_only=False)
        except RuntimeError as exc:
            self.fail(f"module 依赖图存在环：{exc}")
        self.assertIsInstance(order, tuple)
        self.assertGreater(len(order), 0, "registry 应有至少一个模块")


if __name__ == "__main__":
    unittest.main()
