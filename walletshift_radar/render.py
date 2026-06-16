"""
render.py — terminal dashboard with Chart.js charts + interactive category explorer.

Layout:
  1. Header strip
  2. Vitals (4 stat cards with sparklines)
  3. Charts row (category breakdown · protocol mix · platform adoption)
  4. Signals row (newcomers · deathwatch · momentum board)
  5. Category explorer (filter tabs + live search + agent table)
  6. Secondary grid (cluster watch · skills movers · population)
  7. Footer

All data is JSON-embedded; Chart.js loaded from CDN; zero build step.
"""
import html as _html
import json
from .charts import sparkline, hbar, diverging_bar

_CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"

_CAT_SHORT = {
    "defi-yield-rebalancing":           "DeFi Yield",
    "conversational-assistant-general": "Conversational",
    "market-data-analytics":            "Market Data",
    "realworld-data-feeds":             "Data Feeds",
    "builder-infra-tooling":            "Dev Tools",
    "defi-trade-execution":             "Trading",
    "content-creative-generation":      "Content",
    "task-execution-services":          "Task Exec",
    "knowledge-reference-apis":         "Knowledge",
    "dev-infra-intelligence":           "Dev Intel",
    "identity-naming":                  "Identity",
    "security-risk-scoring":            "Security",
    "agent-economy-infrastructure":     "Agent Infra",
}

_CSS = """
:root{
  --bg:#0a0a0a;--fg:#00ff41;--fg2:#00cc33;--dim:#006622;
  --red:#ff3333;--amber:#ffaa00;--blue:#00aaff;--hi:#ccffcc;
  --border:#006622;--panel:#050505;
}
*{box-sizing:border-box;margin:0;padding:0}
html{overflow-x:hidden}
body{background:var(--bg);color:var(--fg);max-width:100vw;overflow-x:hidden;
     font-family:'Courier New',Courier,monospace;font-size:13px;
     line-height:1.5;padding:10px 14px}
/* header */
.hdr{display:flex;align-items:baseline;flex-wrap:wrap;gap:10px;
     border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:10px}
.hdr-logo{color:var(--hi);font-size:15px;font-weight:bold;letter-spacing:3px;white-space:nowrap}
.hdr-meta{color:var(--dim);font-size:11px}
.hdr-meta span{color:var(--fg)}
/* vitals */
.vitals{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
.vital{border:1px solid var(--border);padding:5px 12px;
       display:flex;align-items:center;gap:10px;min-width:110px}
.vital .val{font-size:22px;color:var(--hi);font-weight:bold;min-width:3ch;text-align:right}
.vital .meta{display:flex;flex-direction:column}
.vital .lbl{color:var(--dim);font-size:10px;letter-spacing:1px}
.vital .spark{color:var(--fg);font-size:13px;letter-spacing:0}
.vital .delta{font-size:10px;color:var(--dim)}
/* charts row */
.charts-row{display:grid;grid-template-columns:5fr 2fr 3fr;gap:10px;margin-bottom:10px}
@media(max-width:920px){.charts-row{grid-template-columns:1fr 1fr}
  .charts-row .panel:first-child{grid-column:1/-1}}
@media(max-width:600px){.charts-row{grid-template-columns:1fr}}
.chart-wrap{position:relative;height:220px;width:100%}
/* priority/signals row */
.priority{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px}
@media(max-width:900px){.priority{grid-template-columns:1fr 1fr}}
@media(max-width:600px){.priority{grid-template-columns:1fr}}
/* panels */
.panel{border:1px solid var(--border);padding:8px 10px;background:var(--panel);
       min-width:0;overflow:hidden}
.panel h2{color:var(--fg2);font-size:11px;letter-spacing:1px;
          border-bottom:1px solid var(--border);padding-bottom:3px;margin-bottom:6px;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* tables */
.tbl-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:11px}
th{color:var(--dim);text-align:left;border-bottom:1px solid #1a331a;
   padding:1px 5px;white-space:nowrap}
td{padding:2px 5px;border-bottom:1px solid #0d1a0d;white-space:nowrap;
   overflow:hidden;text-overflow:ellipsis;max-width:180px}
tr:hover td{background:#0a1f0a}
/* badges */
.badge{display:inline-block;border:1px solid var(--dim);padding:0 3px;
       font-size:9px;margin-right:2px;color:var(--dim)}
.bl{border-color:var(--fg);color:var(--fg)}
.bx{border-color:var(--amber);color:var(--amber)}
.bo{border-color:var(--blue);color:var(--blue)}
/* category explorer */
.cat-tabs{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.cat-tab{background:none;border:1px solid var(--border);color:var(--dim);
         font-family:inherit;font-size:10px;padding:2px 8px;cursor:pointer;
         letter-spacing:1px;transition:border-color .12s,color .12s}
.cat-tab:hover,.cat-tab:focus{border-color:var(--fg2);color:var(--fg2);outline:none}
.cat-tab.active{background:var(--fg);color:#000;border-color:var(--fg)}
.xbar{display:flex;align-items:center;gap:10px;margin-bottom:5px;flex-wrap:wrap}
.xsearch{background:none;border:1px solid var(--border);color:var(--fg);
          font-family:inherit;font-size:11px;padding:3px 8px;
          flex:1;min-width:180px;outline:none}
.xsearch::placeholder{color:var(--dim)}
.xsearch:focus{border-color:var(--fg2)}
.xtoggle{color:var(--dim);font-size:10px;cursor:pointer;white-space:nowrap;
          display:flex;align-items:center;gap:4px}
.xtoggle input{accent-color:var(--fg);cursor:pointer}
.xcount{color:var(--dim);font-size:10px;margin-bottom:4px}
.sortable{cursor:pointer;user-select:none}
.sortable:hover{color:var(--fg2)}
th.sort-asc::after{content:' ▲';color:var(--fg)}
th.sort-desc::after{content:' ▼';color:var(--fg)}
/* secondary grid */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
      gap:10px;margin-bottom:10px}
/* misc */
.dim{color:var(--dim)}.hi{color:var(--hi)}.red{color:var(--red)}
.amb{color:var(--amber)}.blu{color:var(--blue)}
.bp{color:var(--fg)}.bn{color:var(--red)}
.dp{color:var(--fg)}.dn{color:var(--red)}
pre{white-space:pre;color:var(--fg);font-size:11px;line-height:1.3;overflow-x:auto}
footer{margin-top:12px;color:var(--dim);font-size:10px;
       border-top:1px solid var(--border);padding-top:5px;flex-wrap:wrap}
"""

_JS = """
/* ── Chart.js theme defaults ─────────────────────────────────────────────── */
Chart.defaults.color          = '#006622';
Chart.defaults.borderColor    = '#0d1f0d';
Chart.defaults.font.family    = "'Courier New', monospace";
Chart.defaults.font.size      = 10;
const _COLORS = ['#00ff41','#ffaa00','#00aaff','#ff9933','#cc44ff','#ff3366'];

/* ── 1. Category breakdown (horizontal bar) ──────────────────────────────── */
(function(){
  const d = CAT_CHART;
  if (!d.length) return;
  new Chart(document.getElementById('catChart'), {
    type: 'bar',
    data: {
      labels: d.map(r => r.short || r.category || '?'),
      datasets: [
        { label: 'Agents',         data: d.map(r => r.n),
          backgroundColor: '#00cc3370', borderColor: '#00cc33', borderWidth: 1 },
        { label: 'Live endpoints', data: d.map(r => r.total_live),
          backgroundColor: '#00ff4145', borderColor: '#00ff41', borderWidth: 1 },
        { label: 'Dead endpoints', data: d.map(r => r.total_dead),
          backgroundColor: '#ff333340', borderColor: '#ff3333', borderWidth: 1 },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#006622', boxWidth: 10, padding: 6 } } },
      scales: {
        x: { ticks: { color: '#006622' }, grid: { color: '#0d1a0d' } },
        y: { ticks: { color: '#00cc33', font: { size: 9 } }, grid: { color: '#0d1a0d' } }
      }
    }
  });
})();

/* ── 2. Protocol mix (doughnut) ──────────────────────────────────────────── */
(function(){
  const d = PROTO_DIST;
  const keys = Object.keys(d), vals = keys.map(k => d[k]);
  if (!keys.length) return;
  const cols = ['#00cc33','#ffaa00','#00aaff','#ff3333','#cc44ff','#ff9933'];
  new Chart(document.getElementById('protoChart'), {
    type: 'doughnut',
    data: {
      labels: keys,
      datasets: [{ data: vals,
        backgroundColor: cols.map(c => c + '70'),
        borderColor: cols, borderWidth: 1.5 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position:'bottom',
          labels: { color:'#00cc33', boxWidth:10, padding:6, font:{size:9} } }
      }
    }
  });
})();

/* ── 3. Platform adoption (cumulative by token ID) ───────────────────────── */
(function(){
  const platforms = PLATFORM_SERIES;
  if (!platforms.length) return;
  const MAX_ID = 36000, BUCKETS = 30;
  const bsz = MAX_ID / BUCKETS;
  const labels = Array.from({length: BUCKETS}, (_, i) =>
    Math.round((i + 0.5) * bsz / 1000) + 'k');

  const datasets = platforms.map((p, i) => {
    const counts = Array(BUCKETS).fill(0);
    p.ids.forEach(id => {
      const b = Math.min(Math.floor(id / bsz), BUCKETS - 1);
      counts[b]++;
    });
    const cumul = counts.reduce((acc, v) => {
      acc.push((acc.length ? acc[acc.length-1] : 0) + v); return acc;
    }, []);
    const c = _COLORS[i % _COLORS.length];
    const shortName = p.name.length > 22 ? p.name.slice(0,22)+'…' : p.name;
    return {
      label: shortName + ' (' + p.count + ')',
      data: cumul,
      borderColor: c, backgroundColor: c + '18',
      fill: true, stepped: 'after', borderWidth: 1.5, pointRadius: 0
    };
  });

  new Chart(document.getElementById('platformChart'), {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color:'#006622', boxWidth:10, padding:5, font:{size:9} } },
        tooltip: {
          callbacks: {
            title: ctx => 'Token ID ~' + ctx[0].label,
            label: ctx => ctx.dataset.label.split(' (')[0] + ': ' + ctx.parsed.y + ' instances'
          }
        }
      },
      scales: {
        x: { title: { display:true, text:'Token ID (mint sequence →)', color:'#006622', font:{size:9} },
             ticks: { color:'#006622', maxRotation:0, maxTicksLimit:8 },
             grid: { color:'#0d1a0d' } },
        y: { title: { display:true, text:'Cumulative instances', color:'#006622', font:{size:9} },
             ticks: { color:'#006622' }, grid: { color:'#0d1a0d' } }
      }
    }
  });
})();

/* ── 4. Category explorer ────────────────────────────────────────────────── */
(function(){
  let currentCat = '';
  let searchTerm  = '';
  let sortKey     = 'score';
  let sortDir     = -1;      // -1 = desc
  let activeOnly  = true;    // hide ghost agents by default

  const tbody  = document.getElementById('xtbody');
  const countEl= document.getElementById('xcount');
  const LIMIT  = 150;

  /* ── mini visualisations ── */
  function healthBar(live, dead) {
    const total = live + dead;
    if (!total) return '<span class="dim">—</span>';
    const W = 6, filled = Math.round(live / total * W);
    const ratio = live / total;
    const cls = ratio >= 0.7 ? 'hi' : ratio >= 0.35 ? 'amb' : 'red';
    return '<span class="' + cls + '" style="letter-spacing:0">' +
      '█'.repeat(filled) + '░'.repeat(W - filled) +
      '</span><span class="dim"> ' + live + '↑' + dead + '↓</span>';
  }

  function scoreBar(s) {
    if (s == null || s === 0) return '<span class="dim">—</span>';
    const W = 5, filled = Math.round(s * W);
    const cls = s >= 0.65 ? 'hi' : s >= 0.35 ? 'amb' : 'red';
    return '<span class="' + cls + '">' + s.toFixed(2) +
      ' <span style="letter-spacing:0">' +
      '█'.repeat(filled) + '░'.repeat(W - filled) +
      '</span></span>';
  }

  function protoBadges(json) {
    return (JSON.parse(json || '[]')).map(p => {
      const cls = p === 'x402' ? 'bx' : p === 'web' ? 'bo' : 'bl';
      return '<span class="badge ' + cls + '">' + p + '</span>';
    }).join('');
  }

  function catShort(cat) {
    return cat ? (CAT_SHORT[cat] || cat.slice(0, 14)) : '—';
  }

  /* ── sort + filter + render ── */
  function applyAndRender() {
    let rows = AGENTS_DATA.filter(a => {
      if (activeOnly && a.live_count + a.dead_count + a.skills_count === 0) return false;
      if (currentCat && a.category !== currentCat) return false;
      if (searchTerm) {
        const hay = (a.name + ' ' + (a.cluster_key || '') + ' ' + (a.category || '')).toLowerCase();
        if (!hay.includes(searchTerm)) return false;
      }
      return true;
    });

    rows.sort((a, b) => {
      if (sortKey === 'name') return a.name.localeCompare(b.name) * sortDir;
      const av = a[sortKey] != null ? a[sortKey] : -Infinity;
      const bv = b[sortKey] != null ? b[sortKey] : -Infinity;
      return (bv - av) * sortDir;
    });

    const shown = Math.min(rows.length, LIMIT);
    countEl.textContent = 'Showing ' + shown +
      (rows.length > LIMIT ? ' of ' + rows.length : '') +
      ' / ' + AGENTS_DATA.length + ' total';

    tbody.innerHTML = rows.slice(0, LIMIT).map(a => {
      const nameTrunc = a.name.length > 34 ? a.name.slice(0, 34) + '…' : a.name;
      return '<tr>' +
        '<td class="dim">' + a.token_id + '</td>' +
        '<td title="' + a.name.replace(/[<>"]/g,'') + '">' + nameTrunc + '</td>' +
        '<td class="dim" title="' + (a.category||'') + '">' + catShort(a.category) + '</td>' +
        '<td>' + healthBar(a.live_count, a.dead_count) + '</td>' +
        '<td>' + scoreBar(a.score) + '</td>' +
        '<td class="dim">' + (a.skills_count || '—') + '</td>' +
        '<td>' + protoBadges(a.protos_json) + '</td>' +
        '<td class="' + (a.source === 'onchain' ? 'amb' : 'dim') + '">' +
          (a.source === 'onchain' ? '⬡' : '·') + '</td>' +
        '</tr>';
    }).join('') || '<tr><td colspan="8" class="dim">— no results —</td></tr>';
  }

  /* ── sort header clicks ── */
  document.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (sortKey === key) { sortDir *= -1; }
      else { sortKey = key; sortDir = -1; }
      document.querySelectorAll('th.sortable').forEach(h => {
        h.classList.remove('sort-asc','sort-desc');
      });
      th.classList.add(sortDir === -1 ? 'sort-desc' : 'sort-asc');
      applyAndRender();
    });
  });

  /* ── category tab clicks ── */
  document.querySelectorAll('.cat-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.cat-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentCat = btn.dataset.cat;
      applyAndRender();
    });
  });

  /* ── search ── */
  document.getElementById('xsearch').addEventListener('input', e => {
    searchTerm = e.target.value.toLowerCase();
    applyAndRender();
  });

  /* ── active-only toggle ── */
  document.getElementById('xactive').addEventListener('change', e => {
    activeOnly = e.target.checked;
    applyAndRender();
  });

  /* initial render — mark score column as sorted desc */
  document.querySelector('th[data-sort="score"]').classList.add('sort-desc');
  applyAndRender();
})();
"""


def _e(s) -> str:
    return _html.escape(str(s) if s is not None else "")


def _d(val, prefix="") -> str:
    if val is None:
        return '<span class="dim">—</span>'
    sign = "+" if val > 0 else ""
    cls  = "dp" if val >= 0 else "dn"
    return f'<span class="{cls}">{prefix}{sign}{val}</span>'


def _proto_badges(protos) -> str:
    if not protos:
        return ""
    tag = {"a2a": "bl", "mcp": "bl", "x402": "bx", "web": "bo", "ens": "dim"}
    return "".join(
        f'<span class="badge {tag.get(p,"dim")}">{_e(p)}</span>'
        for p in (protos if isinstance(protos, list) else json.loads(protos or "[]"))
    )


def _vital_card(label, col, summ, hist) -> str:
    vals = [h.get(col) for h in hist if h.get(col) is not None]
    cur  = summ.get(col, vals[-1] if vals else 0) or 0
    spk  = sparkline(vals) if len(vals) > 1 else "·"
    d1   = (cur - vals[-2]) if len(vals) >= 2 and vals[-2] is not None else None
    d7   = (cur - vals[-8]) if len(vals) >= 8 and vals[-8] is not None else None
    return f"""<div class="vital">
  <div class="val">{cur}</div>
  <div class="meta">
    <span class="lbl">{_e(label)}</span>
    <span class="spark">{_e(spk)}</span>
    <span class="delta">1d {_d(d1)} &nbsp; 7d {_d(d7)}</span>
  </div>
</div>"""


def _rows(agents, cols, limit=15) -> str:
    out = []
    for a in agents[:limit]:
        cells = []
        for col in cols:
            v = a.get(col)
            if col in ("protos", "protos_json"):
                cells.append(_proto_badges(v))
            elif col == "x402":
                cells.append('<span class="badge bx">x402</span>' if v else "")
            elif col == "score":
                bar = hbar(float(v or 0), 1.0, width=8)
                cells.append(f'<span class="hi">{float(v or 0):.2f}</span>'
                              f'<span class="bp"> {_e(bar)}</span>')
            elif col == "source":
                cls = "dim" if v == "walletshift" else "amb"
                cells.append(f'<span class="{cls}">{_e((v or "—")[:4])}</span>')
            elif col in ("live_count",):
                cells.append(f'<span class="hi">{v or 0}</span>')
            elif col in ("dead_count",):
                cells.append(f'<span class="red">{v or 0}</span>')
            else:
                s = str(v)[:28] if v is not None else ""
                cells.append(_e(s) if s else '<span class="dim">—</span>')
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    if not out:
        return '<tr><td colspan="20" class="dim">— none —</td></tr>'
    return "".join(out)


def _tbl(headers, cols, agents, limit=15) -> str:
    ths = "".join(f"<th>{_e(h)}</th>" for h in headers)
    return (f'<div class="tbl-wrap"><table><tr>{ths}</tr>'
            + _rows(agents, cols, limit)
            + "</table></div>")


def render_dashboard(data: dict, run_date: str) -> str:
    hist   = data.get("directory_history", [])
    summ   = data.get("summary", {})
    cats   = data.get("categories", [])
    new_   = data.get("newcomers", [])
    drops  = data.get("dropouts", [])
    flips  = data.get("health_flips_dead", [])
    s_up   = data.get("top_skills_up", [])
    s_dn   = data.get("top_skills_down", [])
    mom    = data.get("momentum_board", [])
    death  = data.get("deathwatch", [])
    cw     = data.get("cluster_watch", [])
    reg_h  = data.get("reg_histogram", [])
    # chart data
    cat_chart       = data.get("cat_chart", [])
    platform_series = data.get("platform_series", [])
    proto_dist      = data.get("proto_dist", {})
    agents_all      = data.get("agents_all", [])

    total    = summ.get("total_agents", 0)
    chain    = summ.get("chain_only", 0)
    last_blk = summ.get("last_scanned_block", "—")

    # ── category label lookup ─────────────────────────────────────────────────
    cat_label = {c.get("key", ""): c.get("label", "") for c in cats}

    # ── 1. Header ─────────────────────────────────────────────────────────────
    header = f"""<div class="hdr">
  <span class="hdr-logo">◈ WALLETSHIFT RADAR</span>
  <span class="hdr-meta">
    <span>{_e(run_date)}</span> │
    ERC-8004 Ethereum mainnet │
    block <span class="amb">{_e(str(last_blk))}</span> │
    <span class="hi">{total}</span> agents
    {f'(+<span class="amb">{chain}</span> chain-only)' if chain else ""}
  </span>
</div>"""

    # ── 2. Vitals ─────────────────────────────────────────────────────────────
    vitals = '<div class="vitals">' + (
        _vital_card("AGENTS",         "total_agents",      summ, hist)
        + _vital_card("PRODUCTS",     "distinct_products", summ, hist)
        + _vital_card("LIVE EPs",     "live_skills_read",  summ, hist)
        + _vital_card("X402",         "x402_count",        summ, hist)
    ) + "</div>"

    # ── 3. Charts row ─────────────────────────────────────────────────────────
    # Build chart data with short labels for the category bar
    cat_chart_js = []
    for r in cat_chart:
        key = r.get("category", "")
        cat_chart_js.append({
            "category": key,
            "short":    _CAT_SHORT.get(key, key[:12] if key else "?"),
            "n":        r.get("n", 0),
            "total_live": r.get("total_live", 0),
            "total_dead": r.get("total_dead", 0),
            "x402_n":   r.get("x402_n", 0),
        })

    charts_row = f"""<div class="charts-row">
  <div class="panel">
    <h2>◈ CATEGORY BREAKDOWN <span class="dim">agents · live · dead endpoints</span></h2>
    <div class="chart-wrap"><canvas id="catChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>◈ PROTOCOL MIX</h2>
    <div class="chart-wrap"><canvas id="protoChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>◈ PLATFORM ADOPTION <span class="dim">by mint sequence</span></h2>
    <div class="chart-wrap"><canvas id="platformChart"></canvas></div>
  </div>
</div>"""

    # ── 4. Signals row ────────────────────────────────────────────────────────
    newcomer_html = _tbl(
        ["#", "Name", "Cat", "Skills", "Live", "Dead", "Protos", "Src"],
        ["id", "name", "category", "skills_count", "live_count", "dead_count", "protos", "source"],
        new_, limit=20,
    ) if new_ else '<span class="dim">— none since last scan —</span>'

    death_entries = (
        [{"id": a.get("id"), "name": a.get("name"), "signal": "DROPOUT",
          "live_count": 0, "dead_count": "—"} for a in drops[:6]]
        + [{"id": a.get("id"), "name": a.get("name"), "signal": "ALL DEAD",
            "live_count": 0, "dead_count": a.get("curr_dead", "?")} for a in flips[:6]]
        + [{"id": a.get("id"), "name": a.get("name"),
            "signal": f'score {a.get("score",0):.2f}',
            "live_count": a.get("live_count", 0),
            "dead_count": a.get("dead_count", 0)} for a in death[:6]]
    )
    death_html = _tbl(
        ["#", "Name", "Signal", "Live", "Dead"],
        ["id", "name", "signal", "live_count", "dead_count"],
        death_entries, limit=20,
    ) if death_entries else '<span class="dim">— all healthy —</span>'

    mom_html = _tbl(
        ["#", "Name", "Score", "Live", "Dead", "Protos"],
        ["id", "name", "score", "live_count", "dead_count", "protos"],
        mom, limit=15,
    ) if mom else '<span class="dim">— none yet —</span>'

    signals_row = f"""<div class="priority">
  <div class="panel">
    <h2>▶ NEWCOMERS <span class="dim">since last scan</span></h2>
    {newcomer_html}
  </div>
  <div class="panel">
    <h2>☠ DEATHWATCH <span class="red">—</span> dropouts · dead endpoints</h2>
    {death_html}
  </div>
  <div class="panel">
    <h2>⬆ MOMENTUM BOARD <span class="dim">composite score</span></h2>
    {mom_html}
  </div>
</div>"""

    # ── 5. Category explorer ──────────────────────────────────────────────────
    # Generate tab buttons from known categories (sorted by agent count)
    tab_buttons = ['<button class="cat-tab active" data-cat="">ALL</button>']
    for c in sorted(cats, key=lambda x: x.get("count", 0), reverse=True):
        key   = c.get("key", "")
        short = _CAT_SHORT.get(key, key[:14] if key else "?")
        cnt   = c.get("count", 0)
        tab_buttons.append(
            f'<button class="cat-tab" data-cat="{_e(key)}">'
            f'{_e(short)} <span class="dim">({cnt})</span></button>'
        )
    tabs_html = "\n".join(tab_buttons)

    explorer = f"""<div class="panel" style="margin-bottom:10px">
  <h2>◈ CATEGORY EXPLORER
    <span class="dim">click category · type to search · click column to sort</span>
  </h2>
  <div class="cat-tabs">{tabs_html}</div>
  <div class="xbar">
    <input class="xsearch" id="xsearch" placeholder="filter by name, cluster, category…">
    <label class="xtoggle">
      <input type="checkbox" id="xactive" checked>
      active only
    </label>
  </div>
  <div class="xcount" id="xcount"></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th class="sortable" data-sort="token_id">#</th>
        <th class="sortable" data-sort="name">Name</th>
        <th>Category</th>
        <th class="sortable" data-sort="live_count">Health</th>
        <th class="sortable" data-sort="score">Score</th>
        <th class="sortable" data-sort="skills_count">Skills</th>
        <th>Protos</th>
        <th>Src</th>
      </tr></thead>
      <tbody id="xtbody"></tbody>
    </table>
  </div>
</div>"""

    # ── 6. Secondary grid ─────────────────────────────────────────────────────
    # Cluster watch
    if cw:
        cw_rows = []
        for c in cw[:10]:
            hist_vals = c.get("history", [])
            spk = sparkline(hist_vals) if len(hist_vals) > 1 else "·"
            cur = hist_vals[-1] if hist_vals else 0
            cw_rows.append(
                f'<tr><td>{_e(c["cluster_key"][:28])}</td>'
                f'<td class="hi">{cur}</td>'
                f'<td style="letter-spacing:0">{_e(spk)}</td></tr>'
            )
        cw_html = ('<div class="tbl-wrap"><table>'
                   '<tr><th>Platform</th><th>Instances</th><th>Trend</th></tr>'
                   + "".join(cw_rows) + "</table></div>")
    else:
        cw_html = '<span class="dim">— none —</span>'

    # Skills movers
    max_d = max((a.get("delta", 0) for a in s_up + s_dn), default=1) or 1
    mover_rows = []
    for a in s_up[:5]:
        bar = diverging_bar(a.get("delta", 0), 0, max_d, width=14)
        mover_rows.append(
            f'<tr><td class="hi">+{a.get("delta",0)}</td>'
            f'<td>{_e(str(a.get("name",""))[:22])}</td>'
            f'<td class="bp" style="letter-spacing:0">{_e(bar)}</td></tr>'
        )
    for a in s_dn[:4]:
        bar = diverging_bar(0, a.get("delta", 0), max_d, width=14)
        mover_rows.append(
            f'<tr><td class="red">−{a.get("delta",0)}</td>'
            f'<td>{_e(str(a.get("name",""))[:22])}</td>'
            f'<td class="bn" style="letter-spacing:0">{_e(bar)}</td></tr>'
        )
    mover_html = ('<div class="tbl-wrap"><table>'
                  '<tr><th>Δ</th><th>Name</th><th>Bar</th></tr>'
                  + "".join(mover_rows) + "</table></div>") if mover_rows else \
                 '<span class="dim">— populates day 2+ —</span>'

    # Category leaderboard
    if cats:
        max_cnt = max(c.get("count", 0) for c in cats) or 1
        cat_rows = []
        for c in sorted(cats, key=lambda x: x.get("count", 0), reverse=True):
            cnt  = c.get("count", 0)
            dp   = c.get("distinct_products", cnt)
            dlt  = c.get("delta")
            bar  = hbar(cnt, max_cnt, width=12)
            cat_rows.append(
                f'<tr>'
                f'<td title="{_e(c.get("key",""))}">{_e(c["label"][:28])}</td>'
                f'<td class="hi">{cnt}</td>'
                f'<td>{_d(dlt)}</td>'
                f'<td class="dim">{dp}</td>'
                f'<td class="bp" style="letter-spacing:0">{_e(bar)}</td>'
                f'</tr>'
            )
        cat_lb_html = ('<div class="tbl-wrap"><table>'
                       '<tr><th>Category</th><th>N</th><th>Δ</th><th>Prod</th><th>▓</th></tr>'
                       + "".join(cat_rows) + "</table></div>")
    else:
        cat_lb_html = '<span class="dim">— none —</span>'

    # Population histogram
    if reg_h:
        max_rh = max(r["count"] for r in reg_h) or 1
        rows_h = []
        for r in reg_h[-28:]:
            bar = hbar(r["count"], max_rh, width=22)
            rows_h.append(f'{str(r["week"])[-5:]} {_e(bar)} {r["count"]}')
        pop_html = f'<pre>{chr(10).join(rows_h)}</pre>'
    else:
        pop_html = '<span class="dim">— populates after walletshift reg dates load —</span>'

    grid = f"""<div class="grid">
  <div class="panel">
    <h2>◈ CLUSTER WATCH <span class="dim">wallet-instance deployers</span></h2>
    {cw_html}
  </div>
  <div class="panel">
    <h2>◈ CATEGORY LEADERBOARD</h2>{cat_lb_html}
  </div>
  <div class="panel">
    <h2>◈ SKILLS MOVERS <span class="dim">Δ since last scan</span></h2>
    {mover_html}
  </div>
  <div class="panel">
    <h2>◈ POPULATION <span class="dim">weekly registrations</span></h2>
    {pop_html}
  </div>
</div>"""

    # ── Embedded JS data ──────────────────────────────────────────────────────
    # Build CAT_SHORT map for JS (JS object literal from Python dict)
    cat_short_js = json.dumps({k: v for k, v in _CAT_SHORT.items()})

    data_block = f"""<script>
const CAT_CHART      = {json.dumps(cat_chart_js)};
const PROTO_DIST     = {json.dumps(proto_dist)};
const PLATFORM_SERIES= {json.dumps(platform_series)};
const AGENTS_DATA    = {json.dumps(agents_all)};
const CAT_SHORT      = {cat_short_js};
</script>"""

    footer = (
        '<footer>'
        'WalletShift Radar &nbsp;│&nbsp; ERC-8004 on Ethereum mainnet &nbsp;│&nbsp;'
        'Alchemy RPC + thewalletshift.com seed &nbsp;│&nbsp;'
        '<span class="dim">signals: endpoint health · registry mints · skills counts — '
        'no volume/revenue data</span>'
        '</footer>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="86400">
<title>WalletShift Radar — {_e(run_date)}</title>
<style>{_CSS}</style>
<script src="{_CHARTJS_CDN}"></script>
</head>
<body>
{header}
{vitals}
{charts_row}
{signals_row}
{explorer}
{grid}
{footer}
{data_block}
<script>{_JS}</script>
</body>
</html>"""
