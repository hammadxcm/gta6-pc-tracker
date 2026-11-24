# Porting Race Live

A live, animated 1080p broadcast dashboard for two GitHub repos, streamed to YouTube:
- [ivall/xcl](https://github.com/ivall/xcl)
- [Marvisak/VIctory](https://github.com/Marvisak/VIctory)

## What's on screen
- **PR spotlight** (main scene): status pill, huge `#number`, title, author, `+adds −dels`, files and commits. It rotates every 7 s, and new PRs interrupt it with a "JUST IN" flash.
- **LATEST** feed: opened, merged, closed and draft PRs from both repos.
- **Head-to-head** scene: README checklist completeness, measured counts (e.g. shaders 8,191 / 8,506), feature status chips.
- **Activity** scene: commits per hour over 24 h for both repos, plus top committers.
- **Stat strip**: open PRs, merged today, PRs so far, stars and latest release. It flips between the repos every 12 s.
- **Headline typewriter** built from the live numbers.

Progress is parsed from each repo's README on every poll (tables with ✅/🚧, "Needs" columns, "X of Y" counts), so the bars move when the maintainers update their status.

## Run
```bash
export GITHUB_TOKEN=github_pat_...   # strongly recommended (5,000 req/h; free ETag 304s)
export YT_STREAM_KEY=xxxx-xxxx-...   # YouTube Studio > Go live > Stream
./run.sh --test 20                   # dry run -> data/test.mp4
./run.sh                             # go live
```
Preview in any browser: http://localhost:8080 (it works from Windows too, via WSL localhost forwarding).

**OBS alternative:** run `python3 server.py`, then add a *Browser Source* with `http://localhost:8080` at 1920×1080 and stream from OBS.

## Config (env)
| Var | Default | |
|---|---|---|
| `POLL_SECONDS` | 30 with token / 900 without | GitHub poll interval |
| `VIDEO_BITRATE` | 6000k | H.264 CBR, 2 s keyframes, 1080p30 |
| `YT_RTMP_URL` | rtmps://a.rtmps.youtube.com:443/live2 | |
| `MUSIC_FILE` | (silence) | looped audio track. Use only music you have rights to, or YouTube may mute or flag the stream |

Custom background: drop a `static/bg.jpg` (one you have rights to) and it replaces the generated skyline.
Repos, labels and accent colours are set in `REPOS` at the top of `server.py`.

## Public tracker website (GitHub Pages)
Live at **https://gta6.fyniti.co.uk/** (GoDaddy CNAME `gta6` → `hammadxcm.github.io`; `site/CNAME` holds the domain).

`site/` is a static tracker site: the full race (every ranked team, with details, filters and sorting), milestone guide, progress-over-time chart, change log, XCL/VIctory GitHub stats, news, and the live stream embed.

- `.github/workflows/pages.yml` rebuilds `site/data/live.json` every 15 minutes with `scripts/build_site_data.py` (same pollers as the stream) and deploys to Pages.
- Set your YouTube channel in `site/config.js` to embed the live stream.
- Preview locally: `python3 scripts/build_site_data.py && python3 -m http.server -d site 8090`.

Rockstar artwork (`static/slides/`) and the audio mix stay local and are not part of this repo.
