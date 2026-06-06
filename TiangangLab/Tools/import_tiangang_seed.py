#!/usr/bin/env python3
"""Extract the minimal Tiangang seed used by the isolated iOS prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_NAMES = ["韩爌", "毕自严", "崔呈秀", "曹化淳", "袁崇焕", "祖大寿"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build iOS Tiangang seed JSON")
    parser.add_argument("--source", required=True, help="Path to old Ming content/npc_tiangang.json")
    parser.add_argument("--output", required=True, help="Output path inside the iOS prototype")
    parser.add_argument("--names", nargs="*", default=DEFAULT_NAMES, help="NPC names to extract")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    data = json.loads(source.read_text(encoding="utf-8"))

    meta = data.get("meta", {})
    npcs = data.get("npcs", {})
    selected = {}
    missing = []
    for name in args.names:
        item = npcs.get(name)
        if item is None:
            missing.append(name)
        else:
            selected[name] = item
    if missing:
        raise SystemExit(f"Missing NPCs in source: {', '.join(missing)}")

    seed = {
        "source": str(source),
        "selected_npcs": args.names,
        "meta": {
            "version": meta.get("version", ""),
            "source": meta.get("source", ""),
            "hidden_by_default": meta.get("hidden_by_default", True),
            "growth_enabled": meta.get("growth_enabled", False),
            "dimensions": meta.get("dimensions", []),
        },
        "npcs": selected,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output} with {len(selected)} NPCs and {len(seed['meta']['dimensions'])} dimensions.")


if __name__ == "__main__":
    main()
