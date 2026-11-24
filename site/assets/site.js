/* Porting Race public site. Renders data/live.json (rebuilt every 15 min by GitHub Actions). */
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nf = n => n == null ? "–" : Number(n).toLocaleString("en-US");
const pad = n => String(n).padStart(2, "0");
const CFG = window.SITE_CONFIG || {};
function ago(iso){
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago"; return Math.floor(s / 86400) + " d ago";
}
const initials = r => (r.name || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase();
const avatar = (r, cls = "av") => r.avatar
  ? `<img class="${cls}" src="${esc(r.avatar.replace(/size=\d+/, "size=160"))}" alt="${esc(r.name)} avatar" width="48" height="48" loading="lazy" decoding="async" data-ini="${esc(initials(r))}" onerror="this.outerHTML='<span class=&quot;${cls} ini&quot;>'+this.dataset.ini+'</span>'">`
  : `<span class="${cls} ini" role="img" aria-label="${esc(r.name)}">${esc(initials(r))}</span>`;

let D = null;

/* ---------- elapsed counter (real time, ticks on the second) ---------- */
function clock(){
  if (!D) return;
  const start = Date.parse(D.counter.start), diff = Math.max(0, Date.now() - start);
  const parts = [[Math.floor(diff / 864e5), "days"], [Math.floor(diff % 864e5 / 36e5), "hours"], [Math.floor(diff % 36e5 / 6e4), "min"], [Math.floor(diff % 6e4 / 1e3), "sec"]];
  $("#clk").innerHTML = parts.map(([v, l]) => `<div class="cu"><b>${pad(v)}</b><small>${l}</small></div>`).join("");
}
(function align(){ clock(); setTimeout(align, 1000 - (Date.now() % 1000) + 5); })();

/* ---------- renderers ---------- */
function renderHero(){
  const pw = D.portwatch, L = pw.leader;
  $("#lead-score").textContent = L ? L.score : "–";
  $("#lead-line").innerHTML = L ? `Leader <b>${esc(L.name)}</b> · ${esc(L.stage)} · top-3 average <b>${pw.top3}</b> · <b>${pw.watched}</b> efforts watched` : "";
  $("#clk-lbl").textContent = D.counter.label || "Since the executable dropped";
  $("#clk-note").textContent = `Counting from ${new Date(D.counter.start).toUTCString().replace(":00 GMT", " UTC")}`;
}

const STAGES = ["Groundwork", "Exe runs", "First frame", "Main menu", "In-game", "30 FPS", "Stable"];
let FLT = "all", SRT = "score";
function renderRace(){
  const pw = D.portwatch, L = pw.leader, ms = pw.milestones || [];
  $("#ladder").innerHTML = `<div class="marks" id="marks"><svg class="leaders" aria-hidden="true"></svg>${pw.ranked.slice(0, 6).map(r => `<div class="mk ${r.rank === 1 ? "lead" : ""}" data-l="${r.score}" title="${esc(r.name)} · ${r.score}"><span class="mk-r">${r.rank}</span>${avatar(r)}<span class="mk-n">${esc(r.name.length > 12 ? r.name.slice(0, 11) + "…" : r.name)}</span></div>`).join("")}</div>
    <div class="rail"><div class="fill" data-w="${L ? L.score : 0}"></div>
    ${ms.map((m, i) => `<div class="tick ${L && L.score >= m.at ? "hit" : ""} ${i === 0 ? "first" : i === ms.length - 1 ? "last" : ""}" style="left:${m.at}%"><span><b class="full">${esc(m.label)}</b><b class="num">${m.at}</b></span></div>`).join("")}</div>`;
  layoutMarks();
  const reached = STAGES.indexOf(pw.furthest);
  $("#steps").innerHTML = ms.map((m, i) => `<div class="step ${i <= reached ? "hit" : ""}"><small>${m.at}</small><b>${esc(m.label)}</b><p>${esc((pw.milestone_desc || {})[m.label] || "")}</p></div>`).join("");
  const n = pw.nuked || [];
  $("#nuked").textContent = n.length ? `Taken offline and no longer ranked: ${n.map(x => x.name).join(", ")}.` : "";
  renderBoard();
}
/* ladder avatars: one row, pushed apart so none overlap, each joined to its exact score by a curved line */
function layoutMarks(){
  const box = $("#marks"); if (!box) return;
  const named = [...box.querySelectorAll(".mk-n")].some(n => getComputedStyle(n).display !== "none");
  const W = box.clientWidth, GAP = named ? 92 : 46, H = box.clientHeight, EDGE = GAP / 2;
  const items = [...box.querySelectorAll(".mk")].map(m => ({m, x: W * m.dataset.l / 100})).sort((a, b) => a.x - b.x);
  items.forEach((it, i) => it.a = Math.max(EDGE, i ? Math.max(it.x, items[i - 1].a + GAP) : it.x));
  const over = items.length ? items[items.length - 1].a - (W - EDGE) : 0;
  if (over > 0) for (let i = items.length - 1; i >= 0; i--) it_shift(i);
  function it_shift(i){ const lim = i === items.length - 1 ? W - EDGE : items[i + 1].a - GAP; items[i].a = Math.min(items[i].a, lim); }
  box.querySelector(".leaders").innerHTML = items.map(it => {
    const y0 = it.m.offsetHeight + 2, y1 = H;
    return `<path d="M${it.a} ${y0} C ${it.a} ${y0 + (y1 - y0) * .55}, ${it.x} ${y0 + (y1 - y0) * .45}, ${it.x} ${y1}" class="${it.m.classList.contains("lead") ? "hot" : ""}"/><circle cx="${it.x}" cy="${y1 - 1}" r="3" class="${it.m.classList.contains("lead") ? "hot" : ""}"/>`;
  }).join("");
  items.forEach(it => it.m.style.left = it.a + "px");
}
addEventListener("resize", () => { clearTimeout(layoutMarks.t); layoutMarks.t = setTimeout(layoutMarks, 120); });

function renderBoard(){
  const pw = D.portwatch, ours = new Set(pw.ours || []);
  let list = pw.ranked.filter(r => FLT === "all" || (FLT === "gta" ? /gta/i.test(r.target) : !/gta/i.test(r.target)));
  const by = {score: (a, b) => b.score - a.score, stars: (a, b) => (b.stars || 0) - (a.stars || 0),
              active: (a, b) => Date.parse(b.pushed_at || 0) - Date.parse(a.pushed_at || 0)};
  list = [...list].sort(by[SRT]);
  $("#count").textContent = `${list.length} of ${pw.ranked.length} ranked efforts`;
  $("#board").innerHTML = list.map(r => {
    const si = STAGES.indexOf(r.stage), repoUrl = r.repo ? `https://github.com/${r.repo}` : null;
    return `<details class="eff card reveal ${r.rank <= 3 ? "top" : ""}">
      <summary><span class="rk">${r.rank}</span>${avatar(r)}
        <div style="min-width:0"><h3 class="eff-n">${esc(r.name)}${r.private ? '<em class="tag priv">PRIVATE</em>' : ""}${ours.has(r.repo) ? '<em class="tag ours">ON STREAM</em>' : ""}</h3>
          <span class="meta"><span class="m-ap">${esc(r.approach)} · </span>${esc(r.target)}${r.stars ? ` · ⭐ ${nf(r.stars)}` : ""}<span class="m-push">${r.pushed_at ? ` · pushed ${ago(r.pushed_at)}` : ""}</span></span></div>
        <span class="stage s${si}">${esc(r.stage)}</span><span class="sc">${r.score}</span><span class="chev">›</span>
        <div class="bar"><i data-w="${r.score}"></i></div></summary>
      <div class="body">
        ${r.summary ? `<p class="sum">${esc(r.summary)}</p>` : ""}
        <div class="facts"><span>Verification: ${esc(r.verification || "unknown")}</span>${r.fps ? `<span>~${r.fps} fps</span>` : ""}${r.commits7d != null ? `<span>${r.commits7d >= 100 ? "100+" : r.commits7d} commits / 7d</span>` : ""}${r.proven_on ? `<span>Proven on: ${esc(r.proven_on)}</span>` : ""}${r.assessed_at ? `<span>Assessed ${esc(r.assessed_at)}</span>` : ""}${repoUrl ? `<a href="${esc(repoUrl)}" target="_blank" rel="noopener">github.com/${esc(r.repo)} ↗</a>` : `<span>${esc(r.byline || "No public code")}</span>`}</div>
        ${(r.highlights || []).length ? `<div class="ok"><h4>What works</h4><ul>${r.highlights.map(h => `<li>${esc(h)}</li>`).join("")}</ul></div>` : ""}
        ${(r.blockers || []).length ? `<div class="no"><h4>Blockers</h4><ul>${r.blockers.map(h => `<li>${esc(h)}</li>`).join("")}</ul></div>` : ""}
      </div></details>`;
  }).join("");
  animate();
}
document.addEventListener("click", e => {
  const f = e.target.closest("#flt button"), s2 = e.target.closest("#srt button");
  if (f) { FLT = f.dataset.f; document.querySelectorAll("#flt button").forEach(b => { b.classList.toggle("on", b === f); b.setAttribute("aria-pressed", b === f); }); renderBoard(); }
  if (s2) { SRT = s2.dataset.s; document.querySelectorAll("#srt button").forEach(b => { b.classList.toggle("on", b === s2); b.setAttribute("aria-pressed", b === s2); }); renderBoard(); }
});

function renderTrend(){
  const pw = D.portwatch, h = (pw.history || []).filter(x => x.leader != null).map(x => ({...x}));
  const today = new Date().toISOString().slice(0, 10);   // today's point uses the live scores
  if (pw.leader) { const live = {date: today, leader: pw.leader.score, top3: pw.top3, tracked: pw.watched};
    if (h.length && h[h.length - 1].date === today) h[h.length - 1] = live; else h.push(live); }
  if (h.length < 2) { $("#trend").hidden = true; return; }
  const W = 1000, H = 300, P = {l: 40, r: 20, t: 20, b: 34}, n = h.length;
  const x = i => P.l + (W - P.l - P.r) * (n === 1 ? .5 : i / (n - 1)), y = v => P.t + (H - P.t - P.b) * (1 - v / 100);
  const line = k => h.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[k]).toFixed(1)}`).join("");
  const grid = [0, 25, 50, 75, 100].map(v => `<line class="gl" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text class="lab" x="${P.l - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  const xl = h.map((d, i) => `<text class="lab" x="${x(i)}" y="${H - 10}" text-anchor="middle">${esc(d.date.slice(5))}</text>`).join("");
  const pts = h.map((d, i) => `<circle class="pt" cx="${x(i)}" cy="${y(d.leader)}" r="5" fill="#ff4f9a"/><text class="v" x="${x(i)}" y="${y(d.leader) - 12}" text-anchor="middle">${d.leader}</text>
    <circle class="pt" cx="${x(i)}" cy="${y(d.top3)}" r="4" fill="#7c4dff"/>`).join("");
  $("#trendbox").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Leader score and top-3 average by day">${grid}${xl}<path class="l2" d="${line("top3")}"/><path class="l1" d="${line("leader")}"/>${pts}</svg>
    <div class="legend"><span><i style="background:#ff4f9a"></i>Leading score</span><span><i style="background:#7c4dff"></i>Top-3 average</span><span>${h[h.length - 1].tracked} efforts tracked on ${esc(h[h.length - 1].date)}</span></div>`;
}

function renderTimeline(){
  const all = D.portwatch.changes || [], ic = {new: "+", up: "↑", down: "↓"};
  const scout = all.filter(c => /^Scout spotted/i.test(c.text)), ch = all.filter(c => !/^Scout spotted/i.test(c.text));
  if (scout.length) ch.push({date: scout[0].date, kind: "other", text: `+${scout.length} new repos spotted by PortWatch's scout and queued for review (${scout.slice(0, 4).map(c => (c.text.match(/spotted (\S+)/) || [])[1]).filter(Boolean).join(", ")}${scout.length > 4 ? ", …" : ""}).`});
  $("#timeline").innerHTML = ch.map(c => `<li><span class="when"><span class="ic ${ic[c.kind] ? esc(c.kind) : "other"}">${ic[c.kind] || "•"}</span><time>${esc(c.date)}</time></span><p>${esc(c.text)}</p></li>`).join("") || "<li><p>No changes yet.</p></li>";
}

function renderChallenger(){
  const pw = D.portwatch, ours = new Set(pw.ours || []);
  const c = pw.ranked.slice(0, 3).find(r => !ours.has(r.repo));
  $("#chal-sec").hidden = !c; if (!c) return;
  const best = pw.ranked.find(r => ours.has(r.repo)), gap = best ? c.score - best.score : null;
  $("#chal").innerHTML = `${avatar(c, "ini")}
    <div><div class="tags"><span class="pill red">${c.rank === 1 ? "New #1" : "Challenger #" + c.rank}</span>${c.private ? '<span class="pill">Private code</span>' : ""}<span class="pill">${esc(c.verification || "unverified")}</span></div>
      <h3>${esc(c.name)}</h3><p class="by">${esc(c.byline || c.approach)}${gap != null ? ` · ${gap >= 0 ? "+" : ""}${gap.toFixed(1)} vs ${esc(best.name)}` : ""}</p></div>
    <div class="big"><b>${c.score}</b><small>/100</small><span>${esc(c.stage)}${c.fps ? ` · ~${c.fps} fps` : ""}</span></div>
    <div class="cols"><div class="ok"><h4>What works</h4><ul>${(c.highlights || []).map(h => `<li>${esc(h)}</li>`).join("")}</ul></div>
      <div class="no"><h4>Blockers</h4><ul>${(c.blockers || []).map(h => `<li>${esc(h)}</li>`).join("")}</ul></div></div>`;
}

function renderProjects(){
  $("#proj").innerHTML = D.repos.map(r => {
    const p = r.progress || {}, c = p.completeness || 0, feats = p.features || [], done = feats.filter(f => f.score === 1).length;
    const mx = Math.max(1, ...(r.commits_by_hour || []));
    return `<article class="proj card reveal" style="--acc:${esc(r.accent)}">
      <div class="proj-h"><h3>${esc(r.label)}</h3><small>${esc(r.tagline)}</small><a href="${esc(r.url)}" target="_blank" rel="noopener">github.com/${esc(r.slug)} ↗</a></div>
      <div class="stats">
        <div class="st"><b>${nf(r.stars)}</b><span>Stars</span></div>
        <div class="st"><b>${nf(r.open_prs)}</b><span>Open PRs</span></div>
        <div class="st"><b>${nf(r.merged_today)}</b><span>Merged today</span></div>
        <div class="st"><b>${nf(r.total_prs)}</b><span>PRs so far</span></div>
        <div class="st"><b>${nf(r.commits_24h)}${r.commits_capped ? "+" : ""}</b><span>Commits 24h</span></div>
        <div class="st"><b>${nf(r.contributors)}</b><span>Contributors</span></div>
      </div>
      <div class="prog"><div class="ring"><svg viewBox="0 0 110 110"><circle class="t" cx="55" cy="55" r="46"/><circle class="p" cx="55" cy="55" r="46" data-c="${c}"/></svg>
        <div><span>${Math.round(c * 100)}%<small>CHECKLIST</small></span></div></div>
        <div class="meas">${(p.fractions || []).slice(0, 3).map(f => `<div><div class="r">${esc(f.label[0].toUpperCase() + f.label.slice(1))}<span class="mono">${nf(f.num)} / ${nf(f.den)}</span></div><div class="tk"><i data-w="${(f.num / f.den * 100).toFixed(2)}"></i></div></div>`).join("")
          || `<p class="h4">${done} of ${feats.length} README features done</p>`}</div></div>
      <div><p class="h4">Commits per hour · 24h</p><div class="hist">${(r.commits_by_hour || []).map(v => `<i style="height:${v / mx * 100}%"></i>`).join("")}</div><div class="hist-l"><span>24h ago</span><span>now</span></div></div>
      <div class="evs"><p class="h4" style="margin-bottom:6px">Latest pull requests</p>${(r.events || []).slice(0, 5).map(e => `
        <div class="ev"><span class="k ${esc(e.kind)}">${esc(e.kind[0].toUpperCase() + e.kind.slice(1))}</span>
          <div><p>#${e.n} ${esc(e.title)}</p><s>${esc(e.by)} · ${ago(e.at)}${e.additions != null ? ` · +${nf(e.additions)} −${nf(e.deletions)}` : ""}</s></div></div>`).join("")}</div>
    </article>`;
  }).join("");
}

function renderNews(){
  const w = D.watch, pw = D.portwatch, cards = [];
  (w?.items || []).slice(0, 3).forEach(it => cards.push({badge: `${w.name} watch`, cls: "w", src: it.source, title: it.title, text: it.dek || it.detail, at: it.at}));
  (pw.news || []).slice(0, 4).forEach(n => { if (!cards.some(c => c.title === n.title)) cards.push({badge: n.category || "News", src: "PortWatch", title: n.title, text: n.dek, at: n.date}); });
  (pw.headlines || []).slice(0, 3).forEach(h => cards.push({badge: "Press", src: h.outlet, title: h.title, text: "", at: h.date}));
  $("#newsgrid").innerHTML = cards.slice(0, 8).map((c, i) => `
    <article class="nc card reveal ${i === 0 ? (c.text ? "lead" : "lead flat") : ""}"><div class="src2"><span class="b ${c.cls || ""}">${esc(c.badge)}</span>${esc(c.src)} · ${esc(c.at && c.at.length > 10 ? ago(c.at) : c.at)}</div>
      <h3>${esc(c.title)}</h3>${c.text && (i === 0 || i < 3) ? `<p>${esc(c.text)}</p>` : ""}</article>`).join("");
  if (w?.sources?.length) $("#news-sub").textContent = `From ${["PortWatch", ...w.sources.filter(s => s !== "PortWatch")].join(", ")}. Only items that name a team are matched.`;
}

function renderLive(){
  const vid = (CFG.youtubeVideoId || "").trim(), id = (CFG.youtubeChannelId || "").trim();
  const watch = vid ? `https://www.youtube.com/watch?v=${encodeURIComponent(vid)}` : (CFG.youtubeChannelUrl || "").trim();
  let src = "";
  if (/^[\w-]{11}$/.test(vid)) src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(vid)}?rel=0`;
  else if (/^UC[\w-]{20,}$/.test(id)) src = `https://www.youtube-nocookie.com/embed/live_stream?channel=${encodeURIComponent(id)}`;
  $("#livebox").innerHTML = src
    ? `<iframe src="${src}" title="Porting Race live stream" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe>
       <div class="live-bar"><span><i class="dot"></i>Live 24/7 · same dashboard as this site, plus music and spotlights</span>${watch ? `<a class="btn hot" href="${esc(watch)}" target="_blank" rel="noopener"><i></i>Watch on YouTube</a>` : ""}</div>`
    : `<div class="ph"><b>Live on YouTube</b>${watch ? `<a class="btn hot" href="${esc(watch)}" target="_blank" rel="noopener"><i></i>Open the stream</a>` : "The player appears here once the channel is configured."}</div>`;
  if (watch) for (const a of document.querySelectorAll("#live-btn, .nav-live")) { a.href = watch; a.target = "_blank"; a.rel = "noopener"; }
}

function animate(){
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return; const el = e.target; el.classList.add("in");
    el.querySelectorAll("[data-w]").forEach(i => i.style.width = i.dataset.w + "%");
    el.querySelectorAll(".ring .p").forEach(c => c.style.strokeDashoffset = 289 * (1 - c.dataset.c));
    if (el.id === "ladder") layoutMarks();
    io.unobserve(el);
  }), {threshold: .15});
  document.querySelectorAll(".reveal, .ladder").forEach(el => io.observe(el));
}

async function load(){
  try {
    const r = await fetch(`data/live.json?t=${Math.floor(Date.now() / 6e4)}`, {cache: "no-cache"});
    D = await r.json();
  } catch (e) { $("#upd").textContent = "data unavailable"; return; }
  renderHero(); renderRace(); renderTrend(); renderTimeline(); renderChallenger(); renderProjects(); renderNews(); renderLive(); animate(); clock();
  $("#built").textContent = new Date(D.built_at).toUTCString();
}
setInterval(() => { if (D) $("#upd").textContent = `updated ${ago(D.built_at)}`; }, 1000);
addEventListener("scroll", () => {
  $("#nav").classList.toggle("scrolled", scrollY > 8);
  let cur = ""; for (const sec of document.querySelectorAll("section[id], header[id]")) if (sec.getBoundingClientRect().top < 140) cur = sec.id;
  document.querySelectorAll(".links a").forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + cur));
}, {passive: true});
load();
setInterval(load, 5 * 60 * 1000);   // pick up the next Actions build without a reload
