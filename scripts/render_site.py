#!/usr/bin/env python3
"""Server-side render for SEO: fills site/index.template.html with the live data so crawlers and
link previews see real content, and writes sitemap.xml + robots.txt. Called by build_site_data.py."""
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
SITE_URL = os.environ.get("SITE_URL", "https://gta6.fyniti.co.uk/").rstrip("/") + "/"
STAGES = ["Groundwork", "Exe runs", "First frame", "Main menu", "In-game", "30 FPS", "Stable"]
e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def _ini(name):
    return re.sub(r"[^A-Za-z0-9]", "", name or "?")[:2].upper()


def _avatar(r):
    if r.get("avatar"):
        src = re.sub(r"size=\d+", "size=160", r["avatar"])
        return f'<img class="av" src="{e(src)}" alt="{e(r["name"])} avatar" width="48" height="48" loading="lazy" decoding="async">'
    return f'<span class="av ini" role="img" aria-label="{e(r["name"])}">{e(_ini(r["name"]))}</span>'


def board_html(pw):
    ours = set(pw.get("ours") or [])
    out = []
    for r in pw["ranked"]:
        si = STAGES.index(r["stage"]) if r["stage"] in STAGES else 0
        tags = ('<em class="tag priv">PRIVATE</em>' if r.get("private") else "") + \
               ('<em class="tag ours">ON STREAM</em>' if r.get("repo") in ours else "")
        meta = f'{e(r["target"])}' + (f' · ⭐ {r["stars"]:,}' if r.get("stars") else "")
        lis = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs or [])
        link = (f'<a href="https://github.com/{e(r["repo"])}" target="_blank" rel="noopener">github.com/{e(r["repo"])} ↗</a>'
                if r.get("repo") else f'<span>{e(r.get("byline") or "No public code")}</span>')
        out.append(f'''<details class="eff card {'top' if r['rank'] <= 3 else ''}"><summary><span class="rk">{r['rank']}</span>{_avatar(r)}
<div style="min-width:0"><h3 class="eff-n">{e(r['name'])}{tags}</h3><span class="meta">{meta}</span></div>
<span class="stage s{si}">{e(r['stage'])}</span><span class="sc">{r['score']}</span><span class="chev">›</span>
<div class="bar"><i style="width:{r['score']}%"></i></div></summary>
<div class="body">{f'<p class="sum">{e(r["summary"])}</p>' if r.get('summary') else ''}<div class="facts"><span>Verification: {e(r.get('verification') or 'unknown')}</span>{link}</div>
{f'<div class="ok"><h4>What works</h4><ul>{lis(r.get("highlights"))}</ul></div>' if r.get('highlights') else ''}
{f'<div class="no"><h4>Blockers</h4><ul>{lis(r.get("blockers"))}</ul></div>' if r.get('blockers') else ''}</div></details>''')
    return "\n".join(out)


def intel_html(data):
    i = data.get("intel") or {}
    o, t = i.get("official") or {}, i.get("takedowns") or {}
    reps = o.get("reports") or []
    official = (f'<p class="st-k">Official PC version</p><h3 class="st-h {"yes" if o.get("pc_announced") else "no"}"><span class="st-dot" aria-hidden="true"></span>'
                f'{e(o.get("headline") or "No PC version announced")}</h3><p class="st-p">{e(o.get("detail"))}</p>'
                f'<p class="st-src">Source: {e(o.get("source"))}{" · as of " + e(o.get("as_of")) if o.get("as_of") else ""}</p>'
                + (f'<div class="st-rep"><span class="pill2 warn">Reported, unconfirmed</span><ul>'
                   + "".join(f"<li>{e(r['title'])} <span>· {e(r['outlet'])}</span></li>" for r in reps) + "</ul></div>" if reps else ""))
    ev = t.get("events") or []
    def td_item(x):
        ic = "!" if x["kind"] == "caution" else "✕"
        if x["text"].startswith(x["name"]):
            body = f'<b>{e(x["name"])}</b> {e(x["text"][len(x["name"]):].strip())}'
        else:
            body = f'<b>{e(x["name"])}:</b> {e(x["text"])}'
        return f'<li class="{e(x["kind"])}"><span class="td-ic" aria-hidden="true">{ic}</span><p>{body}</p></li>'
    dm = t.get("dmca") or []
    takedowns = ('<p class="st-k">Takedown watch</p><ul class="td">' + "".join(td_item(x) for x in ev) + '</ul><p class="st-src">'
                 + (("GitHub DMCA notices: " + " · ".join(e(d["text"]) for d in dm)) if dm else "No new GitHub DMCA notices for GTA projects.")
                 + "</p>")
    radar = "".join(
        f'<article class="rd card"><div class="rd-h"><span class="av ini" role="img" aria-label="{e(r["name"])}">{e(_ini(r["name"]))}</span>'
        f'<div style="min-width:0"><h3>{e(r["name"])}</h3><span class="meta">{e(r["owner"])}{" · " + e(r["lang"]) if r.get("lang") else ""}</span></div></div>'
        f'<p class="rd-n">{e(r.get("note") or r.get("desc"))}</p><div class="rd-f"><span>⭐ {r["stars"]:,}</span>'
        f'<a href="{e(r["url"])}" target="_blank" rel="noopener nofollow">GitHub ↗</a></div></article>' for r in i.get("radar") or [])
    press = "".join(
        f'<article class="pr card{" lead" if n == 0 else ""}"><span class="pr-o">{e(p["outlet"])}</span><h3>{e(p["title"])}</h3></article>'
        for n, p in enumerate((i.get("press") or [])[:12]))
    return {"safety": e(i.get("safety") or ""), "official": official, "takedowns": takedowns, "radar": radar, "press": press}


def faqs(data):
    pw, L = data["portwatch"], data["portwatch"]["leader"]
    reached = pw.get("furthest", "Groundwork")
    playable = reached in ("30 FPS", "Stable")
    day = datetime.fromisoformat(data["built_at"]).strftime("%-d %B %Y")
    repos = {r["label"]: r for r in data["repos"]}
    q = [
        ("Does GTA 6 run on PC yet?",
         (f"Yes, in stable form: the furthest stage reached is {reached}." if playable else
          f"Not playably. As of {day}, the furthest stage any team has reached is “{reached}”, led by {L['name']} "
          f"at {L['score']:g}/100{' at around ' + str(L['fps']) + ' fps' if L.get('fps') else ''}. Progress is self-reported by each team, "
          f"and no project has reported stable 30 fps gameplay yet.")),
        ("What are XCL and VIctory?",
         " ".join(f"{k} ({r['slug']}): {r.get('description') or ''}" for k, r in repos.items())
         + " Both are open source on GitHub, and this tracker follows their stars, pull requests and commits live."),
        ("How is the progress score calculated?",
         "Each effort is scored 0–100 on one ladder: Groundwork (0–25), Exe runs (25), First frame (40), Main menu (55), "
         "In-game (70), 30 FPS (85) and Stable (100). Scores and stages come from PortWatch VI."),
        ("How often is this tracker updated?",
         "Every 15 minutes. GitHub stats come from the GitHub API, race scores and news from PortWatch VI, and press coverage from Google News."),
        ("Is this an official Rockstar or Take-Two site?",
         "No. Porting Race is an unofficial fan tracker. It isn't affiliated with Rockstar Games, Take-Two, PortWatch or any listed project, and it hosts no game files."),
    ]
    return q


def faq_html(q):
    return "\n".join(f'<details class="qa card"><summary><h3>{e(a)}</h3></summary><p>{e(b)}</p></details>' for a, b in q)


def json_ld(data, q):
    pw = data["portwatch"]
    graph = [
        {"@type": "WebSite", "@id": SITE_URL + "#site", "name": "Porting Race", "url": SITE_URL,
         "description": "Live tracker of the community efforts to run GTA VI on PC.",
         "sameAs": ["https://www.youtube.com/@hammadxcm"]},
        {"@type": "WebPage", "@id": SITE_URL + "#page", "url": SITE_URL, "name": "GTA 6 PC Port Tracker: Does GTA VI Run on PC Yet?",
         "isPartOf": {"@id": SITE_URL + "#site"}, "dateModified": data["built_at"], "inLanguage": "en",
         "primaryImageOfPage": SITE_URL + "og.png", "about": {"@type": "VideoGame", "name": "Grand Theft Auto VI"}},
        {"@type": "ItemList", "name": "GTA VI on PC efforts by progress score", "numberOfItems": len(pw["ranked"]),
         "itemListOrder": "https://schema.org/ItemListOrderDescending",
         "itemListElement": [{"@type": "ListItem", "position": r["rank"],
                              "name": f'{r["name"]}: {r["score"]}/100 ({r["stage"]})',
                              **({"url": f'https://github.com/{r["repo"]}'} if r.get("repo") else {})} for r in pw["ranked"]]},
        {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": a,
                                            "acceptedAnswer": {"@type": "Answer", "text": b}} for a, b in q]},
    ]
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False).replace("</", "<\\/")


def _num(x):
    return f"{x:g}" if isinstance(x, (int, float)) else e(x)


def live_html(data):
    """SEO snapshot of race bars, live board stats and the press ticker (JS replaces it with live versions)."""
    pw, out = data["portwatch"], {}
    rk, ours = pw.get("ranked") or [], pw.get("ours") or []
    if len(rk) >= 2:
        rows = ([rk[0]] if rk[0].get("repo") not in ours else []) + [r for s_ in ours for r in rk if r.get("repo") == s_]
        mine = sorted([r for r in rows if r.get("repo") in ours], key=lambda r: -r["score"])
        h2h = (f' · {e(mine[0]["name"])} leads {e(mine[1]["name"])} by <b>{_num(round(mine[0]["score"] - mine[1]["score"], 1))}</b>'
               if len(mine) == 2 else "")
        out["rbars"] = (f'<p class="rb-lead"><b>{e(rk[0]["name"])}</b> leads <b>{e(rk[1]["name"])}</b> by '
                        f'<b>{_num(round(rk[0]["score"] - rk[1]["score"], 1))} pts</b>{h2h}</p>' +
                        "".join(f'<div class="rb"><span class="rb-n">{e(r["name"])}</span><div class="rb-t">'
                                f'<span class="rb-f{" ours" if r.get("repo") in ours else ""}" style="width:{r["score"]}%"></span></div>'
                                f'<span class="rb-s">{_num(r["score"])}<small>{e(r["stage"])}</small></span></div>' for r in rows))
    cards = []
    for r in data.get("repos", []):
        tiles = [(r.get("stars"), "Stars"), (r.get("merged_today"), "Merged today"), (r.get("commits_today"), "Commits today"),
                 (r.get("prs_opened_1h"), "PRs opened · 1h"), (r.get("open_prs"), "Open PRs")]
        evs = "".join(f'<li class="lb-ev"><span class="av ini">{e((ev.get("by") or "?")[:2].upper())}</span><div class="lb-m">'
                      f'<p><span class="bd {e(ev["kind"])}">{e(ev["kind"].upper())}</span>{e(ev["title"])}</p>'
                      f'<s>#{ev["n"]} by {e(ev.get("by"))}</s></div></li>' for ev in (r.get("events") or [])[:6])
        cards.append(f'<article class="lb card" style="--acc:{e(r.get("accent"))}"><header class="lb-top"><h3>{e(r["label"])}</h3></header>'
                     f'<div class="lb-tiles">{"".join(f"<div class='st'><b>{(v if v is not None else 0):,}</b><span>{l}</span></div>" for v, l in tiles)}</div>'
                     f'<ol class="lb-list">{evs}</ol></article>')
    out["lboard"] = "".join(cards)
    press = ((data.get("intel") or {}).get("press") or [])[:12]
    out["ticker"] = "".join(f'<li><b>{e(p["outlet"])}</b> {e(p["title"])}</li>' for p in press)
    return out


def fill(tpl, slot, value):
    return re.sub(rf"<!--SSR:{slot}-->.*?<!--/SSR-->", lambda _: f"<!--SSR:{slot}-->{value}<!--/SSR-->", tpl, flags=re.S)


def render(data):
    pw, L = data["portwatch"], data["portwatch"]["leader"]
    tpl = (SITE / "index.template.html").read_text()
    built = datetime.fromisoformat(data["built_at"])
    q = faqs(data)
    og_desc = (f"{L['name']} leads at {L['score']}/100 ({L['stage']}). Live scores, GitHub stats and news for XCL, VIctory "
               f"and every team racing to run GTA VI on PC.") if L else "Live tracker of every effort to run GTA VI on PC."
    out = (tpl.replace("__SITE_URL__", SITE_URL).replace("__OG_DESC__", e(og_desc)).replace("__JSON_LD__", json_ld(data, q)))
    out = fill(out, "score", e(L["score"]) if L else "–")
    out = fill(out, "lead", f'Leader <b>{e(L["name"])}</b> · {e(L["stage"])} · top-3 average <b>{pw["top3"]}</b> · <b>{pw["watched"]}</b> efforts watched' if L else "")
    out = fill(out, "board", board_html(pw))
    out = fill(out, "faq", faq_html(q))
    for slot, value in {**intel_html(data), **live_html(data)}.items():
        if value:
            out = fill(out, slot, value)
    out = fill(out, "upd", f"updated {built.strftime('%H:%M UTC')}")
    out = fill(out, "built", built.strftime("%a, %d %b %Y %H:%M UTC"))
    (SITE / "index.html").write_text(out)
    (SITE / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{SITE_URL}</loc><lastmod>{built.strftime("%Y-%m-%dT%H:%M:%S+00:00")}</lastmod>'
        f'<changefreq>hourly</changefreq><priority>1.0</priority></url>\n</urlset>\n')
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n")
    return len(out)
