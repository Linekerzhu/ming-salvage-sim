"""验证 mypy 配置存在且可加载（不跑全量 mypy，只验配置文件可解析）。"""

import unittest
from pathlib import Path


class MypyConfigTests(unittest.TestCase):
    def test_pyproject_toml_exists(self):
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "pyproject.toml").exists(),
                        "pyproject.toml 必须存在（承载 [tool.mypy] 配置）")

    def test_mypy_section_present(self):
        root = Path(__file__).resolve().parent.parent
        content = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.mypy]", content)
        self.assertIn("python_version", content)

    def test_pytest_section_present(self):
        root = Path(__file__).resolve().parent.parent
        content = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.pytest.ini_options]", content)


if __name__ == "__main__":
    unittest.main()
