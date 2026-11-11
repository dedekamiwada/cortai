/* Cortaí Estúdio — editor de vídeo local (sem IA). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.text();
}
const sendJSON = (method, path, body) => api(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const uid = (p) => p + Math.random().toString(36).slice(2, 9);
const fmt = (s, dec = 1) => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  const r = (s % 60).toFixed(dec).padStart(dec ? 3 + dec : 2, "0");
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${r}` : `${m}:${r}`;
};

const FONTS = ["Arial Black", "Arial", "Impact", "Segoe UI", "Verdana", "Georgia", "Comic Sans MS", "Courier New", "Trebuchet MS"];
const TRANSITIONS = [
  ["none", "Nenhuma"], ["fade", "Esmaecer"], ["dissolve", "Dissolver"], ["fadeblack", "Pelo preto"], ["fadewhite", "Pelo branco"],
  ["slideleft", "Deslizar ←"], ["slideright", "Deslizar →"], ["slideup", "Deslizar ↑"], ["slidedown", "Deslizar ↓"],
  ["wipeleft", "Cortina ←"], ["wiperight", "Cortina →"], ["circleopen", "Círculo abre"], ["circleclose", "Círculo fecha"],
  ["zoomin", "Zoom"], ["radial", "Radial"], ["pixelize", "Pixelizar"], ["smoothleft", "Suave ←"], ["hblur", "Desfoque"],
  ["whip", "Chicote"], ["zoomblur", "Zoom com desfoque"], ["spin", "Giro"], ["glitch", "Glitch"], ["lightleak", "Luz vazando"],
];
const TEXT_PRESETS = [
  { label: "Título", css: "font:900 18px 'Arial Black';color:#fff;-webkit-text-stroke:1px #000",
    props: { text: "SEU TÍTULO", font: "Arial Black", size: 110, color: "#FFFFFF", stroke: "#000000", stroke_w: 7, anim: "pop" } },
  { label: "Destaque", css: "font:900 18px 'Arial Black';color:#FFE600;-webkit-text-stroke:1px #000",
    props: { text: "OLHA ISSO", font: "Arial Black", size: 120, color: "#FFE600", stroke: "#000000", stroke_w: 8, anim: "zoom" } },
  { label: "Caixa", css: "font:700 15px Arial;color:#fff;background:#E8336B;padding:2px 6px;border-radius:4px",
    props: { text: "Texto com fundo", font: "Arial", size: 64, color: "#FFFFFF", bg: "#E8336B", anim: "slide" } },
  { label: "Legenda", css: "font:700 14px Arial;color:#fff;-webkit-text-stroke:.5px #000",
    props: { text: "legenda aqui", font: "Arial", size: 58, color: "#FFFFFF", stroke: "#000000", stroke_w: 4, anim: "fade", y: 0.78 } },
  { label: "Meme", css: "font:400 20px Impact;color:#fff;-webkit-text-stroke:1px #000",
    props: { text: "QUANDO O JOGO TRAVA", font: "Impact", size: 100, color: "#FFFFFF", stroke: "#000000", stroke_w: 6, y: 0.12 } },
  { label: "Neon", css: "font:900 18px 'Arial Black';color:#39E75F;-webkit-text-stroke:1px #062",
    props: { text: "GG", font: "Arial Black", size: 150, color: "#39E75F", stroke: "#003311", stroke_w: 6, anim: "pop" } },
  { label: "Sombra", css: "font:900 18px 'Arial Black';color:#fff;text-shadow:2px 2px 0 rgba(0,0,0,.85)",
    props: { text: "SEM CONTORNO", font: "Arial Black", size: 104, color: "#FFFFFF", stroke_w: 0, shadow: "#000000", shadow_d: 8, shadow_a: 0.85, anim: "pop" } },
  { label: "Gamer", css: "font:400 20px Impact;color:#FFD400;-webkit-text-stroke:1px #000;text-shadow:2px 2px 0 #7A00FF",
    props: { text: "CLUTCH!", font: "Impact", size: 140, color: "#FFD400", stroke: "#000000", stroke_w: 7, shadow: "#7A00FF", shadow_d: 10, shadow_a: 1, anim: "zoom" } },
  { label: "Alerta", css: "font:900 15px 'Arial Black';color:#fff;background:#E10600;padding:2px 6px;border-radius:4px",
    props: { text: "ATENÇÃO", font: "Arial Black", size: 90, color: "#FFFFFF", bg: "#E10600", shadow: "#000000", shadow_d: 6, shadow_a: 0.6, anim: "pop", y: 0.2 } },
  { label: "TikTok", css: "font:900 14px 'Arial Black';color:#fff;-webkit-text-stroke:1px #000;text-shadow:1px 1px 0 rgba(0,0,0,.6)",
    props: { text: "legenda estilo tiktok", font: "Arial Black", size: 76, color: "#FFFFFF", stroke: "#000000", stroke_w: 6, shadow: "#000000", shadow_d: 4, shadow_a: 0.6, y: 0.72 } },
  { label: "Retrô", css: "font:900 18px Georgia;color:#FF8A00;-webkit-text-stroke:1px #2B0A3D;text-shadow:3px 3px 0 #2B0A3D",
    props: { text: "ANOS 80", font: "Georgia", size: 120, color: "#FF8A00", stroke: "#2B0A3D", stroke_w: 6, shadow: "#2B0A3D", shadow_d: 12, shadow_a: 1, anim: "slide" } },
  { label: "Sussurro", css: "font:italic 400 16px 'Trebuchet MS';color:#fff;text-shadow:1px 1px 2px #000",
    props: { text: "(baixinho)", font: "Trebuchet MS", size: 64, color: "#FFFFFF", stroke_w: 0, bold: false, italic: true, shadow: "#000000", shadow_d: 3, shadow_a: 0.9, anim: "fade", y: 0.62 } },
];

let P = null;             // projeto aberto
let T = 0;                // tempo atual (s)
let playing = false;
let lastTick = 0;
let sel = null;           // {track, id}
let pps = 60;             // pixels por segundo na timeline
let history = [], future = [];
let saveTimer = null;
let pollTimer = null;
const waves = {};         // forma de onda por mídia
const layers = {};        // elementos de prévia por item

// ================================================================ rotas
function route() {
  stopPlayback();
  clearTimeout(pollTimer);
  const m = location.hash.match(/^#\/p\/(.+)$/);
  $("#list-view").classList.toggle("hidden", !!m);
  $("#editor-view").classList.toggle("hidden", !m);
  document.body.classList.toggle("studio-editing", !!m);
  if (m) openProject(decodeURIComponent(m[1]));
  else loadProjects();
}
window.addEventListener("hashchange", route);
$("#back").onclick = (e) => { e.preventDefault(); flushSave().then(() => (location.hash = "")); };

// ================================================================ lista de projetos
let newRatio = "9:16";
$$("#ratio-pick button").forEach((b) => (b.onclick = () => {
  newRatio = b.dataset.ratio;
  $$("#ratio-pick button").forEach((x) => x.classList.toggle("on", x === b));
}));
$("#new-btn").onclick = async () => {
  const p = await sendJSON("POST", "/api/studio/projects", { name: $("#new-name").value, ratio: newRatio });
  location.hash = `#/p/${p.id}`;
};
async function loadProjects() {
  const list = await api("/api/studio/projects");
  const box = $("#projects");
  if (!list.length) { box.innerHTML = `<p class="muted">Nenhum projeto ainda.</p>`; return; }
  box.replaceChildren(...list.map((p) => {
    const el = document.createElement("button");
    el.className = "project";
    el.innerHTML = `<div class="pv"></div><div class="pm"><b></b><span class="muted small"></span>
      <span class="ghost danger small del" title="Excluir">🗑</span></div>`;
    if (p.thumb) $(".pv", el).style.backgroundImage = `url('/media/${p.thumb}')`;
    $("b", el).textContent = p.name;
    $(".muted", el).textContent = `${p.canvas.ratio} · ${fmt(p.duration, 0)} · ${new Date(p.updated * 1000).toLocaleString("pt-BR")}`;
    el.onclick = (e) => {
      if (e.target.closest(".del")) {
        if (confirm(`Excluir o projeto "${p.name}"? As mídias originais não são apagadas.`))
          api(`/api/studio/projects/${p.id}`, { method: "DELETE" }).then(loadProjects);
        return;
      }
      location.hash = `#/p/${p.id}`;
    };
    return el;
  }));
}

// ================================================================ abrir projeto
async function openProject(id) {
  P = await api(`/api/studio/projects/${id}`);
  T = 0; sel = null; history = []; future = [];
  Object.keys(layers).forEach(dropLayer);
  $("#p-name").value = P.name;
  setSaveState("Salvo");
  renderAssets();
  renderTextPresets();
  renderTransitions();
  layoutStage();
  fitZoom();
  afterChange(false);
  pollAssets();
  if (P.export?.status === "running") openExport();
  renderAutocap();
  if (P.captions_job?.status === "running" || P.captions_job?.status === "done") pollAutocap();
}

function pollAssets() {
  clearTimeout(pollTimer);
  if (!P || !Object.values(P.assets).some((a) => a.status === "processing")) return;
  pollTimer = setTimeout(async () => {
    const fresh = await api(`/api/studio/projects/${P.id}`);
    P.assets = fresh.assets; // só as mídias: a timeline local é a verdade enquanto você edita
    renderAssets();
    renderTimeline();
    syncStage(true);
    pollAssets();
  }, 1500);
}

// ================================================================ modelo de tempo (igual ao studio.py)
const assetOf = (it) => P.assets[it.asset] || {};
const itemLen = (it) => Math.max(0.05, (it.out - it.in) / Math.max(0.1, it.speed || 1));
function mainLayout() {
  const out = [];
  let t = 0;
  P.tracks.main.forEach((it, i) => {
    const tr = it.transition || {};
    let d = i > 0 && tr.type && tr.type !== "none" ? +tr.dur || 0 : 0;
    if (i > 0) d = Math.min(d, itemLen(it) / 2, itemLen(P.tracks.main[i - 1]) / 2);
    const start = t - d;
    const end = start + itemLen(it);
    out.push({ it, start, end, overlap: d, index: i });
    t = end;
  });
  return out;
}
function totalDur() {
  const L = mainLayout();
  return L.length ? L[L.length - 1].end : 0;
}
function spanOf(track, it) {
  if (track === "main") { const e = mainLayout().find((x) => x.it === it); return e ? [e.start, e.end] : [0, 0]; }
  if (track === "text") return [it.start, it.start + it.dur];
  return [it.start, it.start + itemLen(it)];
}
function findItem(s = sel) {
  if (!s) return null;
  return P.tracks[s.track].find((x) => x.id === s.id) || null;
}

// ================================================================ histórico e salvamento
const snapshot = () => JSON.stringify({ tracks: P.tracks, canvas: P.canvas, name: P.name });
function restore(snap) {
  const s = JSON.parse(snap);
  P.tracks = s.tracks; P.canvas = s.canvas; P.name = s.name;
  if (sel && !findItem()) sel = null;
  layoutStage();
  afterChange();
}
function commit(fn, before = snapshot()) {
  fn();
  if (snapshot() === before) return;
  history.push(before);
  if (history.length > 150) history.shift();
  future = [];
  afterChange();
}
function undo() { if (history.length) { future.push(snapshot()); restore(history.pop()); } }
function redo() { if (future.length) { history.push(snapshot()); restore(future.pop()); } }
$("#undo").onclick = undo;
$("#redo").onclick = redo;

function afterChange(save = true) {
  renderTimeline();
  renderInspector();
  syncStage(true);
  $("#undo").disabled = !history.length;
  $("#redo").disabled = !future.length;
  if (save) scheduleSave();
}
function setSaveState(s) { $("#save-state").textContent = s; }
function scheduleSave() {
  setSaveState("Salvando…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 700);
}
async function flushSave() {
  if (!P || !saveTimer) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  try {
    await sendJSON("PUT", `/api/studio/projects/${P.id}`, { name: P.name, canvas: P.canvas, tracks: P.tracks });
    setSaveState("Salvo");
  } catch (ex) { setSaveState(`Erro ao salvar: ${ex.message}`); }
}
window.addEventListener("beforeunload", () => { if (saveTimer) flushSave(); });
$("#p-name").addEventListener("change", (e) => commit(() => (P.name = e.target.value.trim() || P.name)));

// ================================================================ mídia
function renderAssets() {
  const box = $("#assets");
  const list = Object.values(P.assets);
  if (!list.length) { box.innerHTML = `<p class="muted small" style="grid-column:1/-1">Importe vídeos, imagens ou músicas do seu PC.</p>`; return; }
  box.replaceChildren(...list.map((a) => {
    const el = document.createElement("div");
    el.className = "asset";
    el.draggable = a.status === "ready";
    const icon = a.kind === "audio" ? "♪" : a.kind === "image" ? "🖼" : "🎬";
    el.innerHTML = `<div class="th">${a.poster ? "" : icon}</div><div class="nm"></div>
      ${a.duration ? `<span class="du">${fmt(a.duration, 0)}</span>` : ""}
      <button class="add" title="Adicionar à timeline">+</button>
      ${a.status !== "ready" ? `<div class="st ${a.status === "error" ? "err" : ""}"></div>` : ""}`;
    if (a.poster) $(".th", el).style.backgroundImage = `url('/media/${a.poster}')`;
    $(".nm", el).textContent = a.name;
    el.title = a.path;
    if (a.status !== "ready") $(".st", el).textContent = a.status === "error" ? `Erro: ${a.error}` : a.message || "Processando…";
    $(".add", el).onclick = (e) => { e.stopPropagation(); if (a.status === "ready") addAsset(a.id); };
    el.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/x-asset", a.id); e.dataTransfer.effectAllowed = "copy"; });
    el.addEventListener("dblclick", () => a.status === "ready" && addAsset(a.id));
    return el;
  }));
}

async function importPath(path) {
  if (!path.trim()) return;
  try {
    await sendJSON("POST", `/api/studio/projects/${P.id}/assets`, { path: path.trim() });
  } catch (ex) { alert(ex.message); }
  P.assets = (await api(`/api/studio/projects/${P.id}`)).assets;
  renderAssets();
  pollAssets();
}
$("#bin-path-btn").onclick = async () => {
  for (const line of $("#bin-path").value.split(/\n/)) await importPath(line.replace(/^"|"$/g, ""));
  $("#bin-path").value = "";
};
$("#bin-path").addEventListener("keydown", (e) => e.key === "Enter" && $("#bin-path-btn").click());
$("#bin-file").addEventListener("change", (e) => uploadFiles([...e.target.files]));
const binDrop = $("#bin-drop");
["dragover", "dragenter"].forEach((ev) => binDrop.addEventListener(ev, (e) => { e.preventDefault(); binDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => binDrop.addEventListener(ev, () => binDrop.classList.remove("over")));
binDrop.addEventListener("drop", (e) => { e.preventDefault(); uploadFiles([...e.dataTransfer.files]); });

async function uploadFiles(files) {
  for (const file of files) {
    const label = $("#bin-drop span");
    try {
      const { upload_id } = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `/api/uploads?filename=${encodeURIComponent(file.name)}`);
        xhr.upload.onprogress = (e) => e.lengthComputable && (label.textContent = `Enviando ${file.name}… ${Math.floor(e.loaded / e.total * 100)}%`);
        xhr.onload = () => (xhr.status < 300 ? resolve(JSON.parse(xhr.responseText)) : reject(new Error(xhr.responseText)));
        xhr.onerror = () => reject(new Error("falha no envio"));
        xhr.send(file);
      });
      await sendJSON("POST", `/api/studio/projects/${P.id}/assets`, { upload_id, name: file.name });
    } catch (ex) { alert(`${file.name}: ${ex.message}`); }
    label.textContent = "Arraste arquivos aqui ou clique para enviar";
  }
  P.assets = (await api(`/api/studio/projects/${P.id}`)).assets;
  renderAssets();
  pollAssets();
}

function newItem(aid, track, at = T) {
  const a = P.assets[aid];
  const dur = a.kind === "image" ? 3 : a.duration;
  const base = { id: uid("i"), asset: aid, in: 0, out: +dur.toFixed(3) };
  if (track === "main") return { ...base, speed: 1, volume: 1, fit: a.kind === "image" ? "fit" : "fill" };
  if (track === "overlay") return { ...base, start: at, x: 0.5, y: 0.3, scale: 0.45, opacity: 1, volume: 0 };
  return { ...base, start: at, volume: 1, speed: 1, fade_in: 0, fade_out: 0 };
}
function addAsset(aid, track, at, index) {
  const a = P.assets[aid];
  track = track || (a.kind === "audio" ? "audio" : "main");
  if (a.kind === "audio" && track !== "audio") track = "audio";
  if (a.kind === "image" && track === "audio") return;
  commit(() => {
    const it = newItem(aid, track, at ?? T);
    if (track === "main") P.tracks.main.splice(index ?? P.tracks.main.length, 0, it);
    else P.tracks[track].push(it);
    sel = { track, id: it.id };
  });
}

// ---- abas da esquerda
$$(".bin-tabs button").forEach((b) => (b.onclick = () => {
  $$(".bin-tabs button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".bin-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== b.dataset.tab));
}));
function renderTextPresets() {
  $("#text-presets").replaceChildren(...TEXT_PRESETS.map((pr) => {
    const b = document.createElement("button");
    b.innerHTML = `<span style="${pr.css}">${esc(pr.label)}</span>`;
    b.onclick = () => commit(() => {
      const it = { id: uid("t"), start: +T.toFixed(2), dur: 3, x: 0.5, y: 0.45, bold: true, ...pr.props };
      P.tracks.text.push(it);
      sel = { track: "text", id: it.id };
    });
    return b;
  }));
}
function renderTransitions() {
  const it = sel?.track === "main" ? findItem() : null;
  const cur = it?.transition?.type || "none";
  $("#trans-list").replaceChildren(...TRANSITIONS.map(([k, label]) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.classList.toggle("on", !!it && k === cur);
    b.onclick = () => {
      const target = sel?.track === "main" ? findItem() : null;
      if (!target) return alert("Selecione um vídeo da faixa principal primeiro.");
      if (P.tracks.main.indexOf(target) === 0) return alert("O primeiro vídeo não tem transição de entrada.");
      commit(() => (target.transition = k === "none" ? undefined : { type: k, dur: target.transition?.dur || 0.5 }));
    };
    return b;
  }));
}

// ================================================================ prévia (palco)
const stage = $("#stage");
let K = 1; // px de tela por px do canvas
function layoutStage() {
  if (!P) return;
  const wrap = $("#stage-wrap");
  const W = wrap.clientWidth - 28, H = wrap.clientHeight - 28;
  const r = P.canvas.w / P.canvas.h;
  const w = Math.max(80, Math.min(W, H * r));
  stage.style.width = `${w}px`;
  stage.style.height = `${w / r}px`;
  stage.style.background = P.canvas.bg;
  K = w / P.canvas.w;
  syncStage(true);
}
window.addEventListener("resize", () => { layoutStage(); renderTimeline(); });

function dropLayer(id) {
  const l = layers[id];
  if (!l) return;
  l.media?.pause?.();
  l.el.remove();
  l.fxEl?.remove();
  l.grainEl?.remove();
  document.getElementById(`fx-${l.fid}`)?.remove();
  l.audio?.pause();
  delete layers[id];
}
function layerFor(track, it) {
  const key = `${track}:${it.id}`;
  const a = assetOf(it);
  let l = layers[key];
  const src = a.preview ? `/media/${a.preview}` : null;
  if (l && l.src === src && l.track === track) return l;
  if (l) dropLayer(key);
  l = { track, src };
  if (track === "text") {
    l.el = document.createElement("div");
    l.el.className = "txt";
    stage.appendChild(l.el);
    bindStageDrag(l.el, track, it.id);
  } else if (track === "audio") {
    l.el = document.createElement("span");
    l.audio = new Audio(src || "");
    l.audio.preload = "auto";
    l.media = l.audio;
  } else {
    const media = a.kind === "image" ? new Image() : document.createElement("video");
    if (src) media.src = src;
    if (media.tagName === "VIDEO") { media.preload = "auto"; media.playsInline = true; }
    l.media = media;
    if (track === "overlay") {
      l.el = document.createElement("div");
      l.el.className = "ov";
      l.el.appendChild(media);
      bindStageDrag(l.el, track, it.id);
    } else {
      media.className = "layer";
      l.el = media;
    }
    stage.appendChild(l.el);
  }
  layers[key] = l;
  return l;
}

let blurBg = null;
function syncStage(seek = false) {
  if (!P) return;
  const live = new Set();
  const L = mainLayout();
  if (!blurBg) { blurBg = document.createElement("div"); blurBg.className = "blurbg"; stage.prepend(blurBg); }
  blurBg.style.display = "none";

  // ---- faixa principal (+ transições)
  L.forEach((e, i) => {
    const key = `main:${e.it.id}`;
    live.add(key);
    const l = layerFor("main", e.it);
    const media = l.media;
    const active = T >= e.start && T < e.end && assetOf(e.it).status === "ready";
    if (!active) { hide(l); [l.fxEl, l.grainEl].forEach((x) => x && (x.style.display = "none")); return; }
    const it = e.it, a = assetOf(it);
    media.style.display = "block";
    media.style.zIndex = 2 + i;
    const fit = it.fit || "fill";
    media.style.objectFit = fit === "fill" ? "cover" : "contain";
    const lt = T - e.start;
    const panX = kv(it, "pan_x", lt, it.pan_x || 0), panY = kv(it, "pan_y", lt, it.pan_y || 0);
    media.style.objectPosition = `${50 + 50 * panX}% ${50 + 50 * panY}%`;
    media.style.objectViewBox = cropBox(it);
    // o zoom amplia a partir do ponto da posição (como o recorte da exportação); espelhar e girar
    // valem sobre o quadro já encaixado, em volta do centro
    media.style.transformOrigin = "50% 50%";
    const z = fit === "fill" ? Math.max(1, kv(it, "zoom", lt, it.zoom || 1)) : 1;
    const mw = media.clientWidth, mh = media.clientHeight;
    let tx = ((1 - z) * panX * mw) / 2, ty = ((1 - z) * panY * mh) / 2;
    const zoomT = z > 1 ? `translate(${tx}px, ${ty}px) scale(${z})` : "";
    // tremor: amplia 8% e move o recorte (mesma fórmula do crop da exportação)
    const sh = fxv(it, "shake");
    let S = z, shakeT = "";
    if (sh) {
      const [sx, sy] = shakeAt(T - e.start, sh), q = (FX?.shake_zoom || 1.08) - 1;
      const dx = (-q / 2) * mw * sx, dy = (-q / 2) * mh * sy;
      shakeT = `translate(${dx}px, ${dy}px) scale(${1 + q})`;
      S = z * (1 + q);
      tx = tx * (1 + q) + dx;
      ty = ty * (1 + q) + dy;
    }
    // recorta o que o zoom/tremor empurrou para fora do quadro antes de girar (a exportação faz o mesmo)
    const ix = (mw / 2) * (1 - 1 / S), iy = (mh / 2) * (1 - 1 / S);
    const zoomClip = S > 1 ? `inset(${iy - ty / S}px ${ix + tx / S}px ${iy + ty / S}px ${ix - tx / S}px)` : "none";
    let transform = `${orient(it)} ${shakeT} ${zoomT}`.replace(/\s+/g, " ").trim();
    media.style.filter = `${cssFilter(it)} ${svgFilter(l, it)}`.replace("none ", "").trim() || "none";
    let opacity = 1, clip = "none", tfilter = "";
    // transição de entrada
    if (e.overlap > 0 && T < e.start + e.overlap) {
      const p = (T - e.start) / e.overlap;
      ({ opacity, transform, clip, filter: tfilter } = transitionStyle(it.transition?.type, p, transform, mw));
    }
    // transições próprias também mexem no vídeo que sai
    const nx = L[i + 1];
    if (nx && nx.overlap > 0 && T >= nx.start && T < nx.start + nx.overlap) {
      const o = transitionOutStyle(nx.it.transition?.type, (T - nx.start) / nx.overlap, transform, mw);
      if (o) ({ opacity, transform, clip, filter: tfilter } = o);
    }
    if (tfilter) media.style.filter = `${media.style.filter === "none" ? "" : media.style.filter} ${tfilter}`.trim();
    // fades para o preto
    const local = T - e.start, len = e.end - e.start;
    if (it.fade_in && local < it.fade_in) opacity *= local / it.fade_in;
    if (it.fade_out && local > len - it.fade_out) opacity *= Math.max(0, (len - local) / it.fade_out);
    media.style.opacity = opacity;
    media.style.transform = transform;
    media.style.clipPath = clip !== "none" ? clip : zoomClip;
    syncFxOverlay(l, it, 2 + i, opacity, orient(it));
    if (fit === "blur" && a.poster && i === L.findIndex((x) => T >= x.start && T < x.end)) {
      blurBg.style.display = "block";
      blurBg.style.backgroundImage = `url('/media/${a.poster}')`;
    }
    if (media.tagName === "VIDEO") {
      syncMedia(media, it.in + (T - e.start) * (it.speed || 1), it.speed || 1, seek,
        it.mute ? 0 : kv(it, "volume", lt, it.volume ?? 1) * (opacity < 1 ? opacity : 1));
    }
  });

  // ---- sobreposições
  P.tracks.overlay.forEach((it, i) => {
    const key = `overlay:${it.id}`;
    live.add(key);
    const l = layerFor("overlay", it);
    const [s, en] = spanOf("overlay", it);
    if (!(T >= s && T < en) || assetOf(it).status !== "ready") { hide(l); return; }
    const ot = T - s;
    Object.assign(l.el.style, {
      display: "block", left: `${kv(it, "x", ot, it.x ?? 0.5) * 100}%`, top: `${kv(it, "y", ot, it.y ?? 0.5) * 100}%`,
      width: `${kv(it, "scale", ot, it.scale ?? 0.4) * 100}%`, opacity: clamp(kv(it, "opacity", ot, it.opacity ?? 1), 0, 1), zIndex: 50 + i,
      transform: `translate(-50%, -50%) ${overlayShake(it, ot)} ${orient({ ...it, rotate: kv(it, "rotate", ot, it.rotate || 0) })}`,
      mixBlendMode: CSS_BLEND[it.blend] || "normal",
    });
    l.media.style.filter = `${cssFilter(it)} ${svgFilter(l, it)}`.replace("none ", "").trim() || "none";
    l.media.style.objectViewBox = cropBox(it);
    l.el.classList.toggle("sel", sel?.id === it.id);
    if (l.media.tagName === "VIDEO") syncMedia(l.media, it.in + (T - s), 1, seek, it.mute ? 0 : kv(it, "volume", ot, it.volume ?? 0));
  });

  // ---- textos
  P.tracks.text.forEach((it, i) => {
    const key = `text:${it.id}`;
    live.add(key);
    const l = layerFor("text", it);
    if (!(T >= it.start && T < it.start + it.dur)) { l.el.style.display = "none"; return; }
    const an = textAnim(it.anim, T - it.start, it.dur);
    const tt = T - it.start;
    const size = (it.size || 80) * K * kv(it, "scale", tt, it.scale ?? 1);
    const st = l.el.style;
    const html = richText(it, T - it.start);
    if (html === null) { if (l.el._html !== undefined || l.el.textContent !== (it.text || "")) { l.el.textContent = it.text || ""; delete l.el._html; } }
    else if (l.el._html !== html) { l.el.innerHTML = html; l.el._html = html; }
    st.display = "block";
    st.left = `${kv(it, "x", tt, it.x ?? 0.5) * 100}%`;
    st.top = `${kv(it, "y", tt, it.y ?? 0.5) * 100}%`;
    st.zIndex = 100 + i;
    st.fontFamily = `'${it.font || "Arial Black"}'`;
    st.fontSize = `${size}px`;
    st.fontWeight = it.bold === false ? 400 : 900;
    st.fontStyle = it.italic ? "italic" : "normal";
    st.color = it.color || "#fff";
    st.background = it.bg || "transparent";
    st.webkitTextStroke = it.bg ? "0" : `${(it.stroke_w ?? 5) * 2 * K}px ${it.stroke || "#000"}`;
    st.textShadow = textShadowCss(it, K);
    st.paintOrder = "stroke fill";
    st.opacity = an.opacity * clamp(kv(it, "opacity", tt, it.opacity ?? 1), 0, 1);
    st.transform = `translate(-50%, calc(-50% + ${an.dy * P.canvas.h * K}px)) scale(${an.scale})`;
    l.el.classList.toggle("sel", sel?.id === it.id);
  });

  // ---- áudio
  P.tracks.audio.forEach((it) => {
    const key = `audio:${it.id}`;
    live.add(key);
    const l = layerFor("audio", it);
    const [s, en] = spanOf("audio", it);
    if (!(T >= s && T < en) || assetOf(it).status !== "ready") { l.audio.pause(); return; }
    const local = T - s, len = en - s;
    let vol = it.mute ? 0 : kv(it, "volume", T - s, it.volume ?? 1);
    if (it.fade_in && local < it.fade_in) vol *= local / it.fade_in;
    if (it.fade_out && local > len - it.fade_out) vol *= Math.max(0, (len - local) / it.fade_out);
    syncMedia(l.audio, it.in + local * (it.speed || 1), it.speed || 1, seek, vol);
  });

  Object.keys(layers).forEach((k) => !live.has(k) && dropLayer(k));
  $("#tc").textContent = fmt(T);
  $("#tc-total").textContent = fmt(totalDur());
  refreshKfInspector();
  $("#tl-playhead").style.left = `${T * pps}px`;
}

function hide(l) {
  if (l.track === "overlay") l.el.style.display = "none";
  else l.el.style.display = "none";
  if (l.media && !l.media.paused) l.media.pause?.();
}
// cortar (object-view-box recorta a mídia antes do object-fit, como o crop antes do encaixe)
// ================================================================ keyframes (mesmas contas do keys.py)
const KF_PROPS = { main: ["zoom", "pan_x", "pan_y", "volume"], overlay: ["x", "y", "scale", "opacity", "rotate", "volume"],
  text: ["x", "y", "scale", "opacity"], audio: ["volume"] };
const KF_DEF = { main: { zoom: 1, pan_x: 0, pan_y: 0, volume: 1 }, overlay: { x: 0.5, y: 0.5, scale: 0.4, opacity: 1, rotate: 0, volume: 0 },
  text: { x: 0.5, y: 0.5, scale: 1, opacity: 1 }, audio: { volume: 1 } };
const kfList = (it, p) => ((it.kf || {})[p] || []).filter((k) => isFinite(k[0]) && isFinite(k[1])).slice().sort((a, b) => a[0] - b[0]);
const hasKf = (it, p) => kfList(it, p).length > 0;
function kv(it, p, t, def) {
  const ks = kfList(it, p);
  if (!ks.length) return def;
  if (t <= ks[0][0]) return ks[0][1];
  for (let i = 0; i + 1 < ks.length; i++) {
    const [t0, v0] = ks[i], [t1, v1] = ks[i + 1];
    if (t < t1) {
      let u = t1 > t0 ? (t - t0) / (t1 - t0) : 1;
      if ((it.kf_ease || "linear") === "smooth") u = u * u * (3 - 2 * u);
      return v0 + (v1 - v0) * u;
    }
  }
  return ks[ks.length - 1][1];
}
const localT = (track, it) => T - spanOf(track, it)[0];
const kval = (track, it, p) => kv(it, p, localT(track, it), it[p] ?? KF_DEF[track][p]);
const kfTimes = (it) => [...new Set(Object.keys(it.kf || {}).flatMap((p) => kfList(it, p).map((k) => k[0])))].sort((a, b) => a - b);
const kfTol = () => 0.5 / (P?.canvas.fps || 30);
// mexer num valor que tem keyframes grava uma chave no ponto do cursor
function setAnim(track, it, k, v) {
  const ks = (it.kf || {})[k];
  if (!ks || !ks.length) return false;
  const t = +Math.max(0, localT(track, it)).toFixed(3);
  const hit = ks.find((x) => Math.abs(x[0] - t) < kfTol());
  if (hit) hit[1] = v;
  else { ks.push([t, v]); ks.sort((a, b) => a[0] - b[0]); }
  return true;
}
function toggleKey(track, it, k) {
  commit(() => {
    it.kf = it.kf || {};
    const ks = it.kf[k] || (it.kf[k] = []);
    const [s, e] = spanOf(track, it);
    const t = +clamp(T - s, 0, e - s).toFixed(3);
    const i = ks.findIndex((x) => Math.abs(x[0] - t) < kfTol());
    if (i >= 0) ks.splice(i, 1);
    else { ks.push([t, +(+kv(it, k, t, it[k] ?? KF_DEF[track][k])).toFixed(4)]); ks.sort((a, b) => a[0] - b[0]); }
    if (!ks.length) delete it.kf[k];
    if (!Object.keys(it.kf).length) delete it.kf;
  });
}
// cortar o começo de um item: as chaves andam junto e o valor do novo começo é mantido
function shiftKf(it, dt) {
  if (!it.kf || !dt) return;
  for (const p of Object.keys(it.kf)) {
    const ks = kfList(it, p);
    const at0 = kv(it, p, -dt, 0);
    let moved = ks.map(([t, v]) => [+(t + dt).toFixed(3), v]);
    if (moved.some((k) => k[0] < 0)) moved = [[0, +at0.toFixed(4)], ...moved.filter((k) => k[0] > 0)];
    it.kf[p] = moved;
  }
}
function refreshKfInspector() {
  const it = findItem();
  if (!it || !KF_PROPS[sel.track]) return;
  for (const k of KF_PROPS[sel.track]) {
    const btn = $(`#inspector [data-kf="${k}"]`);
    if (!btn) continue;
    const ks = kfList(it, k), t = localT(sel.track, it);
    btn.classList.toggle("has", ks.length > 0);
    btn.classList.toggle("on", ks.some((x) => Math.abs(x[0] - t) < kfTol()));
    const input = $(`#inspector [data-k="${k}"]`);
    if (ks.length && input && document.activeElement !== input) {
      const v = kval(sel.track, it, k);
      input.value = v;
      const out = input.parentElement.querySelector("output");
      if (out) out.textContent = k === "volume" ? `${Math.round(v * 100)}%` : (+v).toFixed(input.step < 0.1 ? 2 : 1);
    }
  }
}

// ================================================================ filtros e efeitos (mesmas contas do fx.py)
let FX = null;
api("/api/studio/fx").then((c) => { FX = c; if (P) { renderInspector(); syncStage(true); } }).catch(() => {});
const fxv = (it, k) => clamp(+((it.fx || {})[k]) || 0, 0, 1);
function shakeAt(t, amt) {
  return [amt * (0.6 * Math.sin(t * 23 + 1) + 0.4 * Math.sin(t * 41)), amt * (0.6 * Math.sin(t * 31) + 0.4 * Math.sin(t * 47 + 2))];
}
function overlayShake(it, t) {
  const amt = fxv(it, "shake");
  if (!amt) return "";
  const [sx, sy] = shakeAt(t, amt);
  return `translate(${P.canvas.w * 0.03 * sx * K}px, ${P.canvas.w * 0.03 * sy * K}px)`;
}
// linha de alfa do feColorMatrix para o recorte de fundo (mesma ideia do chromakey/colorkey):
// cores perto da cor-chave ficam transparentes, com uma faixa de suavidade
function keyAlphaRow(it) {
  const k = it.key || "";
  if (!k) return null;
  const isLum = k === "black" || k === "white";
  const sim = clamp(+(it.key_sim ?? (isLum ? 0.12 : 0.3)), 0.01, 0.9);
  const soft = Math.max(0.03, clamp(+(it.key_blend ?? 0.1), 0, 0.5) * 2);
  if (isLum) {  // colorkey: transparente até a tolerância, rampa do tamanho da suavidade
    const L = [0.2126, 0.7152, 0.0722], ls = Math.max(0.03, clamp(+(it.key_blend ?? 0.1), 0, 0.5));
    if (k === "black") return [...L.map((v) => v / ls), 0, -sim / ls];
    return [...L.map((v) => -v / ls), 0, (1 - sim) / ls];
  }
  const hex = (k === "custom" ? it.key_color : { green: "#00FF00", blue: "#0000FF" }[k]) || "#00FF00";
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const mean = (c[0] + c[1] + c[2]) / 3;
  const v = c.map((x) => x - mean);
  const kv = c[0] * v[0] + c[1] * v[1] + c[2] * v[2] || 1;
  const th = 1 - sim;  // d = 1 na cor-chave, ~0 no cinza; transparente quando d > th
  return [...v.map((x) => -x / kv / soft), 0, th / soft];
}
const CSS_BLEND = { screen: "screen", addition: "plus-lighter", lighten: "lighten", multiply: "multiply", darken: "darken", overlay: "overlay", difference: "difference" };
function lookMatrix(it) {
  const look = FX?.looks.find((l) => l.id === it.look);
  if (!look) return null;
  const amt = clamp(it.look_amt ?? 1, 0, 1);
  if (amt < 0.01) return null;
  const id = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]];
  return look.matrix.map((row, i) => row.map((v, j) => id[i][j] + amt * (v - id[i][j])));
}
let svgDefs = null;
// um <filter> SVG por camada: filtro de cor, desfoque, brilho, pixelado e separação RGB
function svgFilter(l, it) {
  const m = lookMatrix(it);
  const isMain = l.track === "main";
  const blur = isMain ? fxv(it, "blur") : 0, glow = isMain ? fxv(it, "glow") : 0;
  const pixel = isMain ? fxv(it, "pixel") : 0, rgb = isMain ? fxv(it, "rgb") : 0;
  const keyRow = l.track === "overlay" ? keyAlphaRow(it) : null;
  if (!m && !blur && !glow && !pixel && !rgb && !keyRow) return "";
  const key = JSON.stringify([m, blur, glow, pixel, rgb, K, keyRow]);
  l.fid = l.fid || uid("f");
  if (l.fxKey !== key) {
    l.fxKey = key;
    if (!svgDefs) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
      svg.style.position = "absolute";
      svgDefs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
      svg.appendChild(svgDefs);
      document.body.appendChild(svg);
    }
    const p = [];
    let cur = "SourceGraphic", n = 0;
    const next = () => `r${++n}`;
    if (keyRow) {  // recorte de fundo: transparência calculada da cor (aproximação do chromakey)
      const o = next();
      p.push(`<feColorMatrix in="${cur}" type="matrix" values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 ${keyRow.map((v) => +v.toFixed(4)).join(" ")}" result="${o}"/>`);
      cur = o;
    }
    if (m) {
      const vals = [...m[0].slice(0, 3), 0, m[0][3], ...m[1].slice(0, 3), 0, m[1][3], ...m[2].slice(0, 3), 0, m[2][3], 0, 0, 0, 1, 0];
      const o = next(); p.push(`<feColorMatrix in="${cur}" type="matrix" values="${vals.map((v) => +v.toFixed(5)).join(" ")}" result="${o}"/>`); cur = o;
    }
    if (blur) { const o = next(); p.push(`<feGaussianBlur in="${cur}" stdDeviation="${blur * 25 * K}" edgeMode="duplicate" result="${o}"/>`); cur = o; }
    if (glow) {
      const b = next(), sc = next(), o = next();
      p.push(`<feGaussianBlur in="${cur}" stdDeviation="${20 * K}" result="${b}"/>`,
        `<feBlend in="${cur}" in2="${b}" mode="screen" result="${sc}"/>`,
        `<feComposite in="${cur}" in2="${sc}" operator="arithmetic" k1="0" k2="${1 - glow}" k3="${glow}" k4="0" result="${o}"/>`);
      cur = o;
    }
    if (pixel) {
      const b = Math.max(2, (2 + pixel * 38) * K), h = b / 2;
      const fl = next(), tile = next(), grid = next(), dots = next(), o = next();
      p.push(`<feFlood x="${h}" y="${h}" width="1" height="1" result="${fl}"/>`,
        `<feComposite in="${fl}" width="${b}" height="${b}" result="${tile}"/>`,
        `<feTile in="${tile}" result="${grid}"/>`,
        `<feComposite in="${cur}" in2="${grid}" operator="in" result="${dots}"/>`,
        `<feMorphology in="${dots}" operator="dilate" radius="${h}" result="${o}"/>`);
      cur = o;
    }
    if (rgb) {
      const d = Math.max(1, Math.round(rgb * 24)) * K;
      const r = next(), g = next(), bl = next(), ro = next(), bo = next(), rg = next(), o = next();
      p.push(`<feColorMatrix in="${cur}" type="matrix" values="1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0" result="${r}"/>`,
        `<feColorMatrix in="${cur}" type="matrix" values="0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0" result="${g}"/>`,
        `<feColorMatrix in="${cur}" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0" result="${bl}"/>`,
        `<feOffset in="${r}" dx="${-d}" result="${ro}"/>`, `<feOffset in="${bl}" dx="${d}" result="${bo}"/>`,
        `<feBlend in="${ro}" in2="${g}" mode="screen" result="${rg}"/>`, `<feBlend in="${rg}" in2="${bo}" mode="screen" result="${o}"/>`);
      cur = o;
    }
    let el = document.getElementById(`fx-${l.fid}`);
    if (!el) { el = document.createElementNS("http://www.w3.org/2000/svg", "filter"); el.id = `fx-${l.fid}`; svgDefs.appendChild(el); }
    el.setAttribute("color-interpolation-filters", "sRGB");
    el.setAttribute("x", "0"); el.setAttribute("y", "0"); el.setAttribute("width", "100%"); el.setAttribute("height", "100%");
    el.innerHTML = p.join("");
  }
  return `url(#fx-${l.fid})`;
}
let grainUrl = null;
function grainTexture() {
  if (grainUrl) return grainUrl;
  const c = document.createElement("canvas");
  c.width = c.height = 160;
  const ctx = c.getContext("2d"), img = ctx.createImageData(160, 160);
  for (let i = 0; i < img.data.length; i += 4) { const v = Math.random() * 255; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255; }
  ctx.putImageData(img, 0, 0);
  return (grainUrl = c.toDataURL());
}
// vinheta e grão: camadas por cima do vídeo (somem junto com ele)
function syncFxOverlay(l, it, z, opacity, orientT) {
  const vig = fxv(it, "vignette"), grain = fxv(it, "grain");
  const layer = (key, cls, on) => {
    if (!on) { if (l[key]) l[key].style.display = "none"; return null; }
    if (!l[key]) {
      l[key] = document.createElement("div");
      l[key].className = `fxlayer ${cls}`;
      l.el.after(l[key]);
    }
    Object.assign(l[key].style, { display: "block", zIndex: z, transform: orientT || "none" });
    return l[key];
  };
  const v = layer("fxEl", "vig", vig);
  if (v) {
    v.style.opacity = opacity;
    v.style.backgroundImage = `radial-gradient(ellipse farthest-corner at 50% 50%, rgba(0,0,0,0) ${(FX?.vignette_start ?? 0.35) * 100}%, rgba(0,0,0,${vig}) 100%)`;
  }
  const g = layer("grainEl", "grain", grain);
  if (g) {
    g.style.opacity = opacity * grain * 0.7;
    g.style.backgroundImage = `url(${grainTexture()})`;
    g.style.backgroundPosition = `${Math.floor(Math.random() * 160)}px ${Math.floor(Math.random() * 160)}px`;
  }
}
function textShadowCss(it, k) {
  if (!it.shadow) return "none";
  const d = (it.shadow_d ?? 6) * k;
  const h = it.shadow.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) || 0);
  return `${d}px ${d}px 0 rgba(${r}, ${g}, ${b}, ${it.shadow_a ?? 0.8})`;
}
function cropBox(it) {
  const v = (k) => clamp(+it[k] || 0, 0, 0.45) * 100;
  const [l, r, t, b] = [v("crop_l"), v("crop_r"), v("crop_t"), v("crop_b")];
  return l + r + t + b > 0.1 ? `inset(${t}% ${r}% ${b}% ${l}%)` : "none";
}
function orient(it) {
  const parts = [];
  if (+it.rotate) parts.push(`rotate(${+it.rotate}deg)`);
  if (it.flip_h || it.flip_v) parts.push(`scale(${it.flip_h ? -1 : 1}, ${it.flip_v ? -1 : 1})`);
  return parts.join(" ");
}
function cssFilter(it) {
  const b = 1 + (it.brightness || 0) * 1.4, c = it.contrast ?? 1, s = it.saturation ?? 1;
  return b === 1 && c === 1 && s === 1 ? "none" : `brightness(${b}) contrast(${c}) saturate(${s})`;
}
function syncMedia(m, local, rate, seek, volume) {
  if (!m || m.readyState === 0) return;
  m.volume = clamp(volume, 0, 1);
  m.muted = volume <= 0;
  if (playing) {
    try { m.playbackRate = clamp(rate, 0.25, 4); } catch {}
    if (Math.abs(m.currentTime - local) > 0.3) m.currentTime = local;
    if (m.paused) m.play().catch(() => {});
  } else {
    if (!m.paused) m.pause();
    if (seek || Math.abs(m.currentTime - local) > 0.05) m.currentTime = local;
  }
}
// p vai de 0 a 1; mesmas curvas das expressões do fx.transition_expr
function customTransition(type, p, transform, mw, entering) {
  const s = Math.sin(Math.PI * p), q1 = 2 * p, q2 = 2 - 2 * p;
  const out = { opacity: 1, transform, clip: "none", filter: "" };
  const firstHalf = p < 0.5;
  if (type === "lightleak") {
    out.filter = `brightness(${1 + 0.35 * s}) sepia(${0.6 * s}) saturate(${1 + 0.5 * s})`;
    if (entering) out.opacity = p;
    return out;
  }
  if (entering === firstHalf) { out.opacity = 0; return out; }  // estas trocam de vídeo no meio
  const q = entering ? q2 : q1;
  if (type === "whip") {
    out.transform = `translateX(${(entering ? 1 : -1) * 150 * q * q}%) ${transform}`;
    out.filter = `blur(${0.05 * mw * s * 0.6}px)`;
  } else if (type === "zoomblur") {
    out.transform = `scale(${1 + 1.5 * q * q}) ${transform}`;
    out.filter = `blur(${0.03 * mw * s}px)`;
  } else if (type === "spin") {
    out.transform = `rotate(${(entering ? -180 : 180) * q * q}deg) ${transform}`;
  } else if (type === "glitch") {
    const h = Math.abs(Math.sin(Math.floor(p * 16) * 78.233) * 43758.5453) % 1;
    out.transform = `translateX(${(h - 0.5) * 25 * s}%) ${transform}`;
    out.filter = `hue-rotate(${Math.round((h - 0.5) * 120 * s)}deg) saturate(${1 + s})`;
  }
  return out;
}
const CUSTOM_TR = new Set(["whip", "zoomblur", "spin", "glitch", "lightleak"]);
function transitionOutStyle(type, p, transform, mw) {
  return CUSTOM_TR.has(type) ? customTransition(type, p, transform, mw, false) : null;
}
function transitionStyle(type, p, transform, mw = 0) {
  if (CUSTOM_TR.has(type)) return customTransition(type, p, transform, mw, true);
  const out = { opacity: 1, transform, clip: "none", filter: "" };
  const shift = (x, y) => (out.transform = `translate(${x}%, ${y}%) ${transform}`);
  switch (type) {
    case "slideleft": case "smoothleft": shift((1 - p) * 100, 0); break;
    case "slideright": shift(-(1 - p) * 100, 0); break;
    case "slideup": shift(0, (1 - p) * 100); break;
    case "slidedown": shift(0, -(1 - p) * 100); break;
    case "wipeleft": out.clip = `inset(0 0 0 ${(1 - p) * 100}%)`; break;
    case "wiperight": out.clip = `inset(0 ${(1 - p) * 100}% 0 0)`; break;
    case "circleopen": out.clip = `circle(${p * 75}% at 50% 50%)`; break;
    case "circleclose": out.clip = `circle(${p * 75}% at 50% 50%)`; break;
    case "zoomin": out.opacity = p; out.transform = `scale(${1.4 - 0.4 * p}) ${transform}`; break;
    case "fadeblack": case "fadewhite": out.opacity = clamp(p * 2 - 1, 0, 1); break;
    default: out.opacity = p;
  }
  return out;
}
// texto vindo de um clipe do Cortaí: palavra falada em destaque (karaokê) e palavras-chave
function wordSpans(it, count) {
  const dur = Math.max(0.1, it.dur || 3);
  const sp = it.words || [];
  if (sp.length === count && count) return sp.map(([a, b]) => [Math.max(0, a), Math.min(dur, b)]);
  const step = dur / Math.max(1, count);
  return Array.from({ length: count }, (_, i) => [i * step, (i + 1) * step]);
}
function richText(it, t) {
  const kw = new Set((it.kw_idx || []).map(Number));
  const lines = String(it.text || "").split("\n").map((l) => l.split(/\s+/).filter(Boolean));
  const count = lines.reduce((a, l) => a + l.length, 0);
  const mode = it.hl_mode || "color";
  const karaoke = (it.words || []).length > 0 && (mode === "color" || mode === "box") && count > 0;
  if (!karaoke && !kw.size) return null;
  let active = -1;
  if (karaoke) {
    active = 0;
    wordSpans(it, count).forEach(([a], k) => { if (k > 0 && t >= a) active = k; });
  }
  const pop = `display:inline-block;margin:0 .06em;transform:scale(${(it.hl_pop || 110) / 100})`;
  let k = 0;
  return lines.map((line) => line.map((w) => {
    const i = k++, txt = esc(w);
    if (i === active && mode === "box")
      return `<span style="${pop};background:${esc(it.hl || "#FFE600")};color:#111;-webkit-text-stroke:0;padding:0 .12em;border-radius:.12em">${txt}</span>`;
    if (i === active) return `<span style="${pop};color:${esc(it.hl || "#FFE600")}">${txt}</span>`;
    if (kw.has(i)) return `<span style="color:${esc(it.kw_color || "#39E75F")}">${txt}</span>`;
    return txt;
  }).join(" ")).join("<br>");
}
// dividir um texto com destaque: cada metade fica com as suas palavras e tempos
function splitRich(it, cut) {
  if (!(it.words || []).length && !(it.kw_idx || []).length) return null;
  const tokens = String(it.text || "").split(/\s+/).filter(Boolean);
  const sp = wordSpans(it, tokens.length);
  const k = sp.filter(([a]) => a < cut).length;
  if (k <= 0 || k >= tokens.length) return null;
  const kw = (it.kw_idx || []).map(Number);
  const hasWords = (it.words || []).length > 0;
  return {
    a: { text: tokens.slice(0, k).join(" "), kw_idx: kw.filter((i) => i < k),
         words: hasWords ? sp.slice(0, k).map(([a, b]) => [a, Math.min(b, cut)]) : undefined },
    b: { text: tokens.slice(k).join(" "), kw_idx: kw.filter((i) => i >= k).map((i) => i - k),
         words: hasWords ? sp.slice(k).map(([a, b]) => [+(a - cut).toFixed(3), +(b - cut).toFixed(3)]) : undefined },
  };
}
function textAnim(anim, t, dur) {
  const r = { opacity: 1, scale: 1, dy: 0 };
  if (anim === "fade") r.opacity = Math.min(1, t / 0.25, (dur - t) / 0.25);
  if (anim === "pop") { r.scale = t < 0.16 ? 0.3 + (t / 0.16) * 0.82 : t < 0.26 ? 1.12 - ((t - 0.16) / 0.1) * 0.12 : 1; r.opacity = Math.min(1, (dur - t) / 0.18); }
  if (anim === "slide") { const p = Math.min(1, t / 0.26); r.dy = 0.06 * (1 - p); r.opacity = Math.min(1, t / 0.16, (dur - t) / 0.16); }
  if (anim === "zoom") { r.scale = t < 0.3 ? 1.6 - (t / 0.3) * 0.6 : 1; r.opacity = Math.min(1, t / 0.12, (dur - t) / 0.18); }
  r.opacity = clamp(r.opacity, 0, 1);
  return r;
}

// arrastar texto/sobreposição direto na prévia
function bindStageDrag(el, track, id) {
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    sel = { track, id };
    const it = findItem();
    const before = snapshot();
    const rect = stage.getBoundingClientRect();
    const ox = e.clientX - (it.x ?? 0.5) * rect.width, oy = e.clientY - (it.y ?? 0.5) * rect.height;
    el.setPointerCapture(e.pointerId);
    renderInspector(); renderTimeline();
    const move = (ev) => {
      it.x = +clamp((ev.clientX - ox) / rect.width, 0, 1).toFixed(3);
      it.y = +clamp((ev.clientY - oy) / rect.height, 0, 1).toFixed(3);
      syncStage();
    };
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      commit(() => {}, before);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  });
}

// ---- reprodução
function play() {
  if (!P || !P.tracks.main.length) return;
  if (T >= totalDur() - 0.05) T = 0;
  playing = true;
  lastTick = performance.now();
  $("#play").textContent = "❚❚";
  requestAnimationFrame(tick);
}
function stopPlayback() {
  playing = false;
  $("#play").textContent = "▶";
  Object.values(layers).forEach((l) => l.media?.pause?.());
}
function tick(now) {
  if (!playing) return;
  T += (now - lastTick) / 1000;
  lastTick = now;
  if (T >= totalDur()) { T = totalDur(); stopPlayback(); syncStage(true); return; }
  syncStage();
  keepPlayheadVisible();
  requestAnimationFrame(tick);
}
$("#play").onclick = () => (playing ? (stopPlayback(), syncStage(true)) : play());
function setTime(t) {
  T = clamp(t, 0, Math.max(totalDur(), 0));
  syncStage(true);
}

// ================================================================ timeline
const scroller = $("#tl-scroll");
const content = $("#tl-content");
function fitZoom() {
  const dur = Math.max(totalDur(), 10);
  pps = clamp((scroller.clientWidth - 40) / dur, 8, 400);
  $("#tl-zoom").value = pps;
}
$("#tl-zoom").addEventListener("input", (e) => {
  const center = (scroller.scrollLeft + scroller.clientWidth / 2) / pps;
  pps = +e.target.value;
  renderTimeline();
  scroller.scrollLeft = center * pps - scroller.clientWidth / 2;
});
scroller.addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  const z = $("#tl-zoom");
  z.value = clamp(pps * (e.deltaY < 0 ? 1.15 : 0.87), 8, 400);
  z.dispatchEvent(new Event("input"));
}, { passive: false });
function keepPlayheadVisible() {
  const x = T * pps;
  if (x > scroller.scrollLeft + scroller.clientWidth - 40) scroller.scrollLeft = x - 80;
}

function drawRuler(width) {
  const c = $("#tl-ruler");
  const dpr = devicePixelRatio || 1;
  c.width = width * dpr; c.height = 26 * dpr; c.style.width = `${width}px`;
  const ctx = c.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.fillStyle = "#8a8aa0"; ctx.strokeStyle = "#3a3a4d"; ctx.font = "10px system-ui";
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = steps.find((s) => s * pps >= 70) || 600;
  for (let t = 0; t * pps < width; t += step) {
    const x = Math.round(t * pps) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 14); ctx.lineTo(x, 26); ctx.stroke();
    ctx.fillText(fmt(t, step < 1 ? 1 : 0), x + 3, 11);
    for (let k = 1; k < 5; k++) {
      const xs = Math.round((t + (step * k) / 5) * pps) + 0.5;
      ctx.beginPath(); ctx.moveTo(xs, 21); ctx.lineTo(xs, 26); ctx.stroke();
    }
  }
}

function renderTimeline() {
  if (!P) return;
  const total = totalDur();
  const ends = [total, ...P.tracks.text.map((t) => t.start + t.dur), ...["overlay", "audio"].flatMap((k) => P.tracks[k].map((it) => spanOf(k, it)[1]))];
  const width = Math.max(scroller.clientWidth, (Math.max(...ends) + 15) * pps);
  content.style.width = `${width}px`;
  drawRuler(width);
  const tracks = Object.fromEntries($$(".tl-track", content).map((el) => [el.dataset.track, el]));
  Object.values(tracks).forEach((el) => el.replaceChildren());

  mainLayout().forEach((e) => tracks.main.appendChild(itemEl("main", e.it, e.start, e.end, e.overlap)));
  for (const k of ["overlay", "text", "audio"]) {
    P.tracks[k].forEach((it) => { const [s, en] = spanOf(k, it); tracks[k].appendChild(itemEl(k, it, s, en)); });
  }
  $("#tl-playhead").style.left = `${T * pps}px`;
  renderTransitions();
}

function itemEl(track, it, start, end, overlap = 0) {
  const a = assetOf(it);
  const el = document.createElement("div");
  const kind = track === "text" ? "text" : track === "audio" ? "audio" : a.kind || "video";
  el.className = `tl-item ${kind}`;
  el.classList.toggle("sel", sel?.id === it.id);
  el.style.left = `${start * pps}px`;
  el.style.width = `${Math.max(4, (end - start) * pps)}px`;
  el.dataset.id = it.id;
  const label = track === "text" ? (it.text || "").replace(/\n/g, " ") : a.name || "?";
  const fx = [];
  if ((it.speed || 1) !== 1) fx.push(`${it.speed}x`);
  if (it.mute) fx.push("🔇");
  if (it.fade_in || it.fade_out) fx.push("◐");
  el.innerHTML = `<span class="lbl"></span><span class="fx">${fx.join(" ")}</span><i class="h l"></i><i class="h r"></i>
    ${overlap > 0 ? `<span class="tr" title="Transição">⇄</span>` : ""}`;
  $(".lbl", el).textContent = label;
  kfTimes(it).forEach((t) => {
    const d = document.createElement("i");
    d.className = "kfd";
    d.style.left = `${t * pps}px`;
    el.appendChild(d);
  });
  if (kind === "video" && a.strip && a.duration) {
    el.style.backgroundImage = `url('/media/${a.strip}')`;
    const full = (a.duration / (it.speed || 1)) * pps;
    el.style.backgroundSize = `${full}px 100%`;
    el.style.backgroundPosition = `${-(it.in / (it.speed || 1)) * pps}px 0`;
    el.style.backgroundRepeat = "no-repeat";
  } else if (kind === "image" && a.poster) {
    el.style.backgroundImage = `url('/media/${a.poster}')`;
  }
  if (kind === "audio" || (track === "main" && a.wave && false)) drawWave(el, it, a);
  bindItem(el, track, it);
  return el;
}

async function drawWave(el, it, a) {
  if (!a.wave) return;
  if (!waves[a.id]) waves[a.id] = await api(`/media/${a.wave}`).catch(() => []);
  const data = waves[a.id];
  const c = document.createElement("canvas");
  el.prepend(c);
  const w = el.clientWidth || parseFloat(el.style.width), h = 40;
  c.width = Math.max(1, w); c.height = h;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "rgba(120, 230, 170, .65)";
  const sp = it.speed || 1;
  for (let x = 0; x < w; x += 2) {
    const t = it.in + (x / pps) * sp;
    const db = data[Math.floor(t / 0.25)] ?? -90;
    const v = clamp((db + 55) / 50, 0, 1) * (h - 6);
    ctx.fillRect(x, (h - v) / 2 + 4, 1.5, v);
  }
}

// ---- arrastar, aparar, reordenar
function snapPoints(exceptId) {
  const pts = [0, T, totalDur()];
  mainLayout().forEach((e) => e.it.id !== exceptId && pts.push(e.start, e.end));
  for (const k of ["overlay", "text", "audio"]) P.tracks[k].forEach((it) => { if (it.id !== exceptId) pts.push(...spanOf(k, it)); });
  return pts;
}
function snap(t, exceptId) {
  const line = $("#tl-snapline");
  if (!$("#tl-snap").checked) { line.classList.add("hidden"); return t; }
  let best = null;
  for (const p of snapPoints(exceptId)) if (Math.abs(p - t) * pps < 8 && (best === null || Math.abs(p - t) < Math.abs(best - t))) best = p;
  line.classList.toggle("hidden", best === null);
  if (best !== null) line.style.left = `${best * pps}px`;
  return best ?? t;
}

function bindItem(el, track, it) {
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const mode = e.target.classList.contains("l") ? "trimL" : e.target.classList.contains("r") ? "trimR" : "move";
    sel = { track, id: it.id };
    const before = snapshot();
    const x0 = e.clientX;
    const orig = JSON.parse(JSON.stringify(it));
    const a = assetOf(it);
    const maxOut = a.kind === "image" ? 3600 : a.duration || 1e9;
    const sp = it.speed || 1;
    let moved = false;
    el.setPointerCapture(e.pointerId);
    $$(".tl-item.sel").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel");
    renderInspector();
    syncStage();

    const move = (ev) => {
      const dt = (ev.clientX - x0) / pps;
      if (Math.abs(ev.clientX - x0) > 3) moved = true;
      if (!moved) return;
      if (mode === "move") {
        if (track === "main") { el.style.transform = `translateX(${ev.clientX - x0}px)`; el.style.zIndex = 30; el.style.opacity = .8; return; }
        const len = track === "text" ? orig.dur : itemLen(orig);
        let s = Math.max(0, orig.start + dt);
        const sEnd = snap(s + len, it.id);
        const sStart = snap(s, it.id);
        s = sStart !== s ? sStart : sEnd !== s + len ? sEnd - len : s;
        it.start = +Math.max(0, s).toFixed(3);
      } else if (track === "text") {
        if (mode === "trimL") {
          const s = clamp(snap(orig.start + dt, it.id), 0, orig.start + orig.dur - 0.2);
          it.start = +s.toFixed(3); it.dur = +(orig.start + orig.dur - s).toFixed(3);
          if (orig.kf) { it.kf = JSON.parse(JSON.stringify(orig.kf)); shiftKf(it, -(it.start - orig.start)); }
        } else it.dur = +Math.max(0.2, snap(orig.start + orig.dur + dt, it.id) - orig.start).toFixed(3);
      } else if (mode === "trimL") {
        let d = dt * sp;
        d = clamp(d, -orig.in, orig.out - orig.in - 0.1);
        it.in = +(orig.in + d).toFixed(3);
        if (track !== "main") it.start = +Math.max(0, orig.start + d / sp).toFixed(3);
        if (orig.kf) { it.kf = JSON.parse(JSON.stringify(orig.kf)); shiftKf(it, -d / sp); }
      } else {
        it.out = +clamp(orig.out + dt * sp, orig.in + 0.1, maxOut).toFixed(3);
      }
      renderTimeline();
      syncStage(true);
    };
    const up = (ev) => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      $("#tl-snapline").classList.add("hidden");
      if (!moved) { renderTimeline(); return; }
      if (mode === "move" && track === "main") {
        const t = (ev.clientX - content.getBoundingClientRect().left) / pps;
        const L = mainLayout().filter((x) => x.it.id !== it.id);
        let idx = L.findIndex((x) => t < (x.start + x.end) / 2);
        if (idx < 0) idx = L.length;
        const from = P.tracks.main.indexOf(it);
        P.tracks.main.splice(from, 1);
        P.tracks.main.splice(idx, 0, it);
      }
      commit(() => {}, before);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  });
}

// clique/arraste na régua ou no fundo das faixas = mover o cursor
function scrubFrom(e) {
  const rect = content.getBoundingClientRect();
  const go = (ev) => { stopPlayback(); setTime((ev.clientX - rect.left) / pps); };
  go(e);
  const up = () => { window.removeEventListener("pointermove", go); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointermove", go);
  window.addEventListener("pointerup", up);
}
$("#tl-ruler").addEventListener("pointerdown", scrubFrom);
$$(".tl-track").forEach((tr) => {
  tr.addEventListener("pointerdown", (e) => {
    if (e.target !== tr) return;
    sel = null;
    renderInspector(); renderTimeline();
    scrubFrom(e);
  });
  tr.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/x-asset")) { e.preventDefault(); tr.classList.add("drop"); } });
  tr.addEventListener("dragleave", () => tr.classList.remove("drop"));
  tr.addEventListener("drop", (e) => {
    e.preventDefault();
    tr.classList.remove("drop");
    const aid = e.dataTransfer.getData("text/x-asset");
    if (!aid || !P.assets[aid]) return;
    const t = Math.max(0, (e.clientX - content.getBoundingClientRect().left) / pps);
    const track = tr.dataset.track === "text" ? "overlay" : tr.dataset.track;
    if (track === "main") {
      const L = mainLayout();
      let idx = L.findIndex((x) => t < (x.start + x.end) / 2);
      addAsset(aid, "main", t, idx < 0 ? L.length : idx);
    } else addAsset(aid, track, +snap(t).toFixed(3));
    $("#tl-snapline").classList.add("hidden");
  });
});

// ---- operações
function splitAtPlayhead() {
  let target = sel && findItem() ? { track: sel.track, it: findItem() } : null;
  if (target) { const [s, e] = spanOf(target.track, target.it); if (!(T > s + 0.05 && T < e - 0.05)) target = null; }
  if (!target) { const e = mainLayout().find((x) => T > x.start + 0.05 && T < x.end - 0.05); if (e) target = { track: "main", it: e.it }; }
  if (!target) return;
  commit(() => {
    const { track, it } = target;
    const [s] = spanOf(track, it);
    const copy = JSON.parse(JSON.stringify(it));
    copy.id = uid(track === "text" ? "t" : "i");
    if (track === "text") {
      const parts = splitRich(it, T - it.start);
      if (parts) { Object.assign(it, parts.a); Object.assign(copy, parts.b); }
      shiftKf(copy, -(T - it.start));
      copy.start = +T.toFixed(3); copy.dur = +(it.start + it.dur - T).toFixed(3);
      it.dur = +(T - it.start).toFixed(3);
      P.tracks.text.push(copy);
    } else {
      const cut = +(it.in + (T - s) * (it.speed || 1)).toFixed(3);
      copy.in = cut; it.out = cut;
      shiftKf(copy, -(T - s));
      delete copy.transition;
      if (track === "main") P.tracks.main.splice(P.tracks.main.indexOf(it) + 1, 0, copy);
      else { copy.start = +T.toFixed(3); P.tracks[track].push(copy); }
    }
    sel = { track, id: copy.id };
  });
}
function deleteSelected() {
  const it = findItem();
  if (!it) return;
  commit(() => {
    P.tracks[sel.track].splice(P.tracks[sel.track].indexOf(it), 1);
    sel = null;
  });
}
function duplicateSelected() {
  const it = findItem();
  if (!it) return;
  commit(() => {
    const copy = JSON.parse(JSON.stringify(it));
    copy.id = uid("i");
    if (sel.track === "main") P.tracks.main.splice(P.tracks.main.indexOf(it) + 1, 0, copy);
    else { copy.start = +spanOf(sel.track, it)[1].toFixed(3); P.tracks[sel.track].push(copy); }
    sel = { track: sel.track, id: copy.id };
  });
}

// ---- cortes rápidos: apagar antes/depois do cursor, apagar fechando o buraco, pular entre
// cortes, mudo, copiar/colar e congelar quadro
function itemAtPlayhead() {
  const it = findItem();
  if (it) { const [s, e] = spanOf(sel.track, it); if (T > s + 0.02 && T < e - 0.02) return { track: sel.track, it }; }
  const m = mainLayout().find((x) => T > x.start + 0.02 && T < x.end - 0.02);
  return m ? { track: "main", it: m.it } : null;
}
// "left" (Q) apaga do começo do item até o cursor; "right" (W) apaga do cursor até o fim
function trimAtPlayhead(side) {
  const target = itemAtPlayhead();
  if (!target) return toast("Coloque o cursor em cima de um item (ou selecione um) para cortar");
  const { track, it } = target;
  const [s] = spanOf(track, it);
  commit(() => {
    sel = { track, id: it.id };
    if (track === "text") {
      const parts = splitRich(it, T - it.start);
      if (side === "left") {
        if (parts) Object.assign(it, parts.b);
        shiftKf(it, -(T - it.start));
        it.dur = +(it.start + it.dur - T).toFixed(3);
        it.start = +T.toFixed(3);
      } else {
        if (parts) Object.assign(it, parts.a);
        it.dur = +(T - it.start).toFixed(3);
      }
      return;
    }
    const cut = +(it.in + (T - s) * (it.speed || 1)).toFixed(3);
    if (side === "left") {
      it.in = cut;
      shiftKf(it, -(T - s));
      if (track !== "main") it.start = +T.toFixed(3);
    } else it.out = cut;
  });
  // na faixa principal o buraco fecha sozinho: o cursor volta para o quadro que estava na tela
  if (side === "left" && track === "main") setTime(s);
}
function rippleDelete() {
  const it = findItem();
  if (!it) return;
  const track = sel.track;
  if (track === "main") return deleteSelected();  // a faixa principal já é magnética
  const [s, e] = spanOf(track, it);
  const gap = e - s;
  commit(() => {
    P.tracks[track].splice(P.tracks[track].indexOf(it), 1);
    P.tracks[track].forEach((x) => { if (x.start >= e - 0.001) x.start = +Math.max(0, x.start - gap).toFixed(3); });
    sel = null;
  });
}
function cutPoints() {
  const pts = new Set([0, +totalDur().toFixed(3)]);
  mainLayout().forEach((e) => { pts.add(+e.start.toFixed(3)); pts.add(+e.end.toFixed(3)); });
  for (const k of ["overlay", "text", "audio"]) P.tracks[k].forEach((it) => spanOf(k, it).forEach((t) => pts.add(+t.toFixed(3))));
  return [...pts].sort((a, b) => a - b);
}
function jumpCut(dir) {
  stopPlayback();
  const pts = cutPoints();
  const t = dir < 0 ? pts.reverse().find((p) => p < T - 0.01) : pts.find((p) => p > T + 0.01);
  if (t !== undefined) setTime(t);
}
function toggleMute() {
  const it = findItem();
  if (!it || sel.track === "text") return;
  commit(() => (it.mute = !it.mute));
  toast(it.mute ? "Sem som" : "Som ligado");
}
let clipboard = null;
function copySelected() {
  const it = findItem();
  if (!it) return;
  clipboard = { track: sel.track, item: JSON.parse(JSON.stringify(it)) };
  toast("Copiado — Ctrl+V cola no cursor");
}
function pasteClipboard() {
  if (!clipboard) return;
  const { track } = clipboard;
  if (track !== "text" && !P.assets[clipboard.item.asset]) return toast("A mídia desse item não está mais no projeto");
  commit(() => {
    const copy = JSON.parse(JSON.stringify(clipboard.item));
    copy.id = uid(track === "text" ? "t" : "i");
    if (track === "main") {
      delete copy.transition;
      const L = mainLayout();
      const at = L.findIndex((x) => T < x.end - 0.001);
      P.tracks.main.splice(at < 0 ? L.length : at + 1, 0, copy);
    } else {
      copy.start = +T.toFixed(3);
      P.tracks[track].push(copy);
    }
    sel = { track, id: copy.id };
  });
}
const FREEZE_SECONDS = 2;
async function freezeFrame() {
  const e = mainLayout().find((x) => T >= x.start && T < x.end);
  if (!e) return toast("Coloque o cursor em cima de um vídeo da faixa principal");
  const it = e.it;
  if (assetOf(it).kind !== "video") return toast("Congelar quadro funciona em vídeo");
  const src = +(it.in + (T - e.start) * (it.speed || 1)).toFixed(3);
  const btn = $("#tl-freeze");
  btn.disabled = true;
  toast("Congelando o quadro…");
  let asset;
  try {
    asset = await sendJSON("POST", `/api/studio/projects/${P.id}/freeze`, { asset: it.asset, t: src });
  } catch (ex) {
    btn.disabled = false;
    return toast(`Não deu para congelar: ${ex.message}`);
  }
  btn.disabled = false;
  P.assets[asset.id] = asset;
  commit(() => {
    // a imagem parada herda o enquadramento e a cor do vídeo, para o corte não "pular"
    const still = { id: uid("i"), asset: asset.id, in: 0, out: FREEZE_SECONDS, speed: 1, volume: 1 };
    for (const k of ["fit", "zoom", "pan_x", "pan_y", "brightness", "contrast", "saturation",
      "crop_l", "crop_r", "crop_t", "crop_b", "flip_h", "flip_v", "rotate"]) if (it[k] !== undefined) still[k] = it[k];
    const idx = P.tracks.main.indexOf(it);
    if (T > e.start + 0.05 && T < e.end - 0.05) {
      const rest = JSON.parse(JSON.stringify(it));
      rest.id = uid("i");
      rest.in = src;
      it.out = src;
      delete rest.transition;
      P.tracks.main.splice(idx + 1, 0, still, rest);
    } else if (T <= e.start + 0.05) P.tracks.main.splice(idx, 0, still);
    else P.tracks.main.splice(idx + 1, 0, still);
    sel = { track: "main", id: still.id };
  });
  renderAssets();
  pollAssets();
}
// ---- legendas automáticas (transcrição no servidor; os textos entram aqui, com desfazer)
let acTimer = null;
api("/api/config").then((c) => {
  const sel_ = $("#ac-preset");
  sel_.innerHTML = Object.entries(c.caption_presets || {}).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
}).catch(() => {});
function renderAutocap() {
  const j = P?.captions_job || {};
  const st = $("#ac-status");
  $("#ac-go").disabled = j.status === "running";
  if (j.status === "running") {
    st.innerHTML = `${esc(j.message || "Transcrevendo…")} ${Math.round((j.progress || 0) * 100)}%<div class="bar"><i style="width:${(j.progress || 0) * 100}%"></i></div>`;
  } else if (j.status === "error") st.textContent = `Erro: ${j.error}`;
  else if (j.status === "applied") st.textContent = "Legendas colocadas na timeline (Ctrl+Z desfaz).";
  else st.textContent = "";
}
async function pollAutocap() {
  clearTimeout(acTimer);
  if (!P) return;
  const pid = P.id;
  const fresh = await api(`/api/studio/projects/${pid}`).catch(() => null);
  if (!fresh || !P || P.id !== pid) return;
  P.captions_job = fresh.captions_job;
  const j = P.captions_job || {};
  if (j.status === "done" && Array.isArray(j.items)) {
    commit(() => {
      P.tracks.text = P.tracks.text.filter((t) => !String(t.id).startsWith("tauto"));
      P.tracks.text.push(...j.items);
    });
    await sendJSON("POST", `/api/studio/projects/${pid}/autocaptions/ack`, {}).catch(() => {});
    P.captions_job = { ...j, items: null, status: "applied" };
    toast(j.items.length ? `${j.items.length} legendas adicionadas` : "Não encontrei fala no áudio");
  }
  renderAutocap();
  if ((P.captions_job || {}).status === "running") acTimer = setTimeout(pollAutocap, 1500);
}
$("#ac-go").onclick = async () => {
  if (!P) return;
  await flushSave();
  try {
    await sendJSON("POST", `/api/studio/projects/${P.id}/autocaptions`, {
      preset: $("#ac-preset").value, position: $("#ac-pos").value, color: $("#ac-color").value, upper: $("#ac-upper").checked,
    });
    P.captions_job = { status: "running", progress: 0, message: "Preparando o áudio…" };
    renderAutocap();
    pollAutocap();
  } catch (ex) { $("#ac-status").textContent = `Erro: ${ex.message}`; }
};
let voiceAudio = null;
async function previewVoice(track, it, btn) {
  const [s] = spanOf(track, it);
  const local = clamp(T - s, 0, Math.max(0, spanOf(track, it)[1] - s - 0.5));
  const src = +(it.in + local * (track === "overlay" ? 1 : it.speed || 1)).toFixed(3);
  btn.disabled = true;
  btn.textContent = "Preparando…";
  try {
    await flushSave();
    const r = await sendJSON("POST", `/api/studio/projects/${P.id}/audio-preview`, { track, id: it.id, src });
    stopPlayback();
    voiceAudio?.pause();
    voiceAudio = new Audio(r.url);
    voiceAudio.play().catch(() => {});
  } catch (ex) { toast(`Não deu para ouvir: ${ex.message}`); }
  btn.disabled = false;
  btn.textContent = "▶ Ouvir 6 s com o efeito";
}
let toastTimer = null;
function toast(msg) {
  let el = $("#st-toast");
  if (!el) { el = document.createElement("div"); el.id = "st-toast"; el.className = "st-toast"; document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 2200);
}

$("#tl-split").onclick = splitAtPlayhead;
$("#tl-del").onclick = deleteSelected;
$("#tl-dup").onclick = duplicateSelected;
$("#tl-freeze").onclick = freezeFrame;
$("#tl-trim-l").onclick = () => trimAtPlayhead("left");
$("#tl-trim-r").onclick = () => trimAtPlayhead("right");

document.addEventListener("keydown", (e) => {
  if (!P || $("#editor-view").classList.contains("hidden") || e.target.matches("input, textarea, select") || $("#export-dlg").open) return;
  const k = e.key.toLowerCase();
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && k === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if (ctrl && k === "y") { e.preventDefault(); redo(); }
  else if (ctrl && k === "d") { e.preventDefault(); duplicateSelected(); }
  else if (ctrl && k === "s") { e.preventDefault(); flushSave(); }
  else if (ctrl && k === "c") { e.preventDefault(); copySelected(); }
  else if (ctrl && k === "v") { e.preventDefault(); pasteClipboard(); }
  else if (ctrl && k === "b") { e.preventDefault(); splitAtPlayhead(); }
  else if (ctrl || e.altKey) return;
  else if (e.code === "Space") { e.preventDefault(); $("#play").click(); }
  else if (k === "s") splitAtPlayhead();
  else if (k === "q") trimAtPlayhead("left");
  else if (k === "w") trimAtPlayhead("right");
  else if (k === "f") freezeFrame();
  else if (k === "m") toggleMute();
  else if (k === "n") { $("#tl-snap").checked = !$("#tl-snap").checked; toast($("#tl-snap").checked ? "Ímã ligado" : "Ímã desligado"); }
  else if (k === "arrowup") { e.preventDefault(); jumpCut(-1); }
  else if (k === "arrowdown") { e.preventDefault(); jumpCut(1); }
  else if ((k === "delete" || k === "backspace") && e.shiftKey) rippleDelete();
  else if (k === "delete" || k === "backspace") deleteSelected();
  else if (k === "arrowleft") { stopPlayback(); setTime(T - (e.shiftKey ? 1 : 1 / P.canvas.fps)); }
  else if (k === "arrowright") { stopPlayback(); setTime(T + (e.shiftKey ? 1 : 1 / P.canvas.fps)); }
  else if (k === "home") setTime(0);
  else if (k === "end") setTime(totalDur());
});

// ================================================================ inspetor
function renderInspector() {
  const box = $("#inspector");
  const it = findItem();
  if (!it) return renderProjectInspector(box);
  const track = sel.track;
  const a = assetOf(it);
  const h = [];
  const title = { main: a.kind === "image" ? "Imagem" : "Vídeo", overlay: "Sobreposição", text: "Texto", audio: "Áudio" }[track];
  h.push(`<h3>${title}</h3>`);
  if (track !== "text") h.push(`<p class="muted small" title="${esc(a.path || "")}">${esc(a.name || "")}</p>`);

  if (track === "text") {
    h.push(group("Texto", `<textarea data-k="text" rows="3">${esc(it.text || "")}</textarea>
      ${row2("Fonte", `<select data-k="font">${FONTS.map((f) => `<option ${f === it.font ? "selected" : ""}>${f}</option>`).join("")}</select>`)}
      ${slider("Tamanho", "size", 16, 320, 1, it.size ?? 80)}
      ${seg("Estilo", "style", [["b", "Negrito"], ["i", "Itálico"]], null)}
      ${row2("Cor", `<input type="color" data-k="color" value="${it.color || "#FFFFFF"}">`)}
      ${row2("Contorno", `<input type="color" data-k="stroke" value="${it.stroke || "#000000"}">`)}
      ${slider("Espessura", "stroke_w", 0, 20, 0.5, it.stroke_w ?? 5)}
      ${row2("Fundo", `<label class="check"><input type="checkbox" data-k="bg_on" ${it.bg ? "checked" : ""}> caixa</label> <input type="color" data-k="bg" value="${it.bg || "#E8336B"}">`)}
      ${row2("Sombra", `<label class="check"><input type="checkbox" data-k="shadow_on" ${it.shadow ? "checked" : ""}> sombra</label> <input type="color" data-k="shadow" value="${it.shadow || "#000000"}">`)}
      ${it.shadow ? slider("Distância", "shadow_d", 0, 30, 0.5, it.shadow_d ?? 6) + slider("Opacidade", "shadow_a", 0, 1, 0.05, it.shadow_a ?? 0.8) : ""}`));
    if ((it.words || []).length) {
      h.push(group("Destaque da palavra falada", `${seg("", "hl_mode", [["none", "Nenhum"], ["color", "Cor"], ["box", "Marca-texto"]], it.hl_mode || "color")}
        ${row2("Cor", `<input type="color" data-k="hl" value="${esc(it.hl || "#FFE600")}">`)}
        <p class="muted small">Veio da legenda do clipe. Se mudar o número de palavras, o destaque passa a dividir o tempo igualmente.</p>`));
    }
    if ((it.kw_idx || []).length) {
      h.push(group("Palavras-chave", row2("Cor", `<input type="color" data-k="kw_color" value="${esc(it.kw_color || "#39E75F")}">`)));
    }
    h.push(group("Animação de entrada", seg("", "anim", [["none", "Nenhuma"], ["fade", "Esmaecer"], ["pop", "Pop"], ["slide", "Subir"], ["zoom", "Zoom"]], it.anim || "none")));
    h.push(group("Posição e tempo", `${slider("X", "x", 0, 1, 0.005, it.x ?? 0.5)}${slider("Y", "y", 0, 1, 0.005, it.y ?? 0.5)}
      ${slider("Escala", "scale", 0.2, 3, 0.01, it.scale ?? 1, "x")}${slider("Opacidade", "opacity", 0, 1, 0.01, it.opacity ?? 1)}
      ${num("Início (s)", "start", it.start)}${num("Duração (s)", "dur", it.dur)}`));
  } else {
    if (a.kind !== "image") {
      h.push(group("Trecho do arquivo", `${num("Entrada (s)", "in", it.in)}${num("Saída (s)", "out", it.out)}
        ${track !== "overlay" ? slider("Velocidade", "speed", 0.25, 4, 0.05, it.speed ?? 1, "x") +
          seg("", "speedp", [["0.5", "0,5x"], ["1", "1x"], ["1.5", "1,5x"], ["2", "2x"], ["3", "3x"]], String(it.speed ?? 1)) : ""}`));
    } else {
      h.push(group("Duração", num("Duração (s)", "imgdur", +(it.out - it.in).toFixed(2))));
    }
    if (track !== "main") h.push(group("Tempo", num("Início (s)", "start", it.start)));
    if (track === "main") {
      h.push(group("Enquadramento", `${seg("", "fit", [["fill", "Preencher"], ["fit", "Encaixar"], ["blur", "Fundo desfocado"]], it.fit || "fill")}
        ${(it.fit || "fill") === "fill" ? slider("Zoom", "zoom", 1, 3, 0.01, it.zoom ?? 1, "x") + slider("Posição X", "pan_x", -1, 1, 0.01, it.pan_x ?? 0) + slider("Posição Y", "pan_y", -1, 1, 0.01, it.pan_y ?? 0) : ""}`));
    }
    if (track === "overlay") {
      h.push(group("Posição", `${slider("X", "x", 0, 1, 0.005, it.x ?? 0.5)}${slider("Y", "y", 0, 1, 0.005, it.y ?? 0.5)}
        ${slider("Tamanho", "scale", 0.05, 1.5, 0.01, it.scale ?? 0.4)}${slider("Opacidade", "opacity", 0, 1, 0.01, it.opacity ?? 1)}`));
    }
    if (track !== "audio" && a.kind !== "audio" && FX) {
      const looks = [{ id: "", label: "Nenhum" }, ...FX.looks];
      h.push(group("Filtro de cor", `<div class="look-grid">${looks.map((l) =>
        `<button class="ghost ${(it.look || "") === l.id ? "on" : ""}" data-look="${l.id}">${esc(l.label)}</button>`).join("")}</div>
        ${it.look ? slider("Intensidade", "look_amt", 0, 1, 0.05, it.look_amt ?? 1, "", (v) => `${Math.round(v * 100)}%`) : ""}`));
      if (track === "overlay") {
        const kk = it.key || "";
        h.push(group("Recorte de fundo", `${seg("", "key", FX.keys.map((x) => [x.id, x.label]), kk)}
          ${kk === "custom" ? row2("Cor", `<input type="color" data-k="key_color" value="${esc(it.key_color || "#00FF00")}">`) : ""}
          ${kk ? slider("Tolerância", "key_sim", 0.01, 0.6, 0.01, it.key_sim ?? (kk === "black" || kk === "white" ? 0.12 : 0.3)) +
            slider("Suavidade", "key_blend", 0, 0.5, 0.01, it.key_blend ?? 0.1) +
            `<p class="muted small">Fundo verde/azul de meme, ou tirar o preto/branco de efeitos (fogo, explosão, brilho). A prévia é aproximada; o vídeo final usa o recorte do ffmpeg.</p>` : ""}`));
        h.push(group("Mesclagem", row2("Modo", `<select data-k="blend">${FX.blends.map((x) => `<option value="${x.id}" ${(it.blend || "") === x.id ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select>`) +
          `<p class="muted small">"Tela" e "Somar" deixam só as partes claras (bom para efeitos em fundo preto); "Multiplicar" deixa só as escuras.</p>`));
      }
      const effects = track === "main" ? FX.effects : FX.effects.filter((x) => x.id === "shake");
      h.push(group("Efeitos", effects.map((x) => slider(x.label, `fx_${x.id}`, 0, 1, 0.05, fxv(it, x.id), "", (v) => `${Math.round(v * 100)}%`)).join("") +
        (track === "overlay" ? `<p class="muted small">Na sobreposição só o tremor; os outros efeitos valem para o vídeo principal.</p>` : "") +
        `<div class="insp-actions"><button class="ghost" data-act="resetfx">Tirar efeitos</button></div>`));
    }
    if (track !== "audio" && a.kind !== "audio") {
      const pct = (k) => +((it[k] || 0) * 100).toFixed(1);
      h.push(group("Cortar, espelhar e girar", `${slider("Corte ←", "crop_l", 0, 45, 1, pct("crop_l"), "%")}
        ${slider("Corte →", "crop_r", 0, 45, 1, pct("crop_r"), "%")}${slider("Corte ↑", "crop_t", 0, 45, 1, pct("crop_t"), "%")}
        ${slider("Corte ↓", "crop_b", 0, 45, 1, pct("crop_b"), "%")}
        <div class="insp-actions"><button class="ghost ${it.flip_h ? "on" : ""}" data-act="fliph">↔ Espelhar</button>
        <button class="ghost ${it.flip_v ? "on" : ""}" data-act="flipv">↕ Virar de cabeça</button></div>
        ${slider("Rotação", "rotate", -180, 180, 1, it.rotate ?? 0, "°")}
        ${seg("", "rotp", [["-90", "-90°"], ["0", "0°"], ["90", "90°"], ["180", "180°"]], String(it.rotate ?? 0))}
        <div class="insp-actions"><button class="ghost" data-act="resetgeo">Restaurar</button></div>`));
      h.push(group("Ajustes de cor", `${slider("Brilho", "brightness", -0.5, 0.5, 0.01, it.brightness ?? 0)}
        ${slider("Contraste", "contrast", 0.5, 2, 0.01, it.contrast ?? 1)}${slider("Saturação", "saturation", 0, 3, 0.01, it.saturation ?? 1)}
        <div class="insp-actions"><button class="ghost" data-act="resetcolor">Restaurar cor</button>
        <button class="ghost" data-act="pb">P&amp;B</button><button class="ghost" data-act="vivid">Vivo</button><button class="ghost" data-act="cine">Cinema</button></div>`));
    }
    if (a.has_audio || track === "audio") {
      h.push(group("Áudio", `${slider("Volume", "volume", 0, 2, 0.01, it.volume ?? (track === "overlay" ? 0 : 1), "", (v) => `${Math.round(v * 100)}%`)}
        ${row2("", `<label class="check"><input type="checkbox" data-k="mute" ${it.mute ? "checked" : ""}> Sem som</label>`)}`));
      if (FX) {
        h.push(group("Voz", `${row2("", `<label class="check"><input type="checkbox" data-k="voice_clean" ${it.voice_clean ? "checked" : ""}> Limpar voz (tira ruído e nivela o volume)</label>`)}
          ${row2("Efeito", `<select data-k="voice_fx">${FX.voices.map((v) => `<option value="${v.id}" ${(it.voice_fx || "") === v.id ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>`)}
          <div class="insp-actions"><button class="secondary" data-act="voiceprev">▶ Ouvir 6 s com o efeito</button></div>
          <p class="muted small">A prévia normal toca o som original; a limpeza e o efeito entram na exportação e no botão acima.</p>`));
      }
    }
    if (track === "main" || track === "audio") {
      h.push(group(track === "main" ? "Fade (preto/silêncio)" : "Fade do áudio",
        `${slider("Entrada", "fade_in", 0, 3, 0.05, it.fade_in ?? 0, "s")}${slider("Saída", "fade_out", 0, 3, 0.05, it.fade_out ?? 0, "s")}`));
    }
    if (track === "main" && P.tracks.main.indexOf(it) > 0) {
      const tr = it.transition || {};
      h.push(group("Transição de entrada", `${row2("Tipo", `<select data-k="ttype">${TRANSITIONS.map(([k, l]) => `<option value="${k}" ${k === (tr.type || "none") ? "selected" : ""}>${l}</option>`).join("")}</select>`)}
        ${tr.type ? slider("Duração", "tdur", 0.1, 2, 0.05, tr.dur ?? 0.5, "s") : ""}`));
    }
  }
  if (KF_PROPS[track]) {
    const n = kfTimes(it).length;
    h.push(group("Keyframes (animação)", `<div class="insp-actions"><button class="ghost" data-act="kfprev" title="Chave anterior">◀ ◆</button>
      <button class="ghost" data-act="kfnext" title="Próxima chave">◆ ▶</button>${n ? `<button class="ghost danger" data-act="kfclear">Apagar todas (${n})</button>` : ""}</div>
      ${seg("Curva", "kf_ease", [["linear", "Linear"], ["smooth", "Suave"]], it.kf_ease || "linear")}
      <p class="muted small">Clique no ◆ ao lado de um valor (zoom, posição, tamanho, opacidade, rotação, volume) para marcar uma chave no ponto do cursor.
      Depois mova o cursor e mude o valor: outra chave é criada e, entre as chaves, o valor anda sozinho.</p>`));
  }
  h.push(`<div class="insp-actions"><button class="secondary" data-act="split">✂ Dividir</button>
    <button class="ghost" data-act="dup">⧉ Duplicar</button><button class="ghost danger" data-act="del">🗑 Excluir</button></div>`);
  box.innerHTML = h.join("");
  // botão ◆ ao lado de cada valor que dá para animar, mostrando o valor no ponto do cursor
  for (const k of KF_PROPS[track] || []) {
    const input = $(`[data-k="${k}"]`, box);
    if (!input || input.type !== "range") continue;
    const b = document.createElement("button");
    b.className = "kfb";
    b.dataset.kf = k;
    b.title = "Keyframe neste ponto";
    b.textContent = "◆";
    b.onclick = () => toggleKey(track, it, k);
    input.parentElement.classList.add("kf-row");
    input.parentElement.appendChild(b);
    if (hasKf(it, k)) input.value = kval(track, it, k);
  }
  bindInspector(box, it, track);
  refreshKfInspector();
}

function renderProjectInspector(box) {
  const c = P.canvas;
  box.innerHTML = `<h3>Projeto</h3>
    ${group("Formato", seg("", "ratio", [["9:16", "9:16"], ["16:9", "16:9"], ["1:1", "1:1"], ["4:5", "4:5"], ["3:4", "3:4"]], c.ratio))}
    ${group("Quadros por segundo", seg("", "fps", [["24", "24"], ["25", "25"], ["30", "30"], ["60", "60"]], String(c.fps)))}
    ${group("Cor de fundo", row2("Fundo", `<input type="color" data-k="bg" value="${c.bg}">`))}
    <p class="muted small">Duração: ${fmt(totalDur())} · ${c.w}×${c.h}<br>${P.tracks.main.length} vídeo(s), ${P.tracks.text.length} texto(s),
    ${P.tracks.overlay.length} sobreposição(ões), ${P.tracks.audio.length} áudio(s).</p>
    <p class="muted small">Atalhos: espaço toca/pausa · S ou Ctrl+B divide · Q apaga do começo até o cursor · W apaga do cursor até o fim ·
    F congela o quadro · Delete exclui (Shift+Delete fecha o buraco) · M sem som · N liga/desliga o ímã · ↑/↓ pula entre cortes ·
    Ctrl+C/V copia e cola no cursor · Ctrl+Z/Y desfaz/refaz · Ctrl+D duplica · ←/→ quadro a quadro (Shift = 1 s) · Ctrl+roda = zoom da timeline.</p>`;
  bindSeg(box, "ratio", (v) => commit(() => {
    const dims = { "9:16": [1080, 1920], "16:9": [1920, 1080], "1:1": [1080, 1080], "4:5": [1080, 1350], "3:4": [1080, 1440] }[v];
    Object.assign(P.canvas, { ratio: v, w: dims[0], h: dims[1] });
    layoutStage();
  }));
  bindSeg(box, "fps", (v) => commit(() => (P.canvas.fps = +v)));
  liveInput(box, "bg", null, (v) => { P.canvas.bg = v; stage.style.background = v; });
}

// ---- pedaços de HTML do inspetor
const group = (title, body) => `<div class="insp-group">${title ? `<h4>${title}</h4>` : ""}${body}</div>`;
const row2 = (label, html) => `<div class="insp-row two"><span>${label}</span><div>${html}</div></div>`;
const num = (label, k, v) => `<div class="insp-row two"><span>${label}</span><input type="number" step="0.05" data-k="${k}" value="${(+v || 0).toFixed(2)}"></div>`;
function slider(label, k, min, max, step, v, unit = "", f = null) {
  const show = f ? f(v) : `${(+v).toFixed(step < 0.1 ? 2 : 1)}${unit}`;
  return `<div class="insp-row"><span>${label}</span><input type="range" data-k="${k}" min="${min}" max="${max}" step="${step}" value="${v}"><output>${show}</output></div>`;
}
function seg(label, k, opts, cur) {
  return `${label ? `<span class="muted small">${label}</span>` : ""}<div class="seg" data-seg="${k}">${opts.map(([v, l]) =>
    `<button data-v="${v}" class="${v === cur ? "on" : ""}">${l}</button>`).join("")}</div>`;
}
function bindSeg(box, k, fn) {
  $$(`[data-seg="${k}"] button`, box).forEach((b) => (b.onclick = () => fn(b.dataset.v)));
}
// campo que atualiza a prévia enquanto mexe e grava no histórico ao soltar
function liveInput(box, k, it, apply) {
  const el = $(`[data-k="${k}"]`, box);
  if (!el) return;
  let before = null;
  const out = el.parentElement.querySelector("output");
  el.addEventListener("input", () => {
    before = before || snapshot();
    const v = el.type === "checkbox" ? el.checked : el.type === "range" || el.type === "number" ? +el.value : el.value;
    const tr = sel?.track;
    if (!(it && tr && (KF_PROPS[tr] || []).includes(k) && setAnim(tr, it, k, +(+v).toFixed(4)))) apply(v);
    if (out && el.type === "range") out.textContent = el.step < 0.1 ? (+el.value).toFixed(2) : (+el.value).toFixed(1);
    syncStage(true);
    if (["in", "out", "speed", "start", "dur", "imgdur", "tdur"].includes(k)) renderTimeline();
  });
  el.addEventListener("change", () => {
    const b = before || snapshot();
    before = null;
    commit(() => {}, b);
  });
}
function bindInspector(box, it, track) {
  const a = assetOf(it);
  const set = (k, round = 3) => (v) => (it[k] = typeof v === "number" ? +v.toFixed(round) : v);
  ["text", "font", "color", "stroke", "x", "y", "scale", "opacity", "brightness", "contrast", "saturation", "volume",
    "fade_in", "fade_out", "zoom", "pan_x", "pan_y", "stroke_w", "size", "hl", "kw_color"].forEach((k) => liveInput(box, k, it, set(k)));
  ["crop_l", "crop_r", "crop_t", "crop_b"].forEach((k) => liveInput(box, k, it, (v) => (it[k] = +(v / 100).toFixed(3))));
  liveInput(box, "rotate", it, (v) => (it.rotate = Math.round(v)));
  liveInput(box, "look_amt", it, set("look_amt", 2));
  liveInput(box, "key_sim", it, set("key_sim", 2));
  liveInput(box, "key_blend", it, set("key_blend", 2));
  liveInput(box, "key_color", it, (v) => (it.key_color = v));
  liveInput(box, "blend", it, (v) => (v ? (it.blend = v) : delete it.blend));
  bindSeg(box, "key", (v) => commit(() => { if (v) it.key = v; else { delete it.key; delete it.key_sim; delete it.key_blend; } }));
  (FX?.effects || []).forEach((x) => liveInput(box, `fx_${x.id}`, it, (v) => {
    it.fx = { ...(it.fx || {}), [x.id]: +v.toFixed(2) };
    if (!it.fx[x.id]) delete it.fx[x.id];
    if (!Object.keys(it.fx).length) delete it.fx;
  }));
  liveInput(box, "voice_clean", it, (v) => (v ? (it.voice_clean = true) : delete it.voice_clean));
  liveInput(box, "voice_fx", it, (v) => (v ? (it.voice_fx = v) : delete it.voice_fx));
  $$("[data-look]", box).forEach((b) => (b.onclick = () => commit(() => {
    if (b.dataset.look) { it.look = b.dataset.look; it.look_amt = it.look_amt ?? 1; } else { delete it.look; delete it.look_amt; }
  })));
  liveInput(box, "shadow_d", it, set("shadow_d", 1));
  liveInput(box, "shadow_a", it, set("shadow_a", 2));
  liveInput(box, "shadow", it, (v) => (it.shadow = v));
  liveInput(box, "shadow_on", it, (v) => (it.shadow = v ? it.shadow || "#000000" : ""));
  liveInput(box, "start", it, (v) => (it.start = Math.max(0, +v.toFixed(3))));
  liveInput(box, "dur", it, (v) => (it.dur = Math.max(0.2, +v.toFixed(3))));
  liveInput(box, "in", it, (v) => (it.in = clamp(+v.toFixed(3), 0, it.out - 0.1)));
  liveInput(box, "out", it, (v) => (it.out = clamp(+v.toFixed(3), it.in + 0.1, a.duration || 1e9)));
  liveInput(box, "imgdur", it, (v) => (it.out = +(it.in + Math.max(0.2, v)).toFixed(3)));
  liveInput(box, "speed", it, (v) => (it.speed = +v.toFixed(2)));
  liveInput(box, "mute", it, (v) => (it.mute = v));
  liveInput(box, "bg_on", it, (v) => (it.bg = v ? $('[data-k="bg"]', box).value : ""));
  liveInput(box, "bg", it, (v) => { if (it.bg) it.bg = v; });
  liveInput(box, "tdur", it, (v) => (it.transition = { ...(it.transition || {}), dur: +v.toFixed(2) }));
  const tt = $('[data-k="ttype"]', box);
  if (tt) tt.onchange = () => commit(() => (it.transition = tt.value === "none" ? undefined : { type: tt.value, dur: it.transition?.dur || 0.5 }));
  bindSeg(box, "anim", (v) => commit(() => (it.anim = v)));
  bindSeg(box, "hl_mode", (v) => commit(() => (it.hl_mode = v)));
  bindSeg(box, "fit", (v) => commit(() => (it.fit = v)));
  bindSeg(box, "speedp", (v) => commit(() => (it.speed = +v)));
  bindSeg(box, "rotp", (v) => commit(() => { if (!setAnim(track, it, "rotate", +v)) it.rotate = +v; }));
  bindSeg(box, "kf_ease", (v) => commit(() => (v === "smooth" ? (it.kf_ease = v) : delete it.kf_ease)));
  bindSeg(box, "style", (v) => commit(() => (v === "b" ? (it.bold = it.bold === false) : (it.italic = !it.italic))));
  $$("[data-act]", box).forEach((b) => (b.onclick = () => {
    const act = b.dataset.act;
    if (act === "split") return splitAtPlayhead();
    if (act === "dup") return duplicateSelected();
    if (act === "del") return deleteSelected();
    if (act === "resetfx") return commit(() => delete it.fx);
    if (act === "kfclear") return commit(() => { delete it.kf; });
    if (act === "kfprev" || act === "kfnext") {
      const s0 = spanOf(track, it)[0], ts = kfTimes(it).map((t) => s0 + t);
      const t = act === "kfprev" ? ts.reverse().find((x) => x < T - 0.01) : ts.find((x) => x > T + 0.01);
      if (t !== undefined) { stopPlayback(); setTime(t); }
      return;
    }
    if (act === "voiceprev") return previewVoice(track, it, b);
    if (act === "fliph") return commit(() => (it.flip_h = !it.flip_h));
    if (act === "flipv") return commit(() => (it.flip_v = !it.flip_v));
    if (act === "resetgeo") return commit(() => ["crop_l", "crop_r", "crop_t", "crop_b", "flip_h", "flip_v", "rotate"].forEach((k) => delete it[k]));
    const looks = { resetcolor: [0, 1, 1], pb: [0, 1.1, 0], vivid: [0.03, 1.15, 1.6], cine: [-0.04, 1.2, 0.8] }[act];
    if (looks) commit(() => Object.assign(it, { brightness: looks[0], contrast: looks[1], saturation: looks[2] }));
  }));
}

// ================================================================ exportar
const exDlg = $("#export-dlg");
let exPoll = null;
function openExport() {
  exDlg.showModal();
  renderExport();
}
$("#export-btn").onclick = async () => { await flushSave(); openExport(); };
$("#ex-close").onclick = () => { exDlg.close(); clearTimeout(exPoll); };
$("#ex-start").onclick = async () => {
  await flushSave();
  try {
    await sendJSON("POST", `/api/studio/projects/${P.id}/export`, { resolution: $("#ex-res").value, quality: $("#ex-q").value });
    P.export = { status: "running", progress: 0 };
    renderExport();
  } catch (ex) { $("#ex-status").innerHTML = `<div class="err"></div>`; $("#ex-status .err").textContent = ex.message; }
};
function renderExport() {
  const e = P.export || {};
  const st = $("#ex-status");
  $("#ex-download").classList.add("hidden");
  $("#ex-start").disabled = e.status === "running";
  if (e.status === "running") {
    st.innerHTML = `Exportando… ${Math.round((e.progress || 0) * 100)}%<div class="bar"><i style="width:${(e.progress || 0) * 100}%"></i></div>`;
    clearTimeout(exPoll);
    exPoll = setTimeout(async () => { P.export = (await api(`/api/studio/projects/${P.id}`)).export; if (exDlg.open) renderExport(); }, 1000);
  } else if (e.status === "done") {
    const secs = e.finished && e.started ? ` em ${Math.round(e.finished - e.started)} s` : "";
    st.textContent = `Pronto${secs}: ${fmt(e.duration)} de vídeo. Salvo na pasta do projeto.`;
    const dl = $("#ex-download");
    dl.href = `/media/${e.file}`;
    dl.download = e.file.split("/").pop();
    dl.classList.remove("hidden");
  } else if (e.status === "error") {
    st.innerHTML = `<div class="err"></div>`;
    $("#ex-status .err").textContent = `Erro: ${e.error}`;
  } else {
    st.textContent = `${fmt(totalDur())} · ${P.canvas.w}×${P.canvas.h} · ${P.canvas.fps} fps. A exportação usa a GPU (NVENC) quando disponível.`;
  }
}

route();
