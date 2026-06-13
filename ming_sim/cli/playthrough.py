"""真实 LLM 对局验证 harness：用配置的模型跑多月真实结算，回答 headless 答不了的问题。

与无头试跑（dryrun.py）互补：dryrun 零-LLM 验机制咬合；本工具走**真实月末结算**
（resolve_directives：LLM 写邸报 → extractor 提取并施加 economy_moves/metric_delta/
issue 等），用以——
  1) 验证内库/民心/财政在『有可裁量开支与危机消耗』的真实路径下是否仍失衡（dryrun 判其为无头假象）；
  2) 拿 token 基线（每月/每局 prompt+completion）；
  3) 确认三缺口修复在 LLM 路径里顺（伏笔萌发、势耦合、截留后果与 extractor 不打架）。

跑法：  set -a; . ./.env; set +a; python -m ming_sim.cli.playthrough --months 5 --seed 7
        （需 OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL 在环境里）
"""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from ming_sim import lifecycle, timeflow
from ming_sim.cli.dryrun import DryRun
from ming_sim.content import GameContent
from ming_sim.decree import resolve_directives
from ming_sim.llm_config import load_llm_config
from ming_sim.llm_model import create_agno_db
from ming_sim.session import _bind_all_content
from ming_sim.token_stats import TOKEN_STATS, install_token_stats_patch, print_token_summary
from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int


class Playthrough(DryRun):
    """复用 DryRun 的 bootstrap/自动皇帝/遥测，但月末走真实 LLM 结算。"""

    def __init__(self, *, months: int, seed: int, directives_per_month: int, quiet: bool):
        super().__init__(months=months, seed=seed,
                         directives_per_month=directives_per_month, quiet=quiet)
        self.llm_config = load_llm_config(
            os.environ.get("OPENAI_BASE_URL", ""),
            os.environ.get("OPENAI_MODEL", ""),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            timeout_seconds=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "180") or 180),
        )
        self.agno_db = create_agno_db(self.db.path)
        self.content = GameContent.load()
        _bind_all_content(self.content)  # extractor/issues 需 GameContent 注入
        self.month_tokens: List[tuple] = []  # (turn, prompt, completion)

    def _decree_text(self) -> str:
        """把本回合 confirmed 旨意拼成诏书正文喂 simulator（验证用，省去拟诏润色）。"""
        rows = self.db.conn.execute(
            "SELECT text FROM turn_directives WHERE turn=? AND status='confirmed'",
            (self.state.turn,)).fetchall()
        if not rows:
            return ""
        return "奉天承运皇帝诏曰：\n" + "\n".join(f"一、{r['text']}" for r in rows)

    def _token_totals(self) -> tuple:
        p = sum(b.get("prompt", 0) for b in TOKEN_STATS.values())
        c = sum(b.get("completion", 0) for b in TOKEN_STATS.values())
        return p, c

    def run(self) -> None:
        install_token_stats_patch()
        self._snapshot()  # 开局（turn1 结算前）作为基线
        for i in range(self.months):
            turn_no = self.state.turn
            self._issue_directives()
            self._advance_full_month()
            p0, c0 = self._token_totals()
            directives = self.db.conn.execute(
                "SELECT * FROM turn_directives WHERE turn=? AND status='confirmed'",
                (turn_no,)).fetchall()
            decree = self._decree_text()
            try:
                if directives:
                    resolve_directives(self.state, self.db, self.agno_db, self.llm_config,
                                       directives, decree, content=self.content)
                else:
                    self._rollover_month()
            except Exception as exc:
                self.invariant_failures.append(f"turn{turn_no} resolve 异常：{exc}")
                self._rollover_month()
            p1, c1 = self._token_totals()
            # 结算后再采（捕捉 extractor 对国库/内库/民心 的真实落地）
            self._check_invariants()
            self._scan_gaps_this_month()
            self._snapshot()
            self.month_tokens.append((turn_no, p1 - p0, c1 - c0))
            print(f"  ▸ turn{turn_no} 月结完成"
                  f"（本月 token：prompt {p1-p0:,} / completion {c1-c0:,}）")
        self._scan_gaps_final()

    def report(self) -> None:
        super().report()
        print("\n── Token 基线（真实结算）" + "─" * 44)
        tp = sum(m[1] for m in self.month_tokens)
        tc = sum(m[2] for m in self.month_tokens)
        n = max(1, len(self.month_tokens))
        print(f"  全局：prompt {tp:,} + completion {tc:,} = {tp+tc:,} tokens")
        print(f"  每月均：prompt {tp//n:,} / completion {tc//n:,} / 合计 {(tp+tc)//n:,}")
        print_token_summary()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="大明1628 真实 LLM 对局验证")
    ap.add_argument("--months", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--directives", type=int, default=3)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("缺 OPENAI_API_KEY。先 `set -a; . ./.env; set +a` 再跑。")
    pt = Playthrough(months=args.months, seed=args.seed,
                     directives_per_month=args.directives, quiet=args.quiet)
    pt.run()
    pt.report()
    return 1 if pt.invariant_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
