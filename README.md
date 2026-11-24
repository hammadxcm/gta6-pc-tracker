# GTA 6 PC Port Tracker: gta6.fyniti.co.uk

Source for **https://gta6.fyniti.co.uk/**, a live tracker of the community efforts to run GTA VI on PC:
race scores and stages, XCL and VIctory GitHub stats, a live PR board, rising teams, press coverage and
official status. Unofficial fan project; not affiliated with Rockstar Games, Take-Two, PortWatch or any listed team.

- `site/`: the static site (rendered from `site/index.template.html`).
- `scripts/build_site_data.py` + `scripts/render_site.py`: build `site/data/live.json` and the server-rendered HTML.
- `server.py`: the shared data pollers (GitHub, PortWatch VI, Google News).
- `.github/workflows/pages.yml`: rebuilds and deploys to GitHub Pages every 15 minutes.

Preview locally: `python3 scripts/build_site_data.py && python3 -m http.server -d site 8090`

Data: GitHub REST API · [PortWatch VI](https://portwatch-vi.xyz/) · Google News headlines.
Watch the live stream: [HK Gaming on YouTube](https://www.youtube.com/@hammadxcm).
