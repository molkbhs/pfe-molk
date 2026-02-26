/* ══════════════════════════════════════════════════
   charts.js  —  Analytics Dashboard  v5
   + Sauvegarde KPIs → /api/kpi/save (valeur_kpi)
   + Bouton "💾 Sauvegarder KPIs" dans le dashboard
══════════════════════════════════════════════════ */

const API = (window.location.origin || 'http://127.0.0.1:5000') + '/api';

// ── Auth ───────────────────────────────────────────
const user = JSON.parse(localStorage.getItem('user') || 'null');
if (!user) { window.location.href = 'index.html'; }
else {
  const navUserEl = document.getElementById('navUser');
  if (navUserEl) navUserEl.textContent = user.username || user.email;
  if (user.role === 'admin') {
    const navAdmin = document.getElementById('navAdminItem');
    if (navAdmin) navAdmin.style.display = 'list-item';
  }
}
document.getElementById('logoutNav')?.addEventListener('click', () => {
  localStorage.clear(); sessionStorage.clear();
  window.location.href = 'index.html';
});

// ════════════════════════════════════════════════════
// STATE
// ════════════════════════════════════════════════════
let DS        = null;
let ANALYSIS  = null;
let TYPES     = null;
let CHARTS    = {};

let focusCol  = null;
let focusStat = 'sum';

let modalSearch  = '';
let modalSortCol = -1;
let modalSortAsc = true;

// ── Palette ────────────────────────────────────────
const PAL = [
  '#00e5cc','#7c6ff7','#ff6b6b','#ffd166',
  '#06d6a0','#38bdf8','#fb923c','#e879f9',
  '#4ade80','#facc15','#60a5fa','#f472b6',
];

const STATS = {
  sum:   { label:'Somme',   icon:'∑',  desc:'Total' },
  avg:   { label:'Moyenne', icon:'x̄',  desc:'Moy.' },
  min:   { label:'Minimum', icon:'↓',  desc:'Min.' },
  max:   { label:'Maximum', icon:'↑',  desc:'Max.' },
  med:   { label:'Médiane', icon:'⊕',  desc:'Méd.' },
  count: { label:'Count',   icon:'#',  desc:'Cnt.' },
};

const KPI_COLORS = [
  { cls:'k-t', hex:'#00e5cc' },
  { cls:'k-v', hex:'#7c6ff7' },
  { cls:'k-c', hex:'#ff6b6b' },
  { cls:'k-a', hex:'#ffd166' },
];

// ════════════════════════════════════════════════════
// CHART.JS DEFAULTS
// ════════════════════════════════════════════════════
function setDefaults() {
  Chart.defaults.color       = '#3d5470';
  Chart.defaults.borderColor = 'rgba(255,255,255,0.04)';
  Chart.defaults.font.family = "'JetBrains Mono','DM Mono',monospace";
  Chart.defaults.font.size   = 11;
  Chart.defaults.plugins.legend.display = false;
}

// ════════════════════════════════════════════════════
// TOAST
// ════════════════════════════════════════════════════
function toast(msg, type = 'ok') {
  const t = document.getElementById('toastMsg');
  if (!t) return;
  document.getElementById('toastText').textContent = msg;
  t.className = `toast-msg visible${type === 'err' ? ' err' : ''}`;
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('visible'), 3800);
}

// ════════════════════════════════════════════════════
// LOAD DATA
// ════════════════════════════════════════════════════
function loadData() {
  if (typeof Chart === 'undefined') { showEmpty(); return; }

  const raw = localStorage.getItem('etl_result');
  if (!raw) { showEmpty(); return; }

  let result;
  try { result = JSON.parse(raw); }
  catch (e) { showEmpty(); return; }

  const preview = result.preview || result.data || result.rows || [];
  if (!preview || !preview.length) { showEmpty(); return; }

  const headers = Object.keys(preview[0]);
  const rows    = preview.map(r => headers.map(h => {
    const v = r[h];
    return (v === null || v === undefined) ? '' : v;
  }));

  DS = {
    headers, rows,
    filename: result.filename || 'données.csv',
    rowCount: result.stats?.lignes   ?? rows.length,
    colCount: result.stats?.colonnes ?? headers.length,
    stats:    result.stats || {},
    savedAt:  result.saved_at || null,
  };

  ANALYSIS = analyzeColumns(headers, rows);
  TYPES    = getTypes(ANALYSIS);
  focusCol  = TYPES.num[0] || null;
  focusStat = 'sum';

  renderDashboard();
}

// ════════════════════════════════════════════════════
// ANALYSIS
// ════════════════════════════════════════════════════
function analyzeColumns(headers, rows) {
  return Object.fromEntries(headers.map((h, i) => {
    const vals   = rows.map(r => r[i]).filter(v => v !== '' && v != null);
    const sample = vals.slice(0, 300);
    const numP   = sample.map(v => parseFloat(String(v).replace(/[,\s]/g,''))).filter(v => !isNaN(v));
    const isNum  = numP.length / Math.max(sample.length, 1) > 0.7;
    const isDate = !isNum && sample.some(v => /\d{4}[-/]\d{2}/.test(String(v)));
    const uniq   = new Set(vals.map(v => String(v).toLowerCase())).size;
    const isCat  = !isNum && !isDate && uniq <= Math.min(30, vals.length * 0.5) && uniq > 1;
    const numVals = isNum
      ? rows.map(r => { const v = parseFloat(String(r[i]).replace(/[,\s]/g,'')); return isNaN(v)?0:v; })
      : [];
    return [h, { index:i, isNum, isDate, isCat, isText:!isNum&&!isDate&&!isCat, uniques:uniq, numVals }];
  }));
}

function getTypes(a) {
  const e = Object.entries(a);
  return {
    num:  e.filter(([,v]) => v.isNum).map(([k]) => k),
    cat:  e.filter(([,v]) => v.isCat).map(([k]) => k),
    date: e.filter(([,v]) => v.isDate).map(([k]) => k),
  };
}

// ════════════════════════════════════════════════════
// STAT ENGINE
// ════════════════════════════════════════════════════
function calcStat(vals, stat) {
  const v = vals.filter(x => !isNaN(x) && isFinite(x));
  if (!v.length) return 0;
  switch (stat) {
    case 'sum':   return v.reduce((a,b) => a+b, 0);
    case 'avg':   return v.reduce((a,b) => a+b, 0) / v.length;
    case 'min':   return Math.min(...v);
    case 'max':   return Math.max(...v);
    case 'count': return v.length;
    case 'med': {
      const s=[...v].sort((a,b)=>a-b), m=Math.floor(s.length/2);
      return s.length%2 ? s[m] : (s[m-1]+s[m])/2;
    }
  }
  return 0;
}

// ════════════════════════════════════════════════════
// DELTA CALC (réutilisé pour save)
// ════════════════════════════════════════════════════
function calcDeltaPct(vals) {
  const v = vals.filter(x => !isNaN(x) && isFinite(x));
  if (v.length < 4) return 0;
  const h  = Math.floor(v.length / 2);
  const a1 = v.slice(0, h).reduce((s, x) => s + x, 0) / h;
  const a2 = v.slice(h).reduce((s, x) => s + x, 0) / (v.length - h);
  if (!a1) return 0;
  return parseFloat(((a2 - a1) / Math.abs(a1) * 100).toFixed(2));
}

// ════════════════════════════════════════════════════
// ✅ SAVE KPIs → /api/kpi/save
// ════════════════════════════════════════════════════
async function saveKpisToDb(replace = true) {
  if (!DS || !TYPES) { toast('Aucune donnée chargée', 'err'); return; }

  const token  = localStorage.getItem('token') || localStorage.getItem('access_token');
  const now    = new Date();
  const periode = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;

  // Construire la liste des KPIs (toutes les colonnes numériques, toutes les stats actives)
  const kpis = [];

  TYPES.num.forEach(col => {
    const a = ANALYSIS[col];

    // Toutes les 6 stats pour chaque colonne numérique
    Object.keys(STATS).forEach(stat => {
      const valeur = calcStat(a.numVals, stat);
      kpis.push({
        kpiNom:        `${col}_${stat}`,   // ex: "montant_sum", "montant_avg"
        periode,
        valeur,
        evolution:     stat === 'sum' ? calcDeltaPct(a.numVals) : 0,
        departementId: null,
        stat_type:     stat,
        source:        DS.filename,
      });
    });
  });

  if (!kpis.length) {
    toast('Aucune colonne numérique à sauvegarder', 'err');
    return;
  }

  // Afficher bouton en loading
  const btn = document.getElementById('btnSaveKpis');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Sauvegarde…'; }

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const res = await fetch(`${API}/kpi/save`, {
      method:  'POST',
      headers,
      body:    JSON.stringify({ kpis, replace, source: DS.filename }),
    });

    const data = await res.json();

    if (data.success) {
      toast(`💾 ${data.inserted} KPI(s) sauvegardés en base · période ${periode}`);
      if (btn) { btn.textContent = `✅ Sauvegardé (${data.inserted})`; }
      setTimeout(() => { if (btn) { btn.disabled = false; btn.textContent = '💾 Sauvegarder KPIs'; } }, 3000);
    } else {
      toast('❌ ' + (data.error || 'Erreur sauvegarde'), 'err');
      if (btn) { btn.disabled = false; btn.textContent = '💾 Sauvegarder KPIs'; }
    }
  } catch (err) {
    toast('❌ Erreur réseau : ' + err.message, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '💾 Sauvegarder KPIs'; }
  }
}

// ════════════════════════════════════════════════════
// AGGREGATION HELPERS
// ════════════════════════════════════════════════════
function groupBy(catIdx, numIdx, rows, topN=14) {
  const m={};
  rows.forEach(r => {
    const k=String(r[catIdx]||'N/A'), v=parseFloat(String(r[numIdx]).replace(/[,\s]/g,''))||0;
    m[k]=(m[k]||0)+v;
  });
  return Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,topN);
}

function countBy(catIdx, rows, topN=12) {
  const m={};
  rows.forEach(r => { const k=String(r[catIdx]||'N/A'); m[k]=(m[k]||0)+1; });
  return Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,topN);
}

function groupByDate(dateIdx, numIdx, rows) {
  const m={};
  rows.forEach(r => {
    const d=String(r[dateIdx]||'').match(/(\d{4})-(\d{2})/);
    if (!d) return;
    const k=`${d[1]}-${d[2]}`;
    m[k]=(m[k]||0)+(parseFloat(String(r[numIdx]).replace(/[,\s]/g,''))||0);
  });
  return Object.entries(m).sort((a,b)=>a[0].localeCompare(b[0]));
}

// ════════════════════════════════════════════════════
// FORMATTERS
// ════════════════════════════════════════════════════
function fmt(n) {
  if (n===undefined||n===null||isNaN(n)) return '—';
  if (Math.abs(n)>=1e9) return (n/1e9).toFixed(2)+'B';
  if (Math.abs(n)>=1e6) return (n/1e6).toFixed(2)+'M';
  if (Math.abs(n)>=1e3) return (n/1e3).toFixed(1)+'K';
  return Number(n.toFixed(2)).toLocaleString('fr-FR');
}
function fmtFull(n) {
  if (n===undefined||n===null||isNaN(n)) return '—';
  return Number(n.toFixed(2)).toLocaleString('fr-FR');
}

// ════════════════════════════════════════════════════
// CHART UTILS
// ════════════════════════════════════════════════════
const TT = {
  backgroundColor:'rgba(5,9,15,0.95)', borderColor:'rgba(0,229,204,0.2)', borderWidth:1,
  titleColor:'#00e5cc', bodyColor:'#8ba0b8', padding:12, cornerRadius:10, displayColors:false,
};

function kill(id) { if (CHARTS[id]) { CHARTS[id].destroy(); delete CHARTS[id]; } }

function grad(ctx, hex, a1=0.32, a2=0) {
  const g=ctx.createLinearGradient(0,0,0,300);
  const c=hex.replace('#','');
  const r=parseInt(c.slice(0,2),16), gv=parseInt(c.slice(2,4),16), b=parseInt(c.slice(4,6),16);
  g.addColorStop(0,`rgba(${r},${gv},${b},${a1})`);
  g.addColorStop(1,`rgba(${r},${gv},${b},${a2})`);
  return g;
}

function fillSel(id, cols, idx=0) {
  const el=document.getElementById(id);
  if (!el||!cols.length) return;
  const prev=el.value;
  el.innerHTML='';
  cols.forEach((c,i) => {
    const o=document.createElement('option');
    o.value=c; o.textContent=c;
    if (c===prev) o.selected=true;
    else if (i===idx&&!prev) o.selected=true;
    el.appendChild(o);
  });
}

function noData(wrapId, msg) {
  const el=document.getElementById(wrapId);
  if (!el) return;
  el.querySelectorAll('.no-data').forEach(e=>e.remove());
  const cv=el.querySelector('canvas');
  if (cv) cv.style.display='none';
  el.insertAdjacentHTML('beforeend',
    `<div class="no-data"><div class="no-data-icon">📭</div><div class="no-data-text">${msg}</div></div>`);
}

// ════════════════════════════════════════════════════
// RENDER DASHBOARD
// ════════════════════════════════════════════════════
function renderDashboard() {
  if (!DS) return;
  setDefaults();

  document.getElementById('emptyState').style.display  = 'none';
  document.getElementById('dashContent').style.display = 'block';

  document.getElementById('fbName').textContent = DS.filename;
  document.getElementById('fbRows').textContent = `${Number(DS.rowCount).toLocaleString()} lignes`;
  document.getElementById('fbCols').textContent = `${DS.colCount} colonnes`;
  document.getElementById('fbNum').textContent  = `${TYPES.num.length} num.`;
  document.getElementById('fbCat').textContent  = `${TYPES.cat.length} cat.`;

  Object.keys(CHARTS).forEach(kill);

  buildStatSwitcher();
  buildKPIs();
  buildFocusChart();
  buildLine();
  buildBar();
  buildDoughnut();
  buildScatter();
  buildHBar();
  buildRadar();
  buildChips();
  initDataBtn();
  initSaveKpiBtn();   // ← nouveau

  toast(`✅ ${DS.rowCount.toLocaleString()} lignes · ${TYPES.num.length} métriques · ${DS.filename}`);
}

// ════════════════════════════════════════════════════
// ✅ BOUTON SAVE KPIs
// ════════════════════════════════════════════════════
function initSaveKpiBtn() {
  const btn = document.getElementById('btnSaveKpis');
  if (!btn) return;
  btn.onclick = () => saveKpisToDb(true);
}

// ════════════════════════════════════════════════════
// STAT SWITCHER
// ════════════════════════════════════════════════════
function buildStatSwitcher() {
  const wrap = document.getElementById('statSwitcher');
  if (!wrap) return;
  wrap.innerHTML = '';

  Object.entries(STATS).forEach(([key, info]) => {
    const btn = document.createElement('button');
    btn.className    = `stat-btn${key===focusStat?' active':''}`;
    btn.dataset.stat = key;
    btn.innerHTML    = `<span class="stat-icon">${info.icon}</span><span class="stat-desc">${info.label}</span>`;
    btn.title = info.label;
    btn.addEventListener('click', () => {
      focusStat = key;
      wrap.querySelectorAll('.stat-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      buildKPIs();
      buildFocusChart();
    });
    wrap.appendChild(btn);
  });
}

// ════════════════════════════════════════════════════
// KPIs CLIQUABLES
// ════════════════════════════════════════════════════
function buildKPIs() {
  const c = document.getElementById('kpiRow');
  if (!c) return;
  c.innerHTML = '';

  const total = document.createElement('div');
  total.className = 'kpi-card k-t anim';
  total.innerHTML = `
    <div class="kpi-eyebrow">Enregistrements</div>
    <div class="kpi-val">${DS.rowCount.toLocaleString()}</div>
    <div class="kpi-sub">${DS.colCount} colonnes · ${TYPES.num.length} métriques</div>
    <div class="kpi-icon">📊</div>`;
  c.appendChild(total);

  TYPES.num.slice(0, 4).forEach((col, i) => {
    const a      = ANALYSIS[col];
    const { cls, hex } = KPI_COLORS[i % KPI_COLORS.length];
    const val    = calcStat(a.numVals, focusStat);
    const isAct  = col === focusCol;
    const spark  = buildSparkSVG(a.numVals, hex);
    const delta  = buildDeltaHtml(a.numVals);

    const card = document.createElement('div');
    card.className = `kpi-card ${cls} kpi-num${isAct?' kpi-active':''} anim`;
    card.dataset.col = col;
    card.innerHTML   = `
      <div class="kpi-eyebrow">
        ${col}${isAct?'<span class="kpi-live-dot"></span>':''}
      </div>
      <div class="kpi-val">${fmt(val)}</div>
      <div class="kpi-bottom">
        <span class="kpi-stat-lbl">${STATS[focusStat].label}</span>
        ${delta}
      </div>
      ${spark}
      <div class="kpi-icon">${['📈','💹','🔢','📉'][i]}</div>`;

    card.addEventListener('click', () => {
      focusCol = col;
      buildKPIs();
      buildFocusChart();
    });
    c.appendChild(card);
  });

  if (TYPES.cat.length) {
    const col  = TYPES.cat[0];
    const top  = countBy(ANALYSIS[col].index, DS.rows, 1);
    const card = document.createElement('div');
    card.className = 'kpi-card k-e anim';
    card.innerHTML  = `
      <div class="kpi-eyebrow">Top · ${col}</div>
      <div class="kpi-val" style="font-size:1.1rem;letter-spacing:0">${top[0]?.[0]||'—'}</div>
      <div class="kpi-sub">${top[0]?.[1]||0} fois · ${ANALYSIS[col].uniques} valeurs</div>
      <div class="kpi-icon">🏆</div>`;
    c.appendChild(card);
  }
}

function buildSparkSVG(vals, color) {
  const s = vals.filter(v=>!isNaN(v)&&isFinite(v)).slice(-30);
  if (s.length < 3) return '';
  const mn=Math.min(...s), mx=Math.max(...s), rg=mx-mn||1, W=80, H=22;
  const pts = s.map((v,i) => `${(i/(s.length-1))*W},${H-((v-mn)/rg)*H}`).join(' ');
  return `<svg class="kpi-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" opacity=".55" stroke-linejoin="round"/>
  </svg>`;
}

function buildDeltaHtml(vals) {
  const p = calcDeltaPct(vals);
  if (p === 0) return '';
  const up = p >= 0;
  return `<span class="kpi-delta ${up?'up':'dn'}">${up?'▲':'▼'}${Math.abs(p)}%</span>`;
}

// ════════════════════════════════════════════════════
// FOCUS CHART
// ════════════════════════════════════════════════════
function buildFocusChart() {
  kill('cFocus');
  const wrap   = document.getElementById('cFocusWrap');
  const canvas = document.getElementById('cFocus');
  const title  = document.getElementById('focusTitle');
  const sub    = document.getElementById('focusSub');

  wrap?.querySelectorAll('.no-data').forEach(e=>e.remove());
  if (canvas) canvas.style.display='block';

  if (!focusCol||!canvas) { noData('cFocusWrap','Cliquez un KPI numérique pour activer'); return; }
  if (!TYPES.cat.length)  { noData('cFocusWrap','Aucune colonne catégorielle détectée'); return; }

  const numA   = ANALYSIS[focusCol];
  const catCol = document.getElementById('focusCatSel')?.value || TYPES.cat[0];
  const catIdx = ANALYSIS[catCol]?.index ?? 0;
  const info   = STATS[focusStat];

  const map = {};
  DS.rows.forEach(r => {
    const k=String(r[catIdx]||'N/A');
    const v=parseFloat(String(r[numA.index]).replace(/[,\s]/g,''));
    if (!isNaN(v)) { if (!map[k]) map[k]=[]; map[k].push(v); }
  });

  let entries = Object.entries(map)
    .map(([k, vals]) => [k, calcStat(vals, focusStat)])
    .sort((a,b)=>b[1]-a[1])
    .slice(0,15);

  const labels = entries.map(e=>e[0]);
  const values = entries.map(e=>e[1]);

  if (title) title.textContent = `${info.icon}  ${info.label} de "${focusCol}"`;
  if (sub)   sub.textContent   = `par ${catCol} · ${labels.length} catégories · stat active : ${info.label}`;

  fillSel('focusCatSel', TYPES.cat);
  const sel = document.getElementById('focusCatSel');
  if (sel) { sel.value=catCol; sel.onchange=buildFocusChart; }

  const ctx = canvas.getContext('2d');
  CHARTS.cFocus = new Chart(ctx, {
    type:'bar',
    data:{
      labels,
      datasets:[{
        data:values,
        backgroundColor:values.map((_,i)=>`hsla(${175-i*(140/Math.max(labels.length,1))},85%,60%,0.75)`),
        borderColor:values.map((_,i)=>`hsl(${175-i*(140/Math.max(labels.length,1))},85%,65%)`),
        borderWidth:1, borderRadius:8, borderSkipped:false,
      }],
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      animation:{duration:650, easing:'easeOutQuart'},
      plugins:{tooltip:{...TT, callbacks:{
        title:c=>c[0].label,
        label:c=>` ${info.label} : ${fmtFull(c.parsed.y)}`,
      }}},
      scales:{
        x:{grid:{display:false}, ticks:{maxRotation:42, font:{size:10}}},
        y:{grid:{color:'rgba(255,255,255,0.03)'}, ticks:{callback:v=>fmt(v)}},
      },
    },
  });
}

// ════════════════════════════════════════════════════
// CHARTS (Line / Bar / Donut / Scatter / HBar / Radar)
// ════════════════════════════════════════════════════
function buildLine() {
  kill('cLine');
  const canvas=document.getElementById('cLine');
  if (!canvas) return;
  let labels,values,title;
  if (TYPES.date.length&&TYPES.num.length) {
    const dC=TYPES.date[0],nC=TYPES.num[0];
    const g=groupByDate(ANALYSIS[dC].index,ANALYSIS[nC].index,DS.rows);
    labels=g.map(x=>x[0]); values=g.map(x=>x[1]); title=`${nC} / ${dC}`;
  } else if (TYPES.num.length) {
    const nC=TYPES.num[0], sl=DS.rows.slice(0,80);
    labels=sl.map((_,i)=>i+1);
    values=sl.map(r=>parseFloat(String(r[ANALYSIS[nC].index]).replace(/[,\s]/g,''))||0);
    title=`Distribution · ${nC}`;
  } else { noData('cLineWrap','Aucune donnée numérique'); return; }
  const el=document.getElementById('lineTitle');
  if (el) el.textContent=title;
  const ctx=canvas.getContext('2d');
  CHARTS.cLine=new Chart(ctx,{
    type:'line',
    data:{labels,datasets:[{
      data:values, borderColor:'#00e5cc',
      backgroundColor:grad(ctx,'#00e5cc',0.28,0),
      borderWidth:2, fill:true, tension:0.38,
      pointRadius:labels.length>50?0:3,
      pointBackgroundColor:'#00e5cc', pointBorderColor:'#05090f', pointBorderWidth:2, pointHoverRadius:6,
    }]},
    options:{
      responsive:true, maintainAspectRatio:false,
      animation:{duration:900,easing:'easeOutQuart'},
      plugins:{tooltip:{...TT,callbacks:{label:c=>` ${fmt(c.parsed.y)}`}}},
      scales:{
        x:{grid:{color:'rgba(255,255,255,0.03)'},ticks:{maxTicksLimit:10,maxRotation:35}},
        y:{grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmt(v)}},
      },
    },
  });
}

function buildBar() {
  kill('cBar');
  const canvas=document.getElementById('cBar');
  if (!canvas) return;
  if (!TYPES.cat.length||!TYPES.num.length){noData('cBarWrap','Catégorie + numérique requis');return;}
  fillSel('barX',TYPES.cat); fillSel('barY',TYPES.num);
  _drawBar();
  document.getElementById('barX').onchange=_drawBar;
  document.getElementById('barY').onchange=_drawBar;
}
function _drawBar() {
  kill('cBar');
  const canvas=document.getElementById('cBar');
  if (!canvas) return;
  const catC=document.getElementById('barX')?.value||TYPES.cat[0];
  const numC=document.getElementById('barY')?.value||TYPES.num[0];
  const g=groupBy(ANALYSIS[catC].index,ANALYSIS[numC].index,DS.rows,12);
  const labels=g.map(x=>x[0]), values=g.map(x=>x[1]);
  const el=document.getElementById('barTitle');
  if (el) el.textContent=`${numC} par ${catC}`;
  CHARTS.cBar=new Chart(canvas.getContext('2d'),{
    type:'bar',
    data:{labels,datasets:[{data:values,
      backgroundColor:labels.map((_,i)=>PAL[i%PAL.length]+'bb'),
      borderColor:labels.map((_,i)=>PAL[i%PAL.length]),
      borderWidth:1,borderRadius:7,borderSkipped:false,
    }]},
    options:{responsive:true,maintainAspectRatio:false,animation:{duration:700},
      plugins:{tooltip:{...TT,callbacks:{label:c=>` ${fmt(c.parsed.y)}`}}},
      scales:{
        x:{grid:{display:false},ticks:{maxRotation:40,font:{size:10}}},
        y:{grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmt(v)}},
      },
    },
  });
}

function buildDoughnut() {
  kill('cDonut');
  const canvas=document.getElementById('cDonut');
  if (!canvas) return;
  if (!TYPES.cat.length){noData('cDonutWrap','Aucune colonne catégorielle');return;}
  fillSel('donutCol',TYPES.cat); _drawDonut();
  document.getElementById('donutCol').onchange=_drawDonut;
}
function _drawDonut() {
  kill('cDonut');
  const canvas=document.getElementById('cDonut');
  if (!canvas) return;
  const col=document.getElementById('donutCol')?.value||TYPES.cat[0];
  const counts=countBy(ANALYSIS[col].index,DS.rows,9);
  const labels=counts.map(c=>c[0]), values=counts.map(c=>c[1]);
  const el=document.getElementById('donutTitle');
  if (el) el.textContent=`Répartition · ${col}`;
  CHARTS.cDonut=new Chart(canvas.getContext('2d'),{
    type:'doughnut',
    data:{labels,datasets:[{data:values,
      backgroundColor:PAL.slice(0,labels.length).map(c=>c+'bb'),
      borderColor:PAL.slice(0,labels.length), borderWidth:2, hoverOffset:10,
    }]},
    options:{responsive:true,maintainAspectRatio:false,animation:{duration:700},cutout:'68%',
      plugins:{
        legend:{display:true,position:'bottom',labels:{color:'#3d5470',padding:12,font:{size:10},usePointStyle:true,pointStyleWidth:8}},
        tooltip:{...TT,callbacks:{label:c=>{
          const t=c.dataset.data.reduce((a,b)=>a+b,0);
          return ` ${c.label} — ${c.parsed} (${((c.parsed/t)*100).toFixed(1)}%)`;
        }}},
      },
    },
  });
}

function buildScatter() {
  kill('cScatter');
  const canvas=document.getElementById('cScatter');
  if (!canvas) return;
  if (TYPES.num.length<2){noData('cScatterWrap','≥ 2 colonnes numériques');return;}
  fillSel('scX',TYPES.num,0); fillSel('scY',TYPES.num,1);
  _drawScatter();
  document.getElementById('scX').onchange=_drawScatter;
  document.getElementById('scY').onchange=_drawScatter;
}
function _drawScatter() {
  kill('cScatter');
  const canvas=document.getElementById('cScatter');
  if (!canvas) return;
  const xC=document.getElementById('scX')?.value||TYPES.num[0];
  const yC=document.getElementById('scY')?.value||TYPES.num[1];
  const data=DS.rows.slice(0,600).map(r=>({
    x:parseFloat(String(r[ANALYSIS[xC].index]).replace(/[,\s]/g,''))||0,
    y:parseFloat(String(r[ANALYSIS[yC].index]).replace(/[,\s]/g,''))||0,
  })).filter(p=>!isNaN(p.x)&&!isNaN(p.y));
  const el=document.getElementById('scatterTitle');
  if (el) el.textContent=`${xC} ↔ ${yC}`;
  CHARTS.cScatter=new Chart(canvas.getContext('2d'),{
    type:'scatter',
    data:{datasets:[{data,backgroundColor:'rgba(0,229,204,0.35)',borderColor:'#00e5cc',borderWidth:1,
      pointRadius:data.length>300?2:4,pointHoverRadius:7}]},
    options:{responsive:true,maintainAspectRatio:false,animation:{duration:600},
      plugins:{tooltip:{...TT,callbacks:{label:c=>` x: ${fmt(c.parsed.x)}  y: ${fmt(c.parsed.y)}`}}},
      scales:{
        x:{title:{display:true,text:xC,color:'#3d5470',font:{size:10}},grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmt(v)}},
        y:{title:{display:true,text:yC,color:'#3d5470',font:{size:10}},grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmt(v)}},
      },
    },
  });
}

function buildHBar() {
  kill('cHBar');
  const canvas=document.getElementById('cHBar');
  if (!canvas) return;
  if (!TYPES.cat.length||!TYPES.num.length){noData('cHBarWrap','Catégorie + numérique requis');return;}
  const catC=TYPES.cat.length>1?TYPES.cat[1]:TYPES.cat[0], numC=TYPES.num[0];
  const g=groupBy(ANALYSIS[catC].index,ANALYSIS[numC].index,DS.rows,10);
  const labels=g.map(x=>x[0]).reverse(), values=g.map(x=>x[1]).reverse();
  const el=document.getElementById('hbarTitle');
  if (el) el.textContent=`Top · ${catC}`;
  CHARTS.cHBar=new Chart(canvas.getContext('2d'),{
    type:'bar',
    data:{labels,datasets:[{data:values,
      backgroundColor:values.map((_,i)=>`hsla(${175-i*10},70%,${55-i*2}%,0.7)`),
      borderColor:'rgba(0,229,204,0.4)',borderWidth:1,borderRadius:5,borderSkipped:false,
    }]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,animation:{duration:700},
      plugins:{tooltip:{...TT,callbacks:{label:c=>` ${fmt(c.parsed.x)}`}}},
      scales:{
        x:{grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmt(v)}},
        y:{grid:{display:false},ticks:{font:{size:10}}},
      },
    },
  });
}

function buildRadar() {
  kill('cRadar');
  const canvas=document.getElementById('cRadar');
  if (!canvas) return;
  if (TYPES.num.length<3){noData('cRadarWrap','≥ 3 colonnes numériques');return;}
  const cols=TYPES.num.slice(0,7);
  const avgs=cols.map(col=>{
    const v=ANALYSIS[col].numVals.filter(x=>!isNaN(x)&&isFinite(x));
    return v.reduce((s,x)=>s+x,0)/Math.max(v.length,1);
  });
  const mx=Math.max(...avgs,1);
  CHARTS.cRadar=new Chart(canvas.getContext('2d'),{
    type:'radar',
    data:{labels:cols,datasets:[{
      data:avgs.map(v=>(v/mx)*100),
      backgroundColor:'rgba(0,229,204,0.1)',borderColor:'#00e5cc',borderWidth:2,
      pointBackgroundColor:'#00e5cc',pointBorderColor:'#05090f',pointBorderWidth:2,pointRadius:4,pointHoverRadius:7,
    }]},
    options:{responsive:true,maintainAspectRatio:false,animation:{duration:900},
      plugins:{tooltip:{...TT}},
      scales:{r:{
        grid:{color:'rgba(255,255,255,0.04)'},angleLines:{color:'rgba(255,255,255,0.04)'},
        pointLabels:{color:'#3d5470',font:{size:10}},
        ticks:{color:'#3d5470',backdropColor:'transparent',stepSize:25,max:100},
      }},
    },
  });
}

// ════════════════════════════════════════════════════
// CHIPS
// ════════════════════════════════════════════════════
function buildChips() {
  const wrap=document.getElementById('chipsWrap');
  if (!wrap) return;
  wrap.innerHTML='';
  DS.headers.forEach(h=>{
    const a=ANALYSIS[h];
    const cls=a.isNum?'chip-num':a.isDate?'chip-date':a.isCat?'chip-cat':'chip-text';
    const icon=a.isNum?'🔢':a.isDate?'📅':a.isCat?'🏷':'📝';
    const s=document.createElement('span');
    s.className=`chip ${cls}`;
    s.textContent=`${icon} ${h}`;
    s.title=a.isNum?'Numérique':a.isDate?'Date':a.isCat?`Catégorie (${a.uniques} valeurs)`:'Texte';
    wrap.appendChild(s);
  });
}

// ════════════════════════════════════════════════════
// DATA SECTION + MODAL
// ════════════════════════════════════════════════════
function initDataBtn() {
  const btn=document.getElementById('btnOpenData');
  if (!btn||!DS) return;

  document.getElementById('dataCount').textContent =
    `${DS.rowCount.toLocaleString()} lignes · ${DS.colCount} colonnes · cliquer pour explorer`;

  btn.onclick=openModal;

  const overlay = document.getElementById('modalOverlay');
  if (overlay) overlay.addEventListener('click', e=>{ if (e.target===overlay) closeModal(); });
  document.getElementById('btnCloseModal')?.addEventListener('click', closeModal);
  document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeModal(); });
  document.getElementById('modalSearch')?.addEventListener('input', e=>{
    modalSearch=e.target.value.toLowerCase(); renderModalTable();
  });
  document.getElementById('btnExportCsv')?.addEventListener('click', exportCSV);
}

function openModal() {
  modalSearch=''; modalSortCol=-1; modalSortAsc=true;
  const ms=document.getElementById('modalSearch');
  if (ms) ms.value='';
  document.getElementById('modalOverlay')?.classList.add('open');
  document.body.style.overflow='hidden';
  renderModalTable();
}

function closeModal() {
  document.getElementById('modalOverlay')?.classList.remove('open');
  document.body.style.overflow='';
}

function getFiltered() {
  let rows=DS.rows;
  if (modalSearch) rows=rows.filter(r=>r.some(c=>String(c).toLowerCase().includes(modalSearch)));
  if (modalSortCol>=0) {
    const isNum=ANALYSIS[DS.headers[modalSortCol]]?.isNum;
    rows=[...rows].sort((a,b)=>{
      const va=isNum?parseFloat(String(a[modalSortCol]).replace(/[,\s]/g,''))||0:String(a[modalSortCol]||'');
      const vb=isNum?parseFloat(String(b[modalSortCol]).replace(/[,\s]/g,''))||0:String(b[modalSortCol]||'');
      if (va<vb) return modalSortAsc?-1:1;
      if (va>vb) return modalSortAsc?1:-1;
      return 0;
    });
  }
  return rows;
}

function renderModalTable() {
  const wrap=document.getElementById('modalTableWrap');
  const counter=document.getElementById('modalCounter');
  if (!wrap||!counter) return;
  const rows=getFiltered();
  counter.textContent=`${rows.length.toLocaleString()} / ${DS.rowCount.toLocaleString()} lignes`;

  const sortIco=(i)=>{
    if (i!==modalSortCol) return '<span class="s-ico">⇅</span>';
    return `<span class="s-ico active">${modalSortAsc?'↑':'↓'}</span>`;
  };

  let html=`<table class="modal-table"><thead><tr>
    <th class="rn">#</th>
    ${DS.headers.map((h,i)=>{
      const a=ANALYSIS[h];
      const tc=a.isNum?'cn':a.isDate?'cd':a.isCat?'cc':'ct';
      return `<th class="${tc}${i===modalSortCol?' sorted':''}" data-col="${i}">${h}${sortIco(i)}</th>`;
    }).join('')}
  </tr></thead><tbody>`;

  rows.slice(0,500).forEach((row,ri)=>{
    html+=`<tr><td class="rn">${ri+1}</td>
      ${row.map(cell=>{
        const v=cell==null?'':String(cell);
        return `<td title="${v.replace(/"/g,"'")}">${v}</td>`;
      }).join('')}
    </tr>`;
  });

  html+='</tbody></table>';
  wrap.innerHTML=html;

  wrap.querySelectorAll('thead th[data-col]').forEach(th=>{
    th.style.cursor='pointer';
    th.addEventListener('click',()=>{
      const col=parseInt(th.dataset.col);
      if (modalSortCol===col) modalSortAsc=!modalSortAsc;
      else { modalSortCol=col; modalSortAsc=true; }
      renderModalTable();
    });
  });
}

function exportCSV() {
  const rows=getFiltered();
  const blob=new Blob(
    [DS.headers.join(',')+'\n'+rows.map(r=>r.map(v=>`"${String(v??'').replace(/"/g,'""')}"`).join(',')).join('\n')],
    {type:'text/csv;charset=utf-8;'}
  );
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`${DS.filename.replace(/\.[^.]+$/,'')}_export.csv`;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  toast('📥 Export téléchargé !');
}

// ════════════════════════════════════════════════════
// HELPERS
// ════════════════════════════════════════════════════
function showEmpty() {
  const es=document.getElementById('emptyState');
  const dc=document.getElementById('dashContent');
  if (es) es.style.display='flex';
  if (dc) dc.style.display='none';
}

document.getElementById('btnRefresh')?.addEventListener('click',()=>{
  Object.keys(CHARTS).forEach(kill); CHARTS={};
  loadData();
});

// ════════════════════════════════════════════════════
// INIT
// ════════════════════════════════════════════════════
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', loadData);
} else {
  loadData();
}