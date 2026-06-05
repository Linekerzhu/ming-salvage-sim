#!/usr/bin/env python3
"""批量生人物 DNA 参考图与立绘。

默认读取 ``content/portrait_generation_manifest.json``，调用 302.ai nano-banana-2。
key 走 .env / 环境变量 NANO_BANANA_API_KEY，绝不写入产物。可重跑：已存在的 png 跳过。

用法：
  .venv/bin/python scripts/export_portrait_prompts.py
  .venv/bin/python scripts/gen_portraits.py                  # 跑全部缺失立绘
  .venv/bin/python scripts/gen_portraits.py --kind dna        # 跑 DNA 四视图
  .venv/bin/python scripts/gen_portraits.py --kind both       # DNA + 立绘
  .venv/bin/python scripts/gen_portraits.py --kind both --replace  # 重绘并替换旧图
  .venv/bin/python scripts/gen_portraits.py --only 王承恩      # 只跑名称/文件名含此串的
  .venv/bin/python scripts/gen_portraits.py --only 王承恩 --only 曹化淳
  .venv/bin/python scripts/gen_portraits.py --limit 3         # 只跑前 N 张（试水）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ming_sim.portraits import (  # noqa: E402
    DNA_SHEET_ASPECT_RATIO,
    PORTRAIT_ASPECT_RATIO,
    nano_banana_generate_png,
    normalize_portrait_png,
)

MANIFEST = ROOT / "content" / "portrait_generation_manifest.json"
OUT = ROOT / "web" / "public" / "portraits"
DNA_OUT = OUT / "_dna"
REFERENCE_ROOT = Path(os.environ.get("MING_PORTRAIT_REFERENCE_DIR") or (Path.home() / "Downloads"))


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_reference(ref: str) -> str:
    clean = str(ref).strip()
    if not clean or clean.startswith(("data:image", "http://", "https://")):
        return clean
    if clean.startswith("reference://"):
        return str(REFERENCE_ROOT / clean.removeprefix("reference://"))
    path = Path(clean)
    if path.is_absolute():
        return str(path)
    return str(ROOT / path)


def parse_entries(kind: str) -> list[tuple[str, Path, str, str, tuple[str, ...]]]:
    """返回 [(name, out_path, prompt, aspect_ratio, refs), ...]，按 manifest 出现顺序。"""
    if not MANIFEST.exists():
        raise SystemExit("缺 content/portrait_generation_manifest.json；请先运行 scripts/export_portrait_prompts.py")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries: list[tuple[str, Path, str, str, tuple[str, ...]]] = []
    for rec in data.get("portraits", []):
        name = str(rec.get("name") or "")
        if kind in {"dna", "both"}:
            filename = str(rec.get("dna_filename") or f"dna_{name}.png")
            prompt = str(rec.get("dna_prompt") or "")
            refs = tuple(resolve_reference(str(item)) for item in (rec.get("dna_reference_images") or []) if str(item).strip())
            if filename and prompt:
                entries.append((name, DNA_OUT / filename, prompt, DNA_SHEET_ASPECT_RATIO, refs))
        if kind in {"portrait", "both"}:
            filename = str(rec.get("filename") or "")
            prompt = str(rec.get("prompt") or "")
            refs = tuple(resolve_reference(str(item)) for item in (rec.get("reference_images") or []) if str(item).strip())
            if filename and prompt:
                entries.append((name, OUT / filename, prompt, PORTRAIT_ASPECT_RATIO, refs))
    return entries


def gen_one(
    prompt: str,
    timeout: int = 180,
    aspect_ratio: str = PORTRAIT_ASPECT_RATIO,
    reference_images: tuple[str, ...] = (),
) -> bytes:
    raw = nano_banana_generate_png(
        prompt,
        timeout=timeout,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images,
    )
    if aspect_ratio == DNA_SHEET_ASPECT_RATIO:
        return normalize_portrait_png(
            raw,
            target_width=768,
            target_aspect_ratio=DNA_SHEET_ASPECT_RATIO,
            cutout_background=False,
        )
    return normalize_portrait_png(
        raw,
        target_width=512,
        target_aspect_ratio=PORTRAIT_ASPECT_RATIO,
        cutout_background=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["portrait", "dna", "both"], default="portrait")
    ap.add_argument("--only", action="append", default=[], help="可重复；只跑姓名或文件名含任一子串的")
    ap.add_argument("--limit", type=int, default=0, help="最多跑 N 张")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--retries", type=int, default=2, help="单张失败重试次数")
    ap.add_argument("--workers", type=int, default=1, help="并发线程数")
    ap.add_argument("--replace", action="store_true", help="覆盖已存在的静态立绘/DNA；用于发布前统一替换旧系统图")
    args = ap.parse_args()

    load_dotenv()
    key = os.environ.get("NANO_BANANA_API_KEY", "").strip() or os.environ.get("OPENAI_IMAGE_KEY", "").strip()
    if not key:
        raise SystemExit("缺 NANO_BANANA_API_KEY 环境变量（可写入 .env）")

    OUT.mkdir(parents=True, exist_ok=True)
    DNA_OUT.mkdir(parents=True, exist_ok=True)
    entries = parse_entries(args.kind)
    if args.only:
        needles = [item for item in args.only if item]
        entries = [
            e for e in entries
            if any(needle in e[0] or needle in e[1].name for needle in needles)
        ]

    todo = entries if args.replace else [(name, out, prompt, aspect, refs) for name, out, prompt, aspect, refs in entries if not out.exists()]
    action = "重绘覆盖" if args.replace else "缺图生成（已存在跳过）"
    print(f"解析 {len(entries)} 条，{action}：待生 {len(todo)} 张")
    if args.limit:
        todo = todo[: args.limit]
        print(f"--limit {args.limit}：本次只跑 {len(todo)} 张")

    n = len(todo)
    done_ct = [0]
    lock = threading.Lock()

    def work(item: tuple[str, Path, str, str, tuple[str, ...]]) -> bool:
        name, out, prompt, aspect, refs = item
        out.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, args.retries + 2):
            t0 = time.time()
            try:
                png = gen_one(prompt, args.timeout, aspect, refs)
                tmp = out.with_name(out.name + ".tmp")
                tmp.write_bytes(png)
                tmp.replace(out)
                dt = time.time() - t0
                with lock:
                    done_ct[0] += 1
                    ref_note = f" refs={len(refs)}" if refs else ""
                    print(f"[{done_ct[0]}/{n}] {name} -> {out.name}  {len(png)//1024}KB  {dt:.0f}s{ref_note}  OK", flush=True)
                return True
            except Exception as e:
                dt = time.time() - t0
                msg = str(e)[:160]
                with lock:
                    if attempt <= args.retries:
                        print(f"[{out.name}] {dt:.0f}s  FAIL({attempt}) {msg} — 重试", flush=True)
                    else:
                        print(f"[{out.name}] {dt:.0f}s  FAIL final {msg}", flush=True)
                if attempt <= args.retries:
                    time.sleep(3)
        return False

    if args.workers <= 1:
        results = [work(it) for it in todo]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(work, todo))

    ok = sum(results)
    print(f"\n完成：成功 {ok}，失败 {n - ok}，剩余缺 {n - ok} 张可重跑", flush=True)


if __name__ == "__main__":
    main()
