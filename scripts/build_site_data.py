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

import urllib.request

KEEP = ("commits_today", "prs_opened_1h", "prs_merged_1h", "pr_velocity", "last_activity", "history", "slug", "label", "tagline", "accent", "description", "url", "created_at", "pushed_at", "stars", "forks",
        "open_issues", "open_prs", "merged_today", "total_prs", "release", "contributors", "commits_24h",
        "commits_by_hour", "commits_capped", "leaders", "events", "progress")


def previous_build() -> dict:
    """Last deployed live.json, so star histories survive the stateless Actions runs."""
    url = render_site_url() + "data/live.json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "repo-live-build"}), timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print("no previous build:", e)
        return {}


def render_site_url() -> str:
    import render_site
    return render_site.SITE_URL


def merge_hist(old: list, new: list, keep: int = 1500) -> list:
    seen, out = set(), []
    for p in sorted((old or []) + (new or []), key=lambda x: x[0]):
        if p[0] not in seen:
            seen.add(p[0]); out.append(p)
    return out[-keep:]


def main(out_path: str):
    prev = previous_build()
    prev_hist = {r["slug"]: r.get("history") or [] for r in prev.get("repos", [])}
    prev_radar = (prev.get("intel") or {}).get("radar_hist") or {}
    server.INTEL_HIST["radar"] = {k: list(v) for k, v in prev_radar.items()}
    repos = []
    for cfg in server.REPOS:
        r = server.poll_repo(cfg)
        r["events"] = r["events"][:12]
        r["history"] = merge_hist(prev_hist.get(r["slug"]), r.get("history"))
        repos.append({k: r.get(k) for k in KEEP})
    pw = server.poll_portwatch()
    try:
        watch = server.poll_watch()
    except Exception as e:  # news is optional
        print("watch failed:", e)
        watch = None
    try:
        intel = server.poll_intel(pw, server._intel_candidates())
        intel["radar_hist"] = {k: v[-300:] for k, v in server.INTEL_HIST["radar"].items()}
    except Exception as e:
        print("intel failed:", e)
        intel = prev.get("intel")
    exe = pw.get("exe") or {}
    counter = dict(server.COUNTER)
    if exe.get("status") == "released" and exe.get("releasedAt"):
        counter["start"] = exe["releasedAt"]
    data = {"built_at": datetime.now(timezone.utc).isoformat(), "repos": repos, "portwatch": pw,
            "watch": watch, "intel": intel, "counter": counter}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(data, separators=(",", ":")))
    import render_site
    n = render_site.render(data)
    print(f"rendered site/index.html ({n} bytes), sitemap.xml, robots.txt")
    print(f"wrote {out_path}: {len(repos)} repos, {len(pw.get('ranked', []))} ranked, "
          f"{len((watch or {}).get('items', []))} watch items")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "site" / "data" / "live.json"))
