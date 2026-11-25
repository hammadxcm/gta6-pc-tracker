#!/usr/bin/env python3
"""Repo Live: polls GitHub for a set of repos and serves a live dashboard.

Usage:
    python3 server.py   # GitHub token read from ~/.config/repo-live/github_token (or $GITHUB_TOKEN)
    open http://localhost:8080

Stdlib only. State is kept in memory and star history in data/history.json.
"""
import http.client
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPOS = [
    {"slug": "Marvisak/VIctory", "label": "VIctory", "tagline": "GTA VI → PC", "accent": "#ff3d81"},
    {"slug": "xcl-dev/xcl", "label": "XCL", "tagline": "Xbox Series → Windows", "accent": "#36d25e"},
]

# Elapsed counter shown on stream. Start time taken from portwatch-vi.xyz (data/tracker.json -> exe.releasedAt).
COUNTER = {"label": "Since the executable dropped", "start": "2026-11-23T14:36:00Z",
           "note": "23 Nov · 14:36 UTC"}

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
HISTORY_FILE = DATA / "history.json"

SECRETS = Path(os.environ.get("REPO_LIVE_SECRETS", Path.home() / ".config" / "repo-live"))


def _secret(env: str, fname: str) -> str:
    """Env var wins; otherwise read the private file (never logged or served)."""
    v = os.environ.get(env, "").strip()
    if not v and (SECRETS / fname).is_file():
        v = (SECRETS / fname).read_text().strip()
    return v


TOKEN = _secret("GITHUB_TOKEN", "github_token")
PORT = int(os.environ.get("PORT", "8080"))
# ~20 API calls per poll -> unauthenticated (60/h) must poll slowly. ETag 304s help.
POLL = int(os.environ.get("POLL_SECONDS", "30" if TOKEN else "900"))

_etags: dict[str, tuple[str, object, dict]] = {}
_lock = threading.Lock()
STATE: dict = {"repos": {}, "updated_at": None, "poll_seconds": POLL, "rate": {}, "error": None}


# --------------------------------------------------------------------------- http
def gh(path: str, raw: bool = False, _tries: int = 3):
    """gh() with retries on transient network errors (resets, timeouts, 5xx)."""
    for attempt in range(_tries):
        try:
            return _gh(path, raw)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == _tries - 1:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, http.client.HTTPException):  # incl. IncompleteRead
            if attempt == _tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))


def _gh(path: str, raw: bool = False):
    """GET from api.github.com (or a full URL) with ETag caching. Returns (body, headers)."""
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    headers = {"User-Agent": "repo-live-dashboard", "Accept": "application/vnd.github+json"}
    if TOKEN and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {TOKEN}"
    cached = _etags.get(url)
    if cached:
        headers["If-None-Match"] = cached[0]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            hdrs = dict(r.headers)
            data = body if raw else json.loads(body)
            if r.headers.get("ETag"):
                _etags[url] = (r.headers["ETag"], data, hdrs)
            if "X-RateLimit-Remaining" in hdrs:
                STATE["rate"] = {"remaining": hdrs.get("X-RateLimit-Remaining"),
                                 "limit": hdrs.get("X-RateLimit-Limit"),
                                 "reset": hdrs.get("X-RateLimit-Reset")}
            return data, hdrs
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached:
            return cached[1], cached[2]
        raise


def last_page_count(hdrs: dict, items) -> int:
    link = hdrs.get("Link", "") or hdrs.get("link", "")
    m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', link)
    return int(m.group(1)) if m else len(items)


# ------------------------------------------------------------------ README parse
NUM = r"\d{1,3}(?:[,   ]\d{3})+|\d+"
FRACTION = re.compile(rf"(?<![\w.])({NUM})\s*(?:of|/)\s*({NUM})(?![\w.])")
STOP = {"the", "a", "of", "and", "its", "all", "to", "in", "on", "with", "are", "is", "=", "–", "-"}


def _n(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def _clean(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    return re.sub(r"[*`_]", "", s).strip()


def parse_readme(md: str) -> dict:
    """Extract feature tables (done/partial) and 'X of Y' measurements from a README."""
    lines = md.splitlines()
    features, fractions, section = [], [], ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            section = _clean(line.lstrip("#"))
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [_clean(c).lower() for c in line.strip().strip("|").split("|")]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [_clean(c) for c in lines[i].strip().strip("|").split("|")]
                row = dict(zip(header, cells))
                score = _score(header, row, cells)
                if score is not None:
                    detail = row.get("what works") or row.get("today") or row.get("what it does") or row.get("state", "")
                    features.append({"name": cells[0][:60], "section": section, "score": score,
                                     "detail": detail[:140]})
                i += 1
            continue
        i += 1

    seen = set()
    for m in FRACTION.finditer(md):
        num, den = _n(m.group(1)), _n(m.group(2))
        if den < 50 or num > den or (num, den) in seen:
            continue
        seen.add((num, den))
        before = re.findall(r"[A-Za-z][\w/\-]*", md[max(0, m.start() - 40):m.start()])
        tail = re.split(r"[|,;(]", md[m.end():m.end() + 60].replace("\n", " "))[0]
        after = [w for w in re.findall(r"[A-Za-z][\w/\-']*", tail) if w.lower() not in STOP]
        if after and after[0].endswith("s"):
            label = after[0]
        elif before and before[-1].lower() not in STOP:
            label = before[-1]
        else:
            label = after[0] if after else ""
        fractions.append({"label": label[:32] or "items", "num": num, "den": den})

    done = sum(f["score"] for f in features)
    return {"features": features, "fractions": fractions[:6],
            "completeness": round(done / len(features), 4) if features else None}


def _score(header, row, cells):
    joined = " ".join(cells)
    if "✅" in joined:
        return 1.0
    if "🚧" in joined or "⚠" in joined or "🟡" in joined:
        return 0.5
    if "❌" in joined:
        return 0.0
    if "needs" in header:
        need = row.get("needs", "").strip()
        return 1.0 if need in {"–", "-", "—", ""} else 0.5
    state = row.get("state")
    if state is not None and len(header) <= 3:
        return 1.0 if re.match(r"works|done|yes", state, re.I) else 0.5
    return None


# ------------------------------------------------------------------------ polling
def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return {}


HISTORY = load_history()


PR_DETAILS: dict[tuple, tuple] = {}


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def search_count(q: str) -> int | None:
    try:
        d, _ = gh(f"/search/issues?q={q}&per_page=1")
        return d.get("total_count")
    except Exception as e:
        print("search failed:", q, e, flush=True)
        return None


def pr_detail(slug: str, pr: dict) -> dict:
    key = (slug, pr["number"])
    hit = PR_DETAILS.get(key)
    if hit and hit[0] == pr["updated_at"]:
        return hit[1]
    try:
        d, _ = gh(f"/repos/{slug}/pulls/{pr['number']}")
        info = {k: d.get(k) for k in ("additions", "deletions", "changed_files", "commits", "comments")}
    except Exception:
        info = hit[1] if hit else {}
    PR_DETAILS[key] = (pr["updated_at"], info)
    return info


def poll_repo(cfg: dict) -> dict:
    slug = cfg["slug"]
    now = datetime.now(timezone.utc)
    repo, _ = gh(f"/repos/{slug}")
    commits, _ = gh(f"/repos/{slug}/commits?per_page=100")
    if TOKEN:  # page further back so "commits 24h" isn't capped at 100
        since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for page in (2, 3, 4, 5):
            if len(commits) < 100 * (page - 1):
                break
            more, _ = gh(f"/repos/{slug}/commits?per_page=100&page={page}&since={since}")
            commits += more
    pulls, _ = gh(f"/repos/{slug}/pulls?state=all&sort=created&direction=desc&per_page=100")
    contrib, ch = gh(f"/repos/{slug}/contributors?per_page=1&anon=1")
    try:
        rel, _ = gh(f"/repos/{slug}/releases/latest")
        release = {"tag": rel.get("tag_name"), "name": rel.get("name"), "at": rel.get("published_at")}
    except Exception:
        release = None
    today = now.strftime("%Y-%m-%d")
    open_prs = search_count(f"repo:{slug}+is:pr+is:open")
    merged_today = search_count(f"repo:{slug}+is:pr+is:merged+merged:>={today}")
    total_prs = search_count(f"repo:{slug}+is:pr")
    readme, _ = gh(f"https://raw.githubusercontent.com/{slug}/{repo['default_branch']}/README.md", raw=True)

    # commits: hourly histogram, feed, leaderboard
    hours, authors, avatars, feed = Counter(), Counter(), {}, []
    for c in commits:
        when = _ts(c["commit"]["author"]["date"])
        login = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        age_h = int((now - when).total_seconds() // 3600)
        if 0 <= age_h < 24:
            hours[age_h] += 1
            if not c["commit"]["message"].startswith("Merge "):
                authors[login] += 1
        if (c.get("author") or {}).get("avatar_url"):
            avatars[login] = c["author"]["avatar_url"]
        feed.append({"sha": c["sha"][:7], "msg": c["commit"]["message"].splitlines()[0][:110],
                     "author": login, "avatar": avatars.get(login), "date": c["commit"]["author"]["date"]})

    # PR events: opened / merged / closed, newest first
    events = []
    for p in pulls:
        base = {"n": p["number"], "title": p["title"][:140], "by": p["user"]["login"],
                "avatar": p["user"]["avatar_url"], "draft": p.get("draft", False), "url": p["html_url"]}
        events.append({**base, "kind": "opened", "at": p["created_at"]})
        if p.get("merged_at"):
            events.append({**base, "kind": "merged", "at": p["merged_at"]})
        elif p.get("closed_at"):
            events.append({**base, "kind": "closed", "at": p["closed_at"]})
    events.sort(key=lambda e: e["at"], reverse=True)
    events = events[:24]
    by_num = {p["number"]: p for p in pulls}
    budget = 12 if TOKEN else 2  # per-PR detail calls; cached by updated_at
    for e in events:
        key = (slug, e["n"])
        cached = PR_DETAILS.get(key)
        if cached and cached[0] == by_num[e["n"]]["updated_at"]:
            e.update(cached[1])
        elif budget > 0:
            budget -= 1
            e.update(pr_detail(slug, by_num[e["n"]]))
        elif cached:
            e.update(cached[1])

    # PR velocity: opened / merged per hour over the last 12h, plus "today" and "last hour" counts
    try:
        closed, _ = gh(f"/repos/{slug}/pulls?state=closed&sort=updated&direction=desc&per_page=100")
    except Exception:
        closed = []
    by_num = {p["number"]: p for p in closed}
    by_num.update({p["number"]: p for p in pulls})
    allp = list(by_num.values())
    # how far back each sample reaches (full 100 => window is only as old as its oldest item)
    open_cov = 12 if len(pulls) < 100 else min(12, int((now - _ts(pulls[-1]["created_at"])).total_seconds() // 3600))
    merge_cov = 12 if len(closed) < 100 else min(12, int((now - _ts(closed[-1]["updated_at"])).total_seconds() // 3600))
    vel_open, vel_merge = [0] * 12, [0] * 12
    for p in allp:
        for key, arr in (("created_at", vel_open), ("merged_at", vel_merge)):
            if p.get(key):
                h = int((now - _ts(p[key])).total_seconds() // 3600)
                if 0 <= h < 12:
                    arr[11 - h] += 1
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    commits_today = sum(1 for c in commits if _ts(c["commit"]["author"]["date"]) >= today0)
    opened_1h = sum(1 for p in pulls if now - _ts(p["created_at"]) < timedelta(hours=1))
    merged_1h = sum(1 for p in allp if p.get("merged_at") and now - _ts(p["merged_at"]) < timedelta(hours=1))
    last_activity = max([repo["pushed_at"]] + [e["at"] for e in events[:1]])

    # Fallbacks when GitHub search rejects a repo (HTTP 422 seen for the old ivall/xcl name after the repo moved to xcl-dev/xcl): count from the pulls API
    def _pr_count(state):
        try:
            items, hdrs = gh(f"/repos/{slug}/pulls?state={state}&per_page=1")
            return last_page_count(hdrs, items)
        except Exception:
            return None
    if open_prs is None:
        open_prs = _pr_count("open")
    if total_prs is None:
        total_prs = _pr_count("all")
    if merged_today is None:
        try:
            got, page = [], 1
            while page <= 5:
                batch, _ = gh(f"/repos/{slug}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}")
                got += batch
                if len(batch) < 100 or _ts(batch[-1]["updated_at"]) < today0:
                    break
                page += 1
            merged_today = sum(1 for p in got if p.get("merged_at") and _ts(p["merged_at"]) >= today0)
        except Exception:
            merged_today = None

    merged_recent = [p for p in pulls if p.get("merged_at")]
    merged_recent.sort(key=lambda p: p["merged_at"], reverse=True)

    hist = HISTORY.setdefault(slug, [])
    sample = [int(now.timestamp()), repo["stargazers_count"], repo["forks_count"]]
    if not hist or hist[-1][1:] != sample[1:] or sample[0] - hist[-1][0] > 600:
        hist.append(sample)
    del hist[:-2000]

    n_open = open_prs if open_prs is not None else 0
    return {
        **cfg,
        "description": repo.get("description"),
        "url": repo["html_url"],
        "created_at": repo["created_at"],
        "pushed_at": repo["pushed_at"],
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "open_issues": max(0, repo["open_issues_count"] - n_open),
        "open_prs": open_prs,
        "merged_today": merged_today,
        "total_prs": total_prs,
        "release": release,
        "contributors": last_page_count(ch, contrib),
        "commits_24h": sum(hours.values()),
        "commits_today": commits_today,
        "prs_opened_1h": opened_1h,
        "prs_merged_1h": merged_1h,
        "pr_velocity": {"opened": vel_open, "merged": vel_merge, "opened_cov_h": open_cov, "merged_cov_h": merge_cov},
        "last_activity": last_activity,
        "commits_by_hour": [hours.get(h, 0) for h in range(23, -1, -1)],  # oldest -> newest
        "commits_capped": len(commits) % 100 == 0 and sum(hours.values()) == len(commits),
        "leaders": [{"login": l, "n": n, "avatar": avatars.get(l)} for l, n in authors.most_common(5)],
        "recent_merges": [{"n": p["number"], "title": p["title"][:100], "by": p["user"]["login"],
                           "at": p["merged_at"]} for p in merged_recent[:10]],
        "events": events,
        "feed": feed[:12],
        "progress": parse_readme(readme),
        "history": hist[-300:],
    }


def poll_loop():
    while True:
        STATE["checking"] = True
        try:
            for cfg in REPOS:
                data = poll_repo(cfg)
                with _lock:
                    STATE["repos"][cfg["slug"]] = data
            with _lock:
                STATE["updated_at"] = datetime.now(timezone.utc).isoformat()
                STATE["error"] = None
                STATE["fails"] = 0
            HISTORY_FILE.write_text(json.dumps(HISTORY))
        except Exception as e:  # keep the stream alive on API hiccups
            STATE["fails"] = STATE.get("fails", 0) + 1
            msg = f"{type(e).__name__}: {e}"
            print(f"poll error ({STATE['fails']} in a row):", msg, flush=True)
            if STATE["fails"] >= 3:   # a single network blip shouldn't put a warning on the stream
                STATE["error"] = msg
        STATE["checking"] = False
        STATE["next_poll_at"] = (datetime.now(timezone.utc) + timedelta(seconds=POLL)).isoformat()
        time.sleep(POLL)



# ------------------------------------------------------------ portwatch-vi.xyz
# Live port-race data from https://portwatch-vi.xyz (data/tracker.json). Scoring mirrors
# their assets/js/scoring.js so ranks match the site. Strings are untrusted -> escaped in UI.
PORTWATCH_URL = os.environ.get("PORTWATCH_URL", "https://portwatch-vi.xyz/data/tracker.json")
PORTWATCH_POLL = int(os.environ.get("PORTWATCH_POLL", "120"))
# PortWatch scale (since 2026-11-24): a ladder up to Playable (50), then four quality tracks fill 50-100.
MILESTONES = [("groundwork", "Groundwork", 0), ("loads", "Exe runs", 10), ("boots", "First frame", 20),
              ("menu", "Main menu", 30), ("world", "In the world", 40), ("playable", "Playable", 50), ("complete", "Complete", 100)]
TICK = {k: at for k, _, at in MILESTONES}
LABEL = {k: l for k, l, _ in MILESTONES}
GW_WEIGHTS = {"loader": .2, "shaders": .28, "graphics": .32, "proven": .2}
STAB = {"unknown": 0, "constant": 0, "often": .25, "sometimes": .55, "rarely": .8, "stable": 1}
TRACK_W = {"story": .35, "render": .25, "perf": .2, "stability": .2}
MILESTONE_DESC = {
    "Groundwork": "Loader, system libraries, shader translation and graphics layer, built before the executable exists.",
    "Exe runs": "GTA VI's executable is mapped, its imports resolve and its own code starts running.",
    "First frame": "The game draws its first real frames: logos, a loading screen or the intro.",
    "Main menu": "The title screen or first menus are reached and respond to input.",
    "In the world": "Story mode loads into the game world and draws it, however broken.",
    "Playable": "You control a character in the world: move around, with the HUD or pause menu working.",
    "Complete": "The whole game can be finished, looking like the console, at 30 FPS or better, without crashing.",
}


def _c01(v):
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _ladder(v):
    return 1.0 if v is True else 0.0 if v in (False, None) else _c01(v)


def _stab(v):
    """Stability is a label ('rarely', 'stable', ...) or, since 2026-11-25, a number 0-1."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _c01(v)
    return STAB.get(v, 0)


def score_effort(e: dict) -> dict:
    """Mirror of portwatch-vi.xyz assets/js/scoring.js scoreEffort()."""
    gw = sum(w * _c01((e.get("groundwork") or {}).get(k)) for k, w in GW_WEIGHTS.items())
    gta = e.get("gta") or {}
    score = prev = TICK["loads"] * gw
    reached = "groundwork"
    for key in ("loads", "boots", "menu", "world", "playable"):
        v = _ladder(gta.get(key))
        if v >= 1:
            score = prev = TICK[key]
            reached = key
            continue
        if v > 0:
            score = max(score, prev + v * (TICK[key] - prev))
        break
    tracks = {"story": _c01(gta.get("story")), "render": _c01(gta.get("render")),
              "perf": _c01((float(gta.get("fps") or 0)) / 30), "stability": _stab(gta.get("stability"))}
    quality = sum(TRACK_W[k] * tracks[k] for k in TRACK_W)
    if reached == "playable":
        score = TICK["playable"] + quality * (TICK["complete"] - TICK["playable"])
        if quality >= 1:
            reached = "complete"
    ms = e.get("manualScore")   # PortWatch owner override
    if isinstance(ms, (int, float)):
        score = min(100.0, max(0.0, float(ms)))
    # JavaScript's Math.round(x*10)/10 rounds halves up; Python's round() rounds halves to even (57.25 -> 57.2, not 57.3)
    return {"score": math.floor(score * 10 + 0.5) / 10, "reached": reached, "reachedLabel": LABEL[reached], "tracks": tracks}


def poll_portwatch():
    d, _ = gh(PORTWATCH_URL)
    tracked = [e for e in d.get("efforts", []) if e.get("tier") == "tracked" and not e.get("nuked")]
    order = lambda r: (-r["score"], -((r["e"].get("stats") or {}).get("stars") or 0))
    everyone = sorted(({**score_effort(e), "e": e} for e in tracked), key=order)
    # PortWatch (since 2026-11-24): only public code is ranked; private builds are listed under what they build on,
    # unranked, but still count for the headline number.
    is_public = lambda e: isinstance(e.get("repo"), str) and "/" in e["repo"]
    ranked = [r for r in everyone if is_public(r["e"])]
    names = {e.get("id"): e.get("name") for e in d.get("efforts", [])}
    private = [{"name": str(r["e"].get("name", ""))[:40], "score": r["score"], "stage": r["reachedLabel"],
                "fps": (r["e"].get("gta") or {}).get("fps"), "byline": str(r["e"].get("byline") or "")[:90],
                "based_on": [names.get(b, b) for b in (r["e"].get("basedOn") or [])],
                "summary": str(r["e"].get("summary") or "")[:220],
                "highlights": [str(h)[:110] for h in (r["e"].get("highlights") or [])[:3]],
                "blockers": [str(h)[:110] for h in (r["e"].get("blockers") or [])[:3]],
                "verification": r["e"].get("verification")} for r in everyone if not is_public(r["e"])]
    repo_re = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    out = []
    for i, r in enumerate(ranked[:40]):
        e = r["e"]
        repo = e.get("repo") if repo_re.match(e.get("repo") or "") else None
        out.append({"rank": i + 1, "name": str(e.get("name", ""))[:40], "repo": repo,
                    "avatar": f"https://github.com/{repo.split('/')[0]}.png?size=80" if repo else None,
                    "score": r["score"], "stage": r["reachedLabel"], "approach": str(e.get("approach") or "")[:40],
                    "target": str(e.get("target") or "")[:40], "stars": (e.get("stats") or {}).get("stars"),
                    "verification": e.get("verification"), "summary": str(e.get("summary") or "")[:220],
                    "private": repo is None, "byline": str(e.get("byline") or "")[:80],
                    "fps": (e.get("gta") or {}).get("fps"),
                    "pushed_at": (e.get("stats") or {}).get("pushedAt"),
                    "commits7d": (e.get("stats") or {}).get("commits7d"),
                    "proven_on": str(e.get("provenOn") or "")[:100],
                    "assessed_at": e.get("assessedAt"),
                    "highlights": [str(h)[:110] for h in (e.get("highlights") or [])[:3]],
                    "blockers": [str(h)[:110] for h in (e.get("blockers") or [])[:3]]})
    top3 = everyone[:3]
    furthest = max((next(i for i, m in enumerate(MILESTONES) if m[0] == r["reached"]) for r in everyone), default=0)
    head = everyone[0] if everyone else None
    exe = d.get("exe") or {}
    return {
        "updated": d.get("updated"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "exe": {k: exe.get(k) for k in ("status", "releasedAt", "expectedAt", "by")},
        "leader": out[0] if out else None,   # top PUBLIC project (ranked #1)
        "headline": {"name": str(head["e"].get("name", ""))[:40], "score": head["score"], "stage": head["reachedLabel"],
                     "private": not is_public(head["e"])} if head else None,   # top of everyone, as PortWatch's headline
        "private": private,
        "top3": round(sum(r["score"] for r in top3) / len(top3), 1) if top3 else 0,
        "furthest": MILESTONES[furthest][1],
        "tracked": len(tracked),
        "watched": len(d.get("efforts", [])),
        "milestones": [{"label": l, "at": at} for _, l, at in MILESTONES],
        "milestone_desc": MILESTONE_DESC,
        "nuked": [{"name": str(e.get("name", ""))[:40], "repo": e.get("repo")} for e in d.get("efforts", []) if e.get("nuked")],
        "ours": [r["slug"] for r in REPOS],
        "ranked": out,
        "history": [{k: h.get(k) for k in ("date", "leader", "top3", "tracked")} for h in d.get("history", [])][-14:],
        "news": [{k: str(n.get(k) or "")[:240] for k in ("date", "category", "title", "dek")} for n in d.get("news", [])[:6]],
        "headlines": [{k: str(h.get(k) or "")[:160] for k in ("title", "outlet", "date")} for h in d.get("headlines", [])[:12]],
        "changes": [{k: str(c.get(k) or "")[:420] for k in ("date", "kind", "text")} for c in d.get("changes", [])[-30:]][::-1],
    }


# ------------------------------------------------------------ race events (overtakes etc.)
_RACE_FILE = DATA / "pw_ranks.json"
_RACE_STAGES = [l for _, l, _ in MILESTONES]


def _race_load() -> dict | None:
    try:
        return json.loads(_RACE_FILE.read_text())
    except Exception:
        return None


def _race_detect(pw: dict) -> None:
    """Compare the new PortWatch ranking with the last one; record overtakes, new teams,
    stage-ups and new 'Exclusive' stories in STATE['race_events'] (persisted, newest first)."""
    now = datetime.now(timezone.utc).isoformat()
    ranked = pw.get("ranked") or []
    cur = {r["name"]: {"rank": r["rank"], "stage": r["stage"], "score": r["score"]} for r in ranked}
    titles = [n.get("title", "") for n in pw.get("news") or []]
    prev = _race_load()
    if prev is None:   # first run: seed the baseline, fire nothing
        state = {"ranks": cur, "news_seen": titles, "events": []}
        _RACE_FILE.write_text(json.dumps(state))
        STATE["race_events"] = []
        return
    old, events = prev.get("ranks", {}), []
    holder = {v["rank"]: k for k, v in old.items()}
    for name, c in cur.items():
        o = old.get(name)
        base = {"name": name, "stage": c["stage"], "score": c["score"], "at": now, "to_rank": c["rank"]}
        if o is None:
            events.append({**base, "kind": "new_entry", "from_rank": None,
                           "text": f"{name} enters the race at #{c['rank']} with {c['score']}/100 ({c['stage']})"})
            continue
        if c["rank"] < o["rank"]:
            passed = holder.get(c["rank"])
            # only a real overtake if the team we passed is still ranked and now below us
            if passed and passed != name and passed in cur and cur[passed]["rank"] > c["rank"]:
                events.append({**base, "kind": "overtake", "from_rank": o["rank"],
                               "text": f"{name} overtakes {passed} for #{c['rank']} ({c['score']}/100)"})
        si = _RACE_STAGES.index(c["stage"]) if c["stage"] in _RACE_STAGES else -1
        so = _RACE_STAGES.index(o["stage"]) if o["stage"] in _RACE_STAGES else -1
        if si > so >= 0:
            events.append({**base, "kind": "stage_up", "from_rank": o["rank"],
                           "text": f"{name} reaches {c['stage']}: now {c['score']}/100 at #{c['rank']}"})
    seen = set(prev.get("news_seen", []))
    for t in titles:
        if t and t not in seen and t.lower().startswith("exclusive"):
            events.append({"kind": "exclusive", "name": "PortWatch", "from_rank": None, "to_rank": None,
                           "stage": "", "score": None, "at": now, "text": t[:200]})
    all_events = (events + prev.get("events", []))[:10]
    _RACE_FILE.write_text(json.dumps({"ranks": cur, "news_seen": list(seen | set(titles))[-200:], "events": all_events}))
    STATE["race_events"] = all_events
    for e in events:
        print("race event:", e["kind"], e["text"], flush=True)


def poll_team_stats(slugs: list[str]) -> dict:
    """Light live GitHub stats for public race teams that aren't our two main repos (comparison scene)."""
    out = {}
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for slug in slugs:
        try:
            r, _ = gh(f"/repos/{slug}")
            n, page = 0, 1
            while page <= 3:
                c, _ = gh(f"/repos/{slug}/commits?since={since}&per_page=100&page={page}")
                n += len(c)
                if len(c) < 100:
                    break
                page += 1
            prs, h = gh(f"/repos/{slug}/pulls?state=open&per_page=1")
            out[slug] = {"stars": r.get("stargazers_count"), "forks": r.get("forks_count"), "pushed_at": r.get("pushed_at"),
                         "created_at": r.get("created_at"), "commits_24h": n, "commits_capped": n >= 300,
                         "open_prs": last_page_count(h, prs), "url": r.get("html_url"),
                         "avatar": (r.get("owner") or {}).get("avatar_url")}
        except Exception as e:
            print("team stats failed:", slug, e, flush=True)
    return out


def portwatch_loop():
    while True:
        try:
            STATE["portwatch"] = poll_portwatch()
            STATE["portwatch_error"] = None
            ours = {c["slug"] for c in REPOS}
            extra = [r["repo"] for r in STATE["portwatch"]["ranked"][:6] if r.get("repo") and r["repo"] not in ours]
            STATE["team_stats"] = poll_team_stats(extra)
            try:
                _race_detect(STATE["portwatch"])
            except Exception as e:
                print("race detect error:", e, flush=True)
            pw_exe = STATE["portwatch"]["exe"]
            if pw_exe.get("status") == "released" and pw_exe.get("releasedAt"):
                COUNTER["start"] = pw_exe["releasedAt"]   # keep our counter in sync with their timer
        except Exception as e:
            STATE["portwatch_error"] = f"{type(e).__name__}: {e}"
            print("portwatch error:", STATE["portwatch_error"], flush=True)
        time.sleep(PORTWATCH_POLL)


# ------------------------------------------------------------ news watch (CGBJ)
# Collects news about one team from PortWatch (articles, changelog, headlines) and Google News RSS.
# Only items whose text names the team are kept. All strings are untrusted -> escaped in the UI.
import html as _html
import urllib.parse as _up
WATCH = {"name": "CGBJ", "match": re.compile(r"\bCGBJ\b", re.I),
         "queries": ['"CGBJ" GTA', "CGBJ GTA 6 PC port"]}
WATCH_POLL = int(os.environ.get("WATCH_POLL", "600"))


def _wclean(t: str, n: int) -> str:
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return re.sub(r"\s+", " ", t).strip()[:n]


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()[:80]


def poll_watch() -> dict:
    items, m = [], WATCH["match"]
    d, _ = gh(PORTWATCH_URL)
    for n in d.get("news", []):
        text = " ".join([n.get("title", ""), n.get("dek", "")] + list(n.get("body") or []))
        if m.search(text):
            body = [p for p in (n.get("body") or []) if m.search(p)] or (n.get("body") or [""])
            items.append({"source": "PortWatch", "kind": "article", "title": _wclean(n.get("title"), 160),
                          "dek": _wclean(n.get("dek"), 260), "detail": _wclean(body[0], 320),
                          "at": (n.get("updatedAt") or n.get("date") or "") + ("T12:00:00Z" if len(n.get("updatedAt") or n.get("date") or "") == 10 else "")})
    for c in d.get("changes", []):
        if m.search(c.get("text", "")):
            items.append({"source": "PortWatch", "kind": "update", "title": _wclean(c.get("text"), 200), "dek": "",
                          "detail": "", "at": f'{c.get("date", "")}T12:00:00Z'})
    for h in d.get("headlines", []):
        if m.search(h.get("title", "")):
            items.append({"source": _wclean(h.get("outlet"), 40) or "Press", "kind": "press", "title": _wclean(h.get("title"), 160),
                          "dek": "", "detail": "", "at": h.get("date") or ""})
    for q in WATCH["queries"]:
        try:
            url = "https://news.google.com/rss/search?" + _up.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (repo-live-dashboard)"})
            xml = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
        except Exception as e:
            print("watch: google news failed:", e, flush=True)
            continue
        for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
            title = _wclean((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1], 200)
            outlet = _wclean((re.search(r"<source[^>]*>(.*?)</source>", it, re.S) or [None, ""])[1], 40)
            when = (re.search(r"<pubDate>(.*?)</pubDate>", it, re.S) or [None, ""])[1]
            if outlet and title.endswith(" - " + outlet):
                title = title[: -len(outlet) - 3]
            if not m.search(title):
                continue
            try:
                from email.utils import parsedate_to_datetime
                at = parsedate_to_datetime(when).astimezone(timezone.utc).isoformat()
            except Exception:
                at = ""
            items.append({"source": outlet or "Google News", "kind": "press", "title": title, "dek": "", "detail": "", "at": at})
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["at"] or "", reverse=True):
        k = _norm(it["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return {"name": WATCH["name"], "items": out[:10], "checked_at": datetime.now(timezone.utc).isoformat(),
            "sources": sorted({i["source"] for i in out})}


def watch_loop():
    while True:
        try:
            STATE["watch"] = poll_watch()
        except Exception as e:
            print("watch error:", e, flush=True)
        time.sleep(WATCH_POLL)


# ------------------------------------------------------------ race intel
# Rising teams (radar), press wall, official PC status, takedown watch. Shared by stream + site.
# All strings come from external sources -> escaped in the UIs.
INTEL_POLL = int(os.environ.get("INTEL_POLL", "600"))
INTEL_FILE = DATA / "intel_history.json"
_INTEL_UA = {"User-Agent": "Mozilla/5.0 (repo-live-dashboard)"}

# Hand-picked teams that matter but aren't ranked by PortWatch (yet). Auto-discovery adds more.
# Reviewed by hand (real code, not lures). Unknown repos are never shown automatically.
RADAR_SEED = {
    "fablinoo/palmwave": "Xbox Series compatibility layer built on XCL's approach (Rust)",
    "ThatRecompDev/five-star-recomp": "PortWatch gave it half credit for 'Main menu' (self-reported)",
    "JamesCicada/GTAVI-Recomp": "Translates the PS5 version instead of the Xbox build",
    "boykopovar/AnyBox": "Xbox Series compatibility layer for Windows",
    "rpatty-dev/PercyScarlett": "Xbox Series (Scarlett) translation layer",
}
# Known lures / not ports. Fake "GTA 6 PC" repos are a common malware vector.
RADAR_BLOCK = {"lenguyenkhang816792-arch/gta-6-pc-emulator", "Phosphenq/gta6-dlss5", "ubiquitarianism/leonida", "ProjectLeonidaL/leonida"}
_RADAR_SKIP = re.compile(r"tracker|archive|awesome|mirror|claudecode|test|hub|specs|wallpaper|map", re.I)
_RADAR_QUERIES = ["gta6 port", "gtavi recomp", "gta vi pc", "xbox series compatibility layer", "scarlett compatibility"]

# Last official word on a PC version. Update when Rockstar/Take-Two say something new.
OFFICIAL = {
    "pc_announced": False,
    "headline": "No PC version announced",
    "detail": "Take-Two's fiscal Q2 2027 results (19 Nov 2026) list Grand Theft Auto VI for PS5 and Xbox Series X|S only. "
              "Rockstar hasn't commented on the leak or the community ports. Unconfirmed: GamesBeat reported on 13 Oct, "
              "citing four anonymous industry sources, that Rockstar is targeting a PC release in Q1 2027.",
    "as_of": "2026-11-19",
    "source": "Take-Two Interactive Q2 FY2027 results",
}
_OFFICIAL_DOMAINS = ("rockstargames.com", "take2games.com")
_OFFICIAL_CLAIM = re.compile(r"rockstar\s+(officially\s+)?(confirms|announces|reveals)[^.]*\bpc\b|gta\s*(6|vi)\s+pc\s+(release date\s+)?(officially\s+)?(confirmed|announced)", re.I)
_PRESS_QUERIES = ['"GTA 6" PC port', '"GTA 6" PC recompilation OR emulator', '"GTA VI" PC port', "Rockstar GTA 6 PC version"]
_PRESS_KEEP = re.compile(r"gta\s*(6|vi)|grand theft auto (6|vi)", re.I)
_PRESS_TOPIC = re.compile(r"\bpc\b|port|recomp|emulat|leak|xbox build|executable|modder|takedown|dmca|cyberleek", re.I)
_PRESS_SKIP = re.compile(r"can (my|your) pc run|minimum (and|&) recommended|system requirements|wallpaper|price|pre-?order|deal", re.I)


def _intel_load():
    try:
        return json.loads(INTEL_FILE.read_text())
    except Exception:
        return {"radar": {}, "dmca_seen": []}


INTEL_HIST = _intel_load()


def _intel_rss(query: str, days: int = 3) -> list[dict]:
    url = "https://news.google.com/rss/search?" + _up.urlencode({"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        xml = urllib.request.urlopen(urllib.request.Request(url, headers=_INTEL_UA), timeout=20).read().decode("utf-8", "replace")
    except Exception as e:
        print("intel: news failed:", e, flush=True)
        return []
    out = []
    from email.utils import parsedate_to_datetime
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        title = _wclean((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1], 220)
        src = re.search(r'<source url="(.*?)">(.*?)</source>', it, re.S)
        outlet, domain = (_wclean(src.group(2), 40), src.group(1)) if src else ("", "")
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(outlet) - 3]
        try:
            at = parsedate_to_datetime((re.search(r"<pubDate>(.*?)</pubDate>", it, re.S) or [None, ""])[1]).astimezone(timezone.utc).isoformat()
        except Exception:
            at = ""
        out.append({"title": title, "outlet": outlet, "domain": domain, "at": at})
    return out


def _intel_press() -> tuple[list, list]:
    seen, press, official_hits = set(), [], []
    for q in _PRESS_QUERIES:
        for it in _intel_rss(q):
            k = _norm(it["title"])
            if k in seen or not _PRESS_KEEP.search(it["title"]) or _PRESS_SKIP.search(it["title"]):
                continue
            seen.add(k)
            if any(d in it["domain"] for d in _OFFICIAL_DOMAINS) or _OFFICIAL_CLAIM.search(it["title"]):
                official_hits.append({**it, "official_source": any(d in it["domain"] for d in _OFFICIAL_DOMAINS)})
            if _PRESS_TOPIC.search(it["title"]):
                press.append(it)
    press.sort(key=lambda x: x["at"], reverse=True)
    return press[:24], official_hits[:5]


def _intel_radar(pw_ranked_repos: set, candidates: list) -> list:
    repos = dict(RADAR_SEED)
    since = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    for c in candidates:   # PortWatch scout picks with high substance
        if c.get("repo") and (c.get("substance") or 0) >= 85 and (c.get("stars") or 0) >= 5 and not _RADAR_SKIP.search(c["repo"]):
            repos.setdefault(c["repo"], f"PortWatch scout pick (substance {c['substance']})")
    out, now = [], int(time.time())
    for full, note in repos.items():
        if full in pw_ranked_repos or full in RADAR_BLOCK:
            continue
        try:
            r, _ = gh(f"/repos/{full}")
        except Exception:
            continue
        if r.get("archived") or r.get("disabled"):
            continue
        if full not in RADAR_SEED and (r.get("language") in (None, "HTML", "PowerShell", "Batchfile") or r.get("size", 0) < 300):
            continue   # scout pick that looks like a page/installer rather than code
        h = INTEL_HIST["radar"].setdefault(full, [])
        if not h or h[-1][1] != r["stargazers_count"] or now - h[-1][0] > 1800:
            h.append([now, r["stargazers_count"]])
        del h[:-500]
        base = next((x for x in h if now - x[0] <= 3 * 3600), h[0])
        hours = max((now - base[0]) / 3600, 0.25)
        out.append({"repo": full, "name": r["name"], "owner": r["owner"]["login"], "avatar": r["owner"]["avatar_url"] + "&s=80",
                    "desc": _wclean(r.get("description"), 140), "note": note, "stars": r["stargazers_count"],
                    "stars_per_h": round((r["stargazers_count"] - base[1]) / hours, 1) if len(h) > 1 else None,
                    "pushed_at": r["pushed_at"], "created_at": r["created_at"], "lang": r.get("language"), "url": r["html_url"]})
    out.sort(key=lambda x: (-(x["stars_per_h"] or 0), -x["stars"]))
    return out[:12]


def _intel_takedowns(pw: dict) -> dict:
    events = [{"name": n["name"], "repo": n.get("repo"), "kind": "nuked", "text": f"{n['name']} was taken offline and is no longer ranked."}
              for n in (pw or {}).get("nuked", [])]
    for n in (pw or {}).get("news", []):
        if re.search(r"takedown|dmca|nuked", n.get("title", ""), re.I):
            events.insert(0, {"name": "PortWatch", "repo": None, "kind": "takedown", "text": n["title"]})
    if any(n["name"] == "Leonida" for n in (pw or {}).get("nuked", [])):
        events.append({"name": "Leonida", "repo": None, "kind": "caution",
                       "text": "Several new GitHub repos claim to continue Leonida. None is verified: avoid downloads from them."})
    try:   # notices are named <date>-<company>.md; commit messages are generic, so scan this + last month's folders
        now = datetime.now(timezone.utc); first = now.replace(day=1); prev = first - timedelta(days=1)
        found = []
        try:
            raw, _ = gh(PORTWATCH_URL)
            pw_dmca = {x.get("path"): x for x in (raw.get("dmca") or [])}
        except Exception:
            pw_dmca = {}
        for y, m in ((now.year, now.month), (prev.year, prev.month)):
            try:
                items, _ = gh(f"/repos/github/dmca/contents/{y}/{m:02d}")
            except Exception:
                continue
            for it in items:
                n = it.get("name", "")
                if re.search(r"take-?two|rockstar", n, re.I):
                    meta = pw_dmca.get(it.get("path"), {})
                    if meta and not meta.get("gta6"):
                        continue   # PortWatch read it: not about GTA VI ports (e.g. GTA V mobile ports)
                    hit = ", ".join(meta.get("tracked") or [])
                    found.append({"at": n[:10], "url": it.get("html_url"),
                                  "text": "Take-Two DMCA notice" + (f" naming {hit}" if hit else "") + " (GTA VI ports)" if meta.get("gta6") else "Take-Two DMCA notice"})
        found.sort(key=lambda x: x["at"], reverse=True)
        INTEL_HIST["dmca"] = found[:8]
    except Exception as e:
        print("intel: dmca failed:", e, flush=True)
    del INTEL_HIST["dmca_seen"][:-500]
    return {"events": events, "dmca": INTEL_HIST.get("dmca", [])[:8], "checked_at": datetime.now(timezone.utc).isoformat()}


def poll_intel(pw: dict | None, candidates: list | None = None) -> dict:
    ranked = {r["repo"] for r in (pw or {}).get("ranked", []) if r.get("repo")} | {c["slug"] for c in REPOS}
    press, official_hits = _intel_press()
    intel = {
        "radar": _intel_radar(ranked, candidates or []),
        "press": press,
        "official": {**OFFICIAL, "reports": official_hits},
        "takedowns": _intel_takedowns(pw),
        "safety": "No playable GTA VI PC build exists. 'GTA 6 PC download' links and installers are fakes or malware.",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        INTEL_FILE.write_text(json.dumps(INTEL_HIST))
    except Exception:
        pass
    return intel


def _intel_candidates() -> list:
    try:
        d, _ = gh(PORTWATCH_URL)
        return [c for c in d.get("candidates", []) if c.get("kind") == "effort"]
    except Exception:
        return []


def intel_loop():
    time.sleep(20)  # let the PortWatch poll land first
    while True:
        try:
            STATE["intel"] = poll_intel(STATE.get("portwatch"), _intel_candidates())
        except Exception as e:
            print("intel error:", type(e).__name__, e, flush=True)
        time.sleep(INTEL_POLL)


# ------------------------------------------------------------ site refresh backup
# GitHub often skips/delays scheduled workflows on new repos. While this server runs, make sure the
# public site rebuilds at least every SITE_REFRESH_MIN minutes by dispatching the Pages workflow via
# the local `gh` login (no extra token stored here).
SITE_REPO = os.environ.get("SITE_REPO", "hammadxcm/gta6-pc-tracker")
SITE_REFRESH_MIN = int(os.environ.get("SITE_REFRESH_MIN", "20"))


def site_refresh_loop():
    import shutil
    import subprocess
    if not shutil.which("gh"):
        print("site refresh: gh CLI not found, skipping backup trigger", flush=True)
        return
    time.sleep(90)
    while True:
        try:
            out = subprocess.run(["gh", "run", "list", "-R", SITE_REPO, "--workflow", "pages.yml", "--limit", "1",
                                  "--json", "createdAt,status"], capture_output=True, text=True, timeout=60)
            runs = json.loads(out.stdout or "[]")
            age = (datetime.now(timezone.utc) - _ts(runs[0]["createdAt"])).total_seconds() / 60 if runs else 1e9
            busy = bool(runs) and runs[0]["status"] in ("queued", "in_progress", "waiting", "pending")
            if age > SITE_REFRESH_MIN and not busy:
                r = subprocess.run(["gh", "workflow", "run", "pages.yml", "-R", SITE_REPO], capture_output=True, text=True, timeout=60)
                print(f"site refresh: last build {age:.0f} min ago -> dispatched ({r.returncode})", flush=True)
        except Exception as e:
            print("site refresh error:", e, flush=True)
        time.sleep(300)

# ------------------------------------------------------------------------- slides
SLIDES = STATIC / "slides"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif"}


def list_slides() -> list[dict]:
    """Images dropped into static/slides/ (optional credits.json: [{file,title,creator,license}])."""
    SLIDES.mkdir(exist_ok=True)
    credits = {}
    try:
        for c in json.loads((SLIDES / "credits.json").read_text()):
            credits[Path(c.get("file", "")).name] = c
    except Exception:
        pass
    out = []
    for f in sorted(SLIDES.iterdir()):
        if f.suffix.lower() in IMG_EXT and f.is_file():
            c = credits.get(f.name, {})
            out.append({"src": f"slides/{f.name}?v={int(f.stat().st_mtime)}", "title": str(c.get("title", ""))[:80],
                        "credit": " · ".join(x for x in (c.get("creator"), c.get("license")) if x)[:100]})
    return out


def music_info() -> dict | None:
    """Playlist (music/playlist.json) + when stream.py started the audio, for the now-playing card."""
    try:
        pl = json.loads((ROOT / "music" / "playlist.json").read_text())
        try:   # written by stream.py; absent in OBS mode, where the page's own audio clock is used
            started = json.loads((DATA / "music_started.json").read_text()).get("started_ms")
        except Exception:
            started = None
        return {"tracks": pl.get("tracks", []), "total": pl.get("total"), "started_ms": started}
    except Exception:
        return None


def build_stamp() -> str:
    """Changes when the page code changes -> the live page reloads itself (slides update without reload)."""
    return str(int((STATIC / "index.html").stat().st_mtime))


# ------------------------------------------------------------------------- server
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            with _lock:
                body = json.dumps({**STATE, "order": [r["slug"] for r in REPOS], "counter": COUNTER,
                                   "slides": list_slides(), "build": build_stamp(), "music": music_info(),
                                   "server_time": datetime.now(timezone.utc).isoformat()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/music/mix.m4a":   # OBS mode: the page plays the music itself
            f = ROOT / "music" / "mix.m4a"
            if not f.is_file():
                self.send_error(404); return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "none")
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=portwatch_loop, daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()
    threading.Thread(target=intel_loop, daemon=True).start()
    threading.Thread(target=site_refresh_loop, daemon=True).start()
    print(f"Repo Live on http://localhost:{PORT}  (poll every {POLL}s, token={'yes' if TOKEN else 'no'})")
    # Local-only by default: the stream's own browser is on this machine. Set BIND=0.0.0.0 to share on a LAN.
    ThreadingHTTPServer((os.environ.get("BIND", "127.0.0.1"), PORT), Handler).serve_forever()
