#!/usr/bin/env python3
"""Build site/data/live.json for the public website (run by GitHub Actions every 15 min, or locally).

Reuses the stream server's pollers so the site and the stream always show the same numbers.
Uses GITHUB_TOKEN from the environment (Actions provides one automatically).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402

KEEP = ("slug", "label", "tagline", "accent", "description", "url", "created_at", "pushed_at", "stars", "forks",
        "open_issues", "open_prs", "merged_today", "total_prs", "release", "contributors", "commits_24h",
        "commits_by_hour", "commits_capped", "leaders", "events", "progress")


def main(out_path: str):
    repos = []
    for cfg in server.REPOS:
        r = server.poll_repo(cfg)
        r["events"] = r["events"][:12]
        repos.append({k: r.get(k) for k in KEEP})
    pw = server.poll_portwatch()
    try:
        watch = server.poll_watch()
    except Exception as e:  # news is optional
        print("watch failed:", e)
        watch = None
    exe = pw.get("exe") or {}
    counter = dict(server.COUNTER)
    if exe.get("status") == "released" and exe.get("releasedAt"):
        counter["start"] = exe["releasedAt"]
    data = {"built_at": datetime.now(timezone.utc).isoformat(), "repos": repos, "portwatch": pw,
            "watch": watch, "counter": counter}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(data, separators=(",", ":")))
    import render_site
    n = render_site.render(data)
    print(f"rendered site/index.html ({n} bytes), sitemap.xml, robots.txt")
    print(f"wrote {out_path}: {len(repos)} repos, {len(pw.get('ranked', []))} ranked, "
          f"{len((watch or {}).get('items', []))} watch items")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "site" / "data" / "live.json"))
