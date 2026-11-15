const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
// nome para exibir: o arquivo baixado vem como "Tentando+pegar+champ (1).mp4"
const prettyName = (s) => { s = s || ""; try { s = decodeURIComponent(s); } catch {} return s.replace(/\+/g, " ").replace(/\.(mp4|mkv|mov|webm|flv|avi|ts)$/i, "").trim() || s; };

const STAGES = {
  queued: "Na fila",
  downloading: "Baixando",
  transcribing: "Transcrevendo",
  selecting: "Claude escolhendo candidatos",
  reviewing: "Claude assistindo aos candidatos",
  titling: "Claude escrevendo títulos",
  waiting_manual: "Esperando sua resposta do Claude",
  mapping: "Claude separando as partidas",
  waiting_match: "Escolha a partida",
  rendering: "Renderizando",
  done: "Pronto",
  error: "Erro",
};
const BUSY = new Set(["queued", "downloading", "transcribing", "selecting", "reviewing", "titling", "rendering", "mapping"]);
const LAYOUTS = { face: "seguindo rosto", fit: "vídeo inteiro", split: "tela dividida", manual: "posição fixa" };
const SCORE_NAMES = { hook: "Gancho", flow: "Ritmo", value: "Graça", trend: "Compartilhável" };
// mesmos limites de agrupamento de captions.py, para a prévia do editor
const PRESET_CHUNK = { destaque: [3, 18], marcador: [3, 18], uma: [1, 99], minimal: [6, 34] };

let source = "url";
let pollTimer = null;
let stylePoll = null;
let currentJob = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}
const postJSON = (path, body) => api(path, {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
});

const fmtTime = (s, decimals = false) => {
  s = Math.max(0, s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  const sec = decimals ? (s % 60).toFixed(1).padStart(4, "0") : String(Math.floor(s % 60)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
};
const parseTime = (v) => {
  v = (v || "").trim();
  if (!v) return 0;
  return v.split(":").reduce((acc, part) => acc * 60 + (parseFloat(part) || 0), 0);
};

// ---------------------------------------------------------------- navegação
function route() {
  clearTimeout(pollTimer);
  const m = location.hash.match(/^#\/job\/(.+)$/);
  $("#home").classList.toggle("hidden", !!m);
  $("#job").classList.toggle("hidden", !m);
  if (m) openJob(decodeURIComponent(m[1]));
  else { currentJob = null; loadJobs(); loadStyles(); }
}
window.addEventListener("hashchange", route);
document.addEventListener("click", (e) => {
  const nav = e.target.closest("[data-nav=home]");
  if (nav) { e.preventDefault(); location.hash = ""; }
});

// ---------------------------------------------------------------- config
async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    const parts = [];
    if (cfg.cli.logged_in) parts.push("<b>Claude Code</b> logado");
    else if (cfg.cli.available) parts.push("Claude Code encontrado (não logado)");
    if (cfg.api) parts.push("<b>API</b> configurada");
    if (!parts.length) parts.push("sem login — usando modo <b>manual</b>");
    $("#ai-status").innerHTML = `IA: ${parts.join(" · ")} · ${cfg.model}`;
    $$(".preset-select").forEach((sel) => {
      sel.innerHTML = Object.entries(cfg.caption_presets).map(([k, label]) => `<option value="${k}">${label}</option>`).join("");
    });
    $$("select[name=cover_style]").forEach((sel) => {
      sel.innerHTML = Object.entries(cfg.cover_styles || {}).map(([k, label]) => `<option value="${k}">${label}</option>`).join("");
    });
    // padrões (de fábrica + os salvos pelo usuário) no formulário e no painel "estilo de todos"
    fillFields($("#job-form"), cfg.defaults);
    fillFields($("#apply-form"), cfg.defaults);
    $("#match-serie").value = cfg.defaults.serie || "";
  } catch {}
}

function fillFields(root, values) {
  for (const [k, v] of Object.entries(values || {})) {
    const el = root.querySelector(`[name="${k}"]`);
    if (!el || k === "style_id") continue;
    if (el.type === "checkbox") el.checked = !!v;
    else if (["range_start", "range_end"].includes(k)) el.value = v ? fmtTime(v) : "";
    else el.value = v;
  }
}

function readFields(root, names) {
  const out = {};
  for (const name of names) {
    const el = root.querySelector(`[name="${name}"]`);
    if (el) out[name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return out;
}
const COVER_FIELDS = ["cover_auto", "cover_style", "cover_handle", "cover_in_video"];
const STYLE_FIELDS = ["layout", "caption_preset", "caption_position", "caption_color", "keyword_color", "title_mode",
  "title_position", "title_style", "captions", "caption_upper", "keyword_highlight", "title_overlay", "auto_zoom", "remove_pauses", "normalize_audio", "voice_enhance",
  "pos_susto", "pos_susto_musica"];

// ---------------------------------------------------------------- estilos
let styleCache = [];
const RECIPE_LABELS = {
  caption_preset: () => "legenda pequena",
  title_mode: (v) => (v === "hook" ? "título só no começo" : ""), remove_pauses: (v) => (v ? "" : "sem cortar pausas"),
  pos_susto: (v) => ({ congelar: "congela no grito", pb: "P&B no susto", congelar_pb: "congela + P&B no susto" }[v] || ""),
  cover_style: (v) => `capa ${v}`,
};
// estilo com receita (ex.: Terror) preenche as opções do formulário; dá para mudar depois
function applyRecipe() {
  const st = styleCache.find((s) => s.id === $("#style-select").value);
  const box = $("#style-recipe");
  if (!st?.recipe) { box.classList.add("hidden"); return; }
  fillFields($("#job-form"), st.recipe);
  const r = st.recipe;
  const bits = [r.min_len && r.max_len ? `clipes de ${r.min_len} a ${r.max_len} s` : "",
    ...Object.entries(r).filter(([k]) => !["min_len", "max_len"].includes(k)).map(([k, v]) => RECIPE_LABELS[k]?.(v))].filter(Boolean);
  box.textContent = `Receita do estilo aplicada nas opções: ${bits.join(", ")}. Dá para mudar em "Mais opções".`;
  box.classList.remove("hidden");
}
$("#style-select").addEventListener("change", applyRecipe);

async function loadStyles() {
  clearTimeout(stylePoll);
  const list = await api("/api/styles");
  styleCache = list;
  const sel = $("#style-select");
  const current = sel.value;
  sel.innerHTML = `<option value="">Nenhum (critérios gerais)</option>` + list
    .filter((s) => s.status === "ready")
    .map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  sel.value = current || "streamer-br";
  if (!sel.value) sel.value = "";

  $("#style-list").replaceChildren(...list.map((s) => {
    const el = document.createElement("div");
    el.className = "style-card";
    const badge = s.status === "ready" ? "pronto" : s.status === "error" ? "erro" : "aprendendo…";
    el.innerHTML = `<div class="st-head"><span class="st-name"></span><span class="badge ${s.status}">${badge}</span></div>
      <div class="muted small st-sub"></div>
      ${s.guide ? `<details><summary class="small">Ver guia</summary><pre></pre></details>` : ""}
      ${s.builtin ? "" : `<button class="ghost danger small st-del">Excluir</button>`}`;
    $(".st-name", el).textContent = s.name;
    $(".st-sub", el).textContent = s.status === "learning" ? (s.message || "") :
      s.status === "error" ? s.error : `${s.summary || ""} ${s.sources.length} referência(s).`;
    if (s.guide) $("pre", el).textContent = s.guide;
    const del = $(".st-del", el);
    if (del) del.onclick = async () => {
      if (!confirm(`Excluir o estilo "${s.name}"?`)) return;
      await api(`/api/styles/${s.id}`, { method: "DELETE" });
      loadStyles();
    };
    return el;
  }));
  if (list.some((s) => s.status === "learning") && !currentJob) stylePoll = setTimeout(loadStyles, 3000);
}

$("#style-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#style-error").textContent = "";
  try {
    await postJSON("/api/styles", {
      name: f.name.value, notes: f.notes.value,
      urls: f.urls.value.split(/\s+/).filter(Boolean),
    });
    f.reset();
    loadStyles();
  } catch (ex) {
    $("#style-error").textContent = ex.message;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------- formulário
$$(".tab").forEach((tab) => tab.addEventListener("click", (e) => {
  e.preventDefault();
  source = tab.dataset.src;
  $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  $$(".src").forEach((el) => el.classList.toggle("hidden", !el.classList.contains(`src-${source}`)));
}));

const drop = $("#drop");
const fileInput = $("input[name=file]");
fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  $("#drop-label").textContent = file?.name || "Arraste um vídeo aqui ou clique para escolher";
  const big = file && file.size > 1024 ** 3;
  $("#big-hint").classList.toggle("hidden", !big);
  if (big) $("#big-hint").textContent =
    `Arquivo de ${(file.size / 1024 ** 3).toFixed(1)} GB. Se ele já está neste PC, a aba "Arquivo no PC" é bem mais rápida: usa o arquivo onde está, sem copiar.`;
});
["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; fileInput.dispatchEvent(new Event("change")); }
});

$("#job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const err = $("#form-error");
  err.textContent = "";
  const options = {
    num_clips: +f.num_clips.value, min_len: +f.min_len.value, max_len: +f.max_len.value,
    instructions: f.instructions.value, content_type: f.content_type.value, style_id: f.style_id.value,
    visual_review: f.visual_review.checked, layout: f.layout.value,
    mode: f.mode.value, highlight_basis: f.highlight_basis.value,
    serie: f.serie.value.trim(), serie_n: f.serie_n.value ? +f.serie_n.value : null,
    range_start: parseTime(f.range_start.value), range_end: parseTime(f.range_end.value),
    language: f.language.value, backend: f.backend.value, whisper_model: f.whisper_model.value,
    ...readFields(f, STYLE_FIELDS), ...readFields(f, COVER_FIELDS),
  };
  const body = new FormData();
  body.append("options", JSON.stringify(options));
  if (source === "url") {
    if (!f.url.value.trim()) return (err.textContent = "Cole um link.");
    body.append("url", f.url.value.trim());
  } else if (source === "file") {
    if (!fileInput.files[0]) return (err.textContent = "Escolha um arquivo.");
  } else {
    if (!f.local_path.value.trim()) return (err.textContent = "Informe o caminho do arquivo.");
    body.append("local_path", f.local_path.value.trim());
  }
  const btn = $("#submit");
  btn.disabled = true;
  btn.textContent = source === "file" ? "Enviando… 0%" : "Criando…";
  try {
    if (source === "file") {
      const file = fileInput.files[0];
      const { upload_id } = await uploadFile(file, (p) => (btn.textContent = `Enviando… ${Math.floor(p * 100)}%`));
      body.append("upload_id", upload_id);
      body.append("name", file.name);
      btn.textContent = "Criando…";
    }
    const { id } = await api("/api/jobs", { method: "POST", body });
    location.hash = `#/job/${id}`;
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    btn.disabled = false;
    syncMode();
  }
});

const modeSelect = $("#job-form [name=mode]");
const syncMode = () => {
  const hl = modeSelect.value === "highlights";
  $$(".hl-only").forEach((el) => el.classList.toggle("hidden", !hl));
  $("#submit").textContent = hl ? "Gerar melhores momentos" : "Gerar cortes";
};
modeSelect.addEventListener("change", syncMode);

// fetch não informa progresso de envio; XHR sim.
function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/uploads?filename=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `falha no envio (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("falha de conexão durante o envio"));
    xhr.send(file);
  });
}

// ---------------------------------------------------------------- lista
async function loadJobs() {
  const list = $("#job-list");
  const items = await api("/api/jobs");
  if (!items.length) { list.innerHTML = `<p class="empty">Nenhum projeto ainda.</p>`; return; }
  list.replaceChildren(...items.map((j) => {
    const el = document.createElement("button");
    el.className = "job-item";
    el.innerHTML = `${j.thumb ? `<img alt="" src="/media/${j.thumb}">` : `<div class="ph"></div>`}
      <div class="meta"><div class="name"></div><div class="sub"></div></div>`;
    $(".name", el).textContent = prettyName(j.name);
    $(".sub", el).textContent = `${STAGES[j.status] || j.status}${j.clips ? ` · ${j.clips} clipes` : ""} · ${new Date(j.created * 1000).toLocaleString("pt-BR")}`;
    el.onclick = () => (location.hash = `#/job/${j.id}`);
    return el;
  }));
  if (items.some((j) => BUSY.has(j.status)) && !currentJob) pollTimer = setTimeout(loadJobs, 3000);
}

// ---------------------------------------------------------------- job
async function openJob(id) {
  clearTimeout(pollTimer);
  try {
    currentJob = await api(`/api/jobs/${id}`);
  } catch (ex) {
    $("#job-status").innerHTML = `<p class="err"></p>`;
    $("#job-status .err").textContent = ex.message;
    return;
  }
  renderJob(currentJob);
  const rendering = currentJob.clips.some((c) => c.status === "rendering" || c.status === "pending");
  if (BUSY.has(currentJob.status) || rendering) pollTimer = setTimeout(() => openJob(id), 1500);
}

function renderJob(job) {
  $("#job-title").textContent = prettyName(job.name);
  const st = $("#job-status");
  const busy = BUSY.has(job.status);
  const indeterminate = ["selecting", "downloading", "queued", "titling"].includes(job.status) ||
    (job.status === "reviewing" && job.message.startsWith("Claude"));
  st.innerHTML = `<div class="msg"></div>${busy ? `<div class="bar ${indeterminate ? "indeterminate" : ""}"><i></i></div>` : ""}
    ${job.error ? `<div class="err"></div>` : ""}`;
  const extra = job.backend ? ` · IA: ${{ cli: "Claude Code", api: "API Anthropic", manual: "manual" }[job.backend]}` : "";
  const dur = job.source.duration ? ` · vídeo de ${fmtTime(job.source.duration)}` : "";
  const stage = STAGES[job.status] || job.status;
  const msg = job.message && job.message !== stage ? `${stage} — ${job.message}` : stage;
  $(".msg", st).textContent = `${msg}${dur}${extra}`;
  if (busy) $(".bar > i", st).style.width = `${Math.round((job.progress || 0) * 100)}%`;
  if (job.error) $(".err", st).textContent = job.error;

  $("#manual").classList.toggle("hidden", job.status !== "waiting_manual");
  const ready = ["done", "error"].includes(job.status) && !!job.source.duration;
  $("#job-tools").classList.toggle("hidden", !ready);
  if (!ready) $$(".panel").forEach((p) => p.classList.add("hidden"));
  $("#zip-btn").href = `/api/jobs/${job.id}/export.zip`;
  $("#covers-btn").classList.toggle("hidden", !job.clips.some((c) => c.status === "done" && !c.cover));
  renderMatches(job);
  renderAlts(job);
  renderTimeline(job);
  renderHighlight(job);
  renderClips(job);
}

// ---- barra de ferramentas do projeto
$$("#job-tools [data-panel]").forEach((btn) => btn.addEventListener("click", () => {
  const panel = $(`#${btn.dataset.panel}`);
  const open = panel.classList.contains("hidden");
  $$(".panel").forEach((p) => p.classList.add("hidden"));
  $$("#job-tools [data-panel]").forEach((b) => b.classList.remove("active"));
  panel.classList.toggle("hidden", !open);
  btn.classList.toggle("active", open);
  if (open && btn.dataset.panel === "create-panel") $("#search-input").focus();
}));

let transcriptCache = { id: null, segments: [] };
const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
$("#search-input").addEventListener("input", async (e) => {
  const q = fold(e.target.value.trim());
  const box = $("#search-results");
  if (q.length < 2) { box.replaceChildren(); return; }
  if (transcriptCache.id !== currentJob.id) {
    transcriptCache = { id: currentJob.id, segments: (await api(`/api/jobs/${currentJob.id}/transcript`)).segments };
  }
  const hits = transcriptCache.segments.filter((s) => fold(s.text).includes(q)).slice(0, 40);
  box.replaceChildren(...hits.map((s) => {
    const b = document.createElement("button");
    const i = fold(s.text).indexOf(q);
    b.innerHTML = `<time>${fmtTime(s.s)}</time><span>${escapeHtml(s.text.slice(0, i))}<mark>${escapeHtml(s.text.slice(i, i + q.length))}</mark>${escapeHtml(s.text.slice(i + q.length))}</span>`;
    b.title = "Criar um clipe começando um pouco antes desta fala";
    b.onclick = async () => {
      b.disabled = true;
      try {
        const clip = await postJSON(`/api/jobs/${currentJob.id}/clips`, { start: s.s - 6, end: s.s + 22 });
        await openJob(currentJob.id);
        openEditor(currentJob.id, clip.n);
      } catch (ex) { alert(ex.message); b.disabled = false; }
    };
    return b;
  }));
  if (!hits.length) box.innerHTML = `<p class="muted small">Nada encontrado.</p>`;
});

$("#apply-btn").onclick = async () => {
  $("#apply-error").textContent = "";
  const body = { ...readFields($("#apply-form"), STYLE_FIELDS), save_default: $("#apply-default").checked };
  if (!confirm(`Aplicar este estilo e renderizar de novo os ${currentJob.clips.length} clipes?`)) return;
  try {
    await postJSON(`/api/jobs/${currentJob.id}/apply`, body);
    if (body.save_default) loadConfig();
    openJob(currentJob.id);
  } catch (ex) {
    $("#apply-error").textContent = ex.message;
  }
};

// ---- momentos que o Claude considerou e não entregou
let altsCache = { key: null, list: [] };
async function renderAlts(job) {
  const card = $("#alts");
  const ready = ["done", "error"].includes(job.status) && job.clips.length;
  if (!ready) { card.classList.add("hidden"); return; }
  const key = `${job.id}:${job.clips.map((c) => `${c.n}-${c.start}-${c.end}`).join(",")}`;
  if (altsCache.key !== key) {
    try { altsCache = { key, list: await api(`/api/jobs/${job.id}/alternativas`) }; } catch { return; }
  }
  const list = altsCache.list;
  card.classList.toggle("hidden", !list.length);
  $("#alts-count").textContent = list.length ? `${list.length} momento${list.length > 1 ? "s" : ""}` : "";
  $("#alt-list").replaceChildren(...list.map((a) => {
    const row = document.createElement("div");
    row.className = "alt";
    row.innerHTML = `<span class="score"></span><div class="alt-main"><b class="alt-title"></b>
      <span class="muted small alt-meta"></span><p class="alt-why"></p></div>
      <button class="secondary small alt-use"><i class="ic ic-plus"></i>Usar este</button>`;
    const sc = $(".score", row);
    sc.textContent = a.score;
    sc.className = `score ${a.score >= 75 ? "" : a.score >= 50 ? "mid" : "low"}`;
    $(".alt-title", row).textContent = a.title || "Momento sem título";
    $(".alt-meta", row).textContent = `${fmtTime(a.start)} → ${fmtTime(a.end)} · ${Math.round(a.end - a.start)} s · ${a.situacao}`;
    $(".alt-why", row).textContent = a.visual || a.reason;
    $(".alt-use", row).onclick = async (e) => {
      e.currentTarget.disabled = true;
      try {
        await postJSON(`/api/jobs/${job.id}/alternativas/${a.id}`, {});
        altsCache.key = null;
        openJob(job.id);
      } catch (ex) { alert(ex.message); e.currentTarget.disabled = false; }
    };
    return row;
  }));
}

// ---- linha do tempo de eventos (placar + reações + cortes)
let tlCache = { key: null, data: null };
async function renderTimeline(job) {
  const card = $("#timeline");
  const ready = ["done", "error", "waiting_match"].includes(job.status) && job.options.mode !== "highlights";
  if (!ready) { card.classList.add("hidden"); return; }
  const key = `${job.id}:${job.clips.map((c) => `${c.n}-${c.start}-${c.end}`).join(",")}`;
  if (tlCache.key !== key) {
    try { tlCache = { key, data: await api(`/api/jobs/${job.id}/timeline`) }; } catch { return; }
  }
  const t = tlCache.data;
  const has = t.eventos.length || t.reacoes.length;
  card.classList.toggle("hidden", !has || !t.duration);
  if (!has || !t.duration) return;
  const PX = 2.2;  // px por segundo: 1 h ≈ 8 mil px, rola na horizontal
  const track = $("#tl-track");
  track.style.width = `${Math.ceil(t.duration * PX)}px`;
  const mk = (cls, x, w, title, data) => {
    const el = document.createElement("button");
    el.className = `tl-m ${cls}`;
    el.style.left = `${x * PX}px`;
    if (w) el.style.width = `${Math.max(3, w * PX)}px`;
    el.title = title;
    if (data) el.onclick = (e) => openTlPop(e, data);
    return el;
  };
  const items = [];
  for (let s = 0; s < t.duration; s += 600) {
    const tick = document.createElement("span");
    tick.className = "tl-tick"; tick.style.left = `${s * PX}px`; tick.textContent = fmtTime(s);
    items.push(tick);
  }
  t.partidas.forEach((s) => items.push(mk("tl-match", s, 0, `Começo de partida ~${fmtTime(s)}`)));
  t.cortes.forEach((c) => items.push(mk("tl-cut", c.start, c.end - c.start, `Corte ${c.n}: ${c.title}`)));
  t.reacoes.forEach((r) => items.push(mk("tl-reacao", r.t, 0, `Reação forte (z=${r.z}) ${fmtTime(r.t)}`,
    { t: r.t, title: "Reação forte", sub: `z=${r.z} · ${fmtTime(r.t)}` })));
  t.eventos.forEach((e) => {
    const nosso = e.tipo === "gol_nosso", deles = e.tipo === "gol_deles";
    const title = nosso ? `Seu gol ${e.placar}` : deles ? `Gol sofrido ${e.placar}` : "Fim do tempo normal";
    items.push(mk(nosso ? "tl-nosso" : deles ? "tl-deles" : "tl-fim", e.t, 0, `${title} · ${fmtTime(e.t)}`,
      { t: e.t, title, sub: `${fmtTime(e.t)}${e.relogio ? ` · relógio ${e.relogio}` : ""}` }));
  });
  track.replaceChildren(...items);
}
let tlPopAt = null;
function openTlPop(e, data) {
  e.stopPropagation();
  tlPopAt = data;
  const pop = $("#tl-pop");
  $("#tl-pop-title").textContent = data.title;
  $("#tl-pop-sub").textContent = data.sub;
  const card = $("#timeline").getBoundingClientRect();
  const r = e.currentTarget.getBoundingClientRect();
  pop.style.left = `${Math.min(card.width - 240, Math.max(8, r.left - card.left - 100))}px`;
  pop.classList.remove("hidden");
}
document.addEventListener("click", (e) => { if (!e.target.closest("#tl-pop")) $("#tl-pop").classList.add("hidden"); });
$("#tl-pop-create").onclick = async (e) => {
  if (!tlPopAt) return;
  e.currentTarget.disabled = true;
  try {
    // 10 s de contexto antes do lance e 8 s para a reação (mesmas bordas do motor de cortes)
    const clip = await postJSON(`/api/jobs/${currentJob.id}/clips`, { start: tlPopAt.t - 10, end: tlPopAt.t + 8 });
    $("#tl-pop").classList.add("hidden");
    await openJob(currentJob.id);
    openEditor(currentJob.id, clip.n);
  } catch (ex) { alert(ex.message); }
  e.currentTarget.disabled = false;
};

// ---- partidas da live
const RESULT = { vitoria: ["Vitória", "accent"], derrota: ["Derrota", "danger"], empate: ["Empate", "warn"],
  desconhecido: ["Sem resultado", ""] };
function renderMatches(job) {
  const panel = $("#partidas");
  const list = job.partidas || [];
  panel.classList.toggle("hidden", !list.length);
  const keep = new Set($$("#match-rows input:checked").map((b) => +b.value));
  $("#match-rows").replaceChildren(...list.map((m) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input type="checkbox" value="${m.n}"></td><td><b>#${m.n}</b></td><td class="t"></td>
      <td><b class="res"></b> <span class="score-txt"></span><div class="what"></div></td><td class="muted small meta"></td>`;
    $("input", tr).checked = keep.has(m.n);
    $("input", tr).onchange = updateMatchCount;
    $(".t", tr).textContent = `${fmtTime(m.inicio)} → ${fmtTime(m.fim)} · ${Math.round((m.fim - m.inicio) / 60)} min`;
    const [resText, resKind] = RESULT[m.resultado] || [m.resultado, ""];
    $(".res", tr).textContent = resText;
    $(".res", tr).className = `res chip ${resKind}`;
    $(".score-txt", tr).textContent = m.placar;
    $(".what", tr).textContent = m.resumo;
    $(".meta", tr).textContent = [m.jogo, m.modo, `destaque ${m.destaque}`].filter(Boolean).join(" · ");
    tr.onclick = (e) => {  // clicar na linha marca/desmarca
      if (e.target.tagName === "INPUT") return;
      const box = $("input", tr);
      box.checked = !box.checked;
      updateMatchCount();
    };
    return tr;
  }));
  updateMatchCount();
}
function updateMatchCount() {
  const n = $$("#match-rows input:checked").length;
  $("#match-go").disabled = !n;
  $("#match-count").textContent = n ? `${n} partida${n > 1 ? "s" : ""} marcada${n > 1 ? "s" : ""} → ${n} vídeo${n > 1 ? "s" : ""}` : "nenhuma partida marcada";
}
$("#match-go").onclick = async (e) => {
  const picked = $$("#match-rows input:checked").map((b) => +b.value);
  e.target.disabled = true;
  const made = [];
  for (const n of picked) {
    try {
      const r = await postJSON(`/api/jobs/${currentJob.id}/partidas/${n}`,
        { basis: $("#match-basis").value, serie: $("#match-serie").value.trim() });
      made.push(`<a href="#/job/${r.id}">partida ${n}</a>`);
    } catch (ex) { made.push(`partida ${n}: ${escapeHtml(ex.message)}`); }
  }
  $$("#match-rows input:checked").forEach((b) => (b.checked = false));
  updateMatchCount();
  $("#match-made").innerHTML = `Projetos criados (rodam um por vez, na fila): ${made.join(" · ")}`;
};
$("#map-btn").onclick = async () => {
  if (!confirm("O Claude vai assistir à live inteira (folhas de frames) para separar as partidas. Continuar?")) return;
  try {
    await postJSON(`/api/jobs/${currentJob.id}/partidas`, {});
    openJob(currentJob.id);
  } catch (ex) { alert(ex.message); }
};

// ---- melhores momentos da partida
const hlScripts = {};
async function renderHighlight(job) {
  const panel = $("#highlight");
  const hl = job.options.mode === "highlights" ? job.highlight : null;
  panel.classList.toggle("hidden", !hl);
  if (!hl) return;
  $("#hl-title").textContent = hl.titulo || "Melhores momentos";
  const busy = BUSY.has(job.status);
  $("#hl-ask-btn").disabled = busy;
  $("#hl-ask-reply").textContent = hl.pedido ? `Último pedido: “${hl.pedido.instrucao}” — ${hl.pedido.resposta}` : "";
  $("#hl-post").textContent = hl.post || "";
  $("#hl-compile").disabled = BUSY.has(job.status);
  for (const el of $$(".hl-out", panel)) {
    const v = hl[el.dataset.key];
    el.classList.toggle("hidden", !v);
    if (!v) continue;
    const src = `/media/${v.file}?v=${v.rev}`;
    const video = $("video", el);
    if (video.dataset.src !== src) { video.dataset.src = src; video.src = src; }
    $(".info", el).textContent = `· ${v.duration.toFixed(0)} s · ${v.lances} lances`;
    const base = (job.name || "partida").replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N} _-]/gu, "").slice(0, 50);
    Object.assign($(".dl", el), { href: src, download: `${base} - ${el.dataset.key}.mp4` });
    Object.assign($(".txt", el), { href: `/media/${v.roteiro}?v=${v.rev}`, download: `${base} - roteiro ${el.dataset.key}.txt` });
    Object.assign($(".srt", el), { href: `/media/${v.srt}?v=${v.rev}`, download: `${base} - roteiro ${el.dataset.key}.srt` });
    const key = `${v.roteiro}?v=${v.rev}`;
    if (!hlScripts[el.dataset.key] || hlScripts[el.dataset.key].key !== key) {
      hlScripts[el.dataset.key] = { key, text: await (await fetch(`/media/${key}`)).text() };
    }
  }
  showScript();
}
function showScript() {
  const box = $("#hl-script");
  if (document.activeElement === box) return;  // não apaga o que você está editando no meio do polling
  const s = hlScripts[$("#hl-script-pick").value];
  box.value = s ? s.text : "";
  box.placeholder = "O roteiro aparece quando os vídeos ficarem prontos.";
}
$("#hl-save-script").onclick = async (e) => {
  const key = $("#hl-script-pick").value;
  const btn = e.currentTarget;
  try {
    await postJSON(`/api/jobs/${currentJob.id}/roteiro/${key}`, { texto: $("#hl-script").value });
    delete hlScripts[key];
    btn.lastChild.textContent = "Salvo!";
    setTimeout(() => (btn.lastChild.textContent = "Salvar roteiro"), 1500);
    openJob(currentJob.id);
  } catch (ex) { alert(ex.message); }
};
$("#hl-script-pick").onchange = showScript;
$("#hl-copy").onclick = async (e) => {
  await navigator.clipboard.writeText($("#hl-script").value);
  e.target.textContent = "Copiado!";
  setTimeout(() => (e.target.textContent = "Copiar"), 1500);
};
// ---- editor da partida: assistir à partida e escolher os trechos do resumo
const me = { data: null, sel: null };
const meVideo = $("#me-video");
const meAbs = () => (me.data ? me.data.start + meVideo.currentTime : 0);  // segundo da live original
const meSeek = (t) => { if (me.data) meVideo.currentTime = Math.max(0, t - me.data.start); };
async function openMatchEditor() {
  const dlg = $("#match-ed");
  dlg.showModal();
  $("#me-loading").classList.remove("hidden");
  $("#me-body").classList.add("hidden");
  try {
    me.data = await api(`/api/jobs/${currentJob.id}/partida`);
  } catch (ex) { $("#me-loading").textContent = ex.message; return; }
  meVideo.src = `/media/${me.data.file}`;
  $("#me-loading").classList.add("hidden");
  $("#me-body").classList.remove("hidden");
  me.sel = null;
  drawMatch();
  selectLance(null);
}
function drawMatch() {
  const { start, end, eventos } = me.data;
  const span = end - start;
  const pct = (t) => `${((t - start) / span) * 100}%`;
  const tl = $("#me-tl");
  const items = [$("#me-head")];
  for (const c of [...currentJob.clips].sort((a, b) => a.start - b.start)) {
    const seg = document.createElement("button");
    seg.className = `me-seg ${c.no_short ? "short" : "only-h"} ${me.sel === c.n ? "sel" : ""}`;
    seg.style.left = pct(c.start);
    seg.style.width = `${((c.end - c.start) / span) * 100}%`;
    seg.title = `${c.title} (${fmtTime(c.start)}–${fmtTime(c.end)})`;
    seg.onclick = (e) => { e.stopPropagation(); selectLance(c.n); meSeek(c.start); };
    items.push(seg);
  }
  for (const e of eventos) {
    const m = document.createElement("span");
    m.className = `me-goal ${e.tipo}`;
    m.style.left = pct(e.t);
    m.title = `${e.tipo === "gol_nosso" ? "Seu gol" : e.tipo === "gol_deles" ? "Gol sofrido" : "Fim do tempo"} ${e.placar || ""}`;
    items.push(m);
  }
  const ns = parseFloat($("#me-new-start").value), ne = parseFloat($("#me-new-end").value);
  if (ns && ne && ne > ns) {
    const mark = document.createElement("span");
    mark.className = "me-mark"; mark.style.left = pct(ns); mark.style.width = `${((ne - ns) / span) * 100}%`;
    items.push(mark);
  }
  tl.replaceChildren(...items);
  tl.onclick = (e) => {
    const r = tl.getBoundingClientRect();
    meSeek(start + ((e.clientX - r.left) / r.width) * span);
  };
}
function selectLance(n) {
  me.sel = n;
  const c = currentJob.clips.find((x) => x.n === n);
  $("#me-sel-empty").classList.toggle("hidden", !!c);
  $("#me-sel-body").classList.toggle("hidden", !c);
  if (c) {
    $("#me-sel-title").textContent = c.title;
    $("#me-sel-start").value = c.start.toFixed(1);
    $("#me-sel-end").value = c.end.toFixed(1);
    $("#me-sel-short").checked = !!c.no_short;
  }
  if (me.data) drawMatch();
}
meVideo.addEventListener("timeupdate", () => {
  if (!me.data) return;
  const t = meAbs();
  $("#me-time").textContent = `${fmtTime(t, true)} da live`;
  $("#me-head").style.left = `${((t - me.data.start) / (me.data.end - me.data.start)) * 100}%`;
  if (me.stopAt && t >= me.stopAt) { meVideo.pause(); me.stopAt = null; }
});
$("#me-play").onclick = () => (meVideo.paused ? meVideo.play() : meVideo.pause());
meVideo.addEventListener("play", () => ($("#me-play").textContent = "Pausar"));
meVideo.addEventListener("pause", () => ($("#me-play").textContent = "Tocar"));
$("#me-back").onclick = () => meSeek(meAbs() - 5);
$("#me-fwd").onclick = () => meSeek(meAbs() + 5);
$("#me-sel-in").onclick = () => ($("#me-sel-start").value = meAbs().toFixed(1));
$("#me-sel-out").onclick = () => ($("#me-sel-end").value = meAbs().toFixed(1));
$("#me-sel-play").onclick = () => { meSeek(+$("#me-sel-start").value); me.stopAt = +$("#me-sel-end").value; meVideo.play(); };
const meErr = (msg) => ($("#me-error").textContent = msg || "");
async function meRefresh() {
  currentJob = await api(`/api/jobs/${currentJob.id}`);
  drawMatch();
  renderJob(currentJob);
}
$("#me-sel-short").onchange = async (e) => {
  try { await postJSON(`/api/jobs/${currentJob.id}/clips/${me.sel}/lance`, { no_short: e.target.checked }); meErr(); await meRefresh(); }
  catch (ex) { meErr(ex.message); }
};
$("#me-sel-save").onclick = async () => {
  const s = +$("#me-sel-start").value, e = +$("#me-sel-end").value;
  if (!(e - s >= 1)) return meErr("o trecho precisa ter pelo menos 1 segundo");
  try { await postJSON(`/api/jobs/${currentJob.id}/clips/${me.sel}`, { start: s, end: e }); meErr(); await meRefresh(); }
  catch (ex) { meErr(ex.message); }
};
$("#me-sel-del").onclick = async () => {
  const c = currentJob.clips.find((x) => x.n === me.sel);
  if (!c || !confirm(`Remover o lance "${c.title}" do resumo?`)) return;
  try { await api(`/api/jobs/${currentJob.id}/clips/${me.sel}`, { method: "DELETE" }); meErr(); selectLance(null); await meRefresh(); }
  catch (ex) { meErr(ex.message); }
};
$("#me-new-add").onclick = async () => {
  const s = +$("#me-new-start").value, e = +$("#me-new-end").value;
  if (!(e - s >= 2)) return meErr("marque o início (I) e o fim (O) do trecho, com pelo menos 2 s");
  try {
    const c = await postJSON(`/api/jobs/${currentJob.id}/clips`, { start: s, end: e, title: $("#me-new-title").value.trim() });
    $("#me-new-start").value = $("#me-new-end").value = $("#me-new-title").value = "";
    meErr(); await meRefresh(); selectLance(c.n);
  } catch (ex) { meErr(ex.message); }
};
document.addEventListener("keydown", (e) => {
  if (!$("#match-ed").open || e.target.closest?.("input, textarea")) return;
  const k = e.key.toLowerCase();
  if (k === "i") { $("#me-new-start").value = meAbs().toFixed(1); drawMatch(); }
  else if (k === "o") { $("#me-new-end").value = meAbs().toFixed(1); drawMatch(); }
  else if (e.key === " ") { e.preventDefault(); $("#me-play").click(); }
  else if (e.key === "ArrowLeft") meSeek(meAbs() - (e.shiftKey ? 1 : 5));
  else if (e.key === "ArrowRight") meSeek(meAbs() + (e.shiftKey ? 1 : 5));
});
const closeMatchEditor = () => { meVideo.pause(); $("#match-ed").close(); };
$("#me-close").onclick = closeMatchEditor;
$("#me-done").onclick = closeMatchEditor;
$("#me-compile").onclick = async () => {
  try { await postJSON(`/api/jobs/${currentJob.id}/compile`, {}); closeMatchEditor(); openJob(currentJob.id); }
  catch (ex) { meErr(ex.message); }
};
$("#hl-edit-btn").onclick = openMatchEditor;

$("#hl-ask-btn").onclick = async () => {
  const instrucao = $("#hl-ask-text").value.trim();
  if (!instrucao) return $("#hl-ask-text").focus();
  try {
    await postJSON(`/api/jobs/${currentJob.id}/resumo/pedido`, { instrucao });
    $("#hl-ask-text").value = "";
    openJob(currentJob.id);
  } catch (ex) { alert(ex.message); }
};
$("#hl-ask-text").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#hl-ask-btn").click(); });

$("#hl-compile").onclick = async () => {
  try {
    await postJSON(`/api/jobs/${currentJob.id}/compile`, {});
    openJob(currentJob.id);
  } catch (ex) { alert(ex.message); }
};

function renderClips(job) {
  const wrap = $("#clips");
  const existing = new Map($$(".clip", wrap).map((el) => [el.dataset.key, el]));
  const keep = new Set();
  job.clips.forEach((c) => {
    const key = String(c.n);
    keep.add(key);
    let el = existing.get(key);
    if (!el) {
      el = $("#clip-tpl").content.firstElementChild.cloneNode(true);
      el.dataset.key = key;
      bindClip(el, job.id, c.n);
      wrap.appendChild(el);
    }
    fillClip(el, c);
  });
  existing.forEach((el, key) => { if (!keep.has(key)) el.remove(); });
}

function fillClip(el, c) {
  const done = c.status === "done";
  const video = $("video", el);
  const src = done ? `/media/${c.file}?v=${c.rev}` : "";
  if (done && video.dataset.src !== src) {
    video.dataset.src = src;
    video.src = src;
    video.poster = `/media/${c.thumb}?v=${c.rev}`;
  }
  $(".rendering", el).classList.toggle("hidden", done || c.status === "error");
  $(".rendering", el).textContent = c.status === "pending" ? "Na fila…" : "Renderizando…";
  // sinais que sustentam o corte: âncora lida no placar/volume e o teste sem contexto
  const sig = [];
  const a = c.anchor;
  if (a && a.kind === "gol_nosso") sig.push(`<span class="chip accent">Gol ${escapeHtml(a.placar || "")}</span>`);
  else if (a && a.kind === "gol_deles") sig.push(`<span class="chip danger">Gol sofrido ${escapeHtml(a.placar || "")}</span>`);
  else if (a && a.kind === "reacao") sig.push(`<span class="chip info">Reação forte</span>`);
  if (c.estranho) sig.push(c.estranho.entende
    ? `<span class="chip">Entende sem contexto</span>`
    : c.contexto_tela
      ? `<span class="chip info" title="Sem o texto, quem chega não entenderia: ${escapeHtml(c.estranho.motivo || "")}">Contexto na tela: ${escapeHtml(c.contexto_tela)}</span>`
      : `<span class="chip warn" title="${escapeHtml(c.estranho.motivo || "")}">Falta contexto</span>`);
  $(".signals", el).innerHTML = sig.join("");
  const fb = c.feedback?.veredito || "";
  el.classList.toggle("is-ok", fb === "ok");
  el.classList.toggle("is-rejected", fb === "rejeitado");
  $(".approve", el).classList.toggle("on", fb === "ok");
  $(".reject", el).classList.toggle("on", fb === "rejeitado");
  $(".reject", el).title = fb === "rejeitado" ? `Rejeitado: ${c.feedback.motivo || ""} (R para mudar)` : "Rejeitar com motivo (R)";
  const score = $(".score", el);
  score.textContent = c.score;
  score.className = `score ${c.score >= 75 ? "" : c.score >= 50 ? "mid" : "low"}`;
  $(".dur", el).textContent = `${fmtTime(c.start)} → ${fmtTime(c.end)} · ${(c.out_duration ?? c.end - c.start).toFixed(0)}s`;
  const extras = [LAYOUTS[c.layout] || c.layout];
  if (c.zooms_applied) extras.push(`${c.zooms_applied} zoom${c.zooms_applied > 1 ? "s" : ""}`);
  if (c.removed > 0.2) extras.push(`−${c.removed.toFixed(1)}s de pausas`);
  if (c.manual) extras.push("manual");
  if (c.pos_susto_at != null) extras.push(`pós-susto em ${c.pos_susto_at.toFixed(0)}s`);
  $(".layout", el).textContent = c.layout ? `· ${extras.filter(Boolean).join(" · ")}` : "";
  $(".breakdown", el).innerHTML = Object.entries(c.scores || {})
    .map(([k, v]) => `<span>${SCORE_NAMES[k] || k} <b>${v}</b></span>`).join("");
  $(".title", el).textContent = c.title;
  $(".visual", el).textContent = c.visual || "";
  $(".visual", el).classList.toggle("hidden", !c.visual);
  $(".hook", el).textContent = c.hook ? `“${c.hook}”` : "";
  $(".reason", el).textContent = c.reason;
  $(".tags", el).textContent = c.post ? "" : (c.hashtags || []).join(" ");
  $(".post", el).textContent = c.post || "";
  const srt = $(".srt", el);
  srt.classList.toggle("hidden", !done);
  srt.href = `/media/${c.file?.replace(/clip\.mp4$/, "clip.srt")}?v=${c.rev}`;
  srt.download = `${String(c.n).padStart(2, "0")}.srt`;
  const dl = $(".dl", el);
  dl.classList.toggle("hidden", !done);
  dl.href = src;
  dl.download = `${String(c.n).padStart(2, "0")}-${c.title.replace(/[^\p{L}\p{N} _-]/gu, "").slice(0, 60)}.mp4`;
  const frames = $(".frames", el);
  frames.classList.toggle("hidden", !c.review_image);
  if (c.review_image) frames.href = `/media/${c.review_image}`;
  fillCover(el, c, done);
  $(".edit", el).disabled = !(done || c.status === "error");
  $(".posedit", el).disabled = !done;
  $(".clip-error", el).textContent = c.error || "";
  const isLance = currentJob?.options?.mode === "highlights";
  $(".lance-row", el).classList.toggle("hidden", !isLance);
  if (isLance) $(".lance-short", el).checked = !!c.no_short;
  const redo = $(".clip-redo", el);
  redo.classList.toggle("hidden", !c.redo);
  if (c.redo) redo.textContent = `Pedido: “${c.redo.instrucao}” — ${c.redo.resposta || ""}`;
  $(".redo", el).disabled = !(done || c.status === "error");
  el._clip = c;
}

function fillCover(el, c, done) {
  const row = $(".cover-row", el);
  row.classList.toggle("hidden", !done);
  if (!done) return;
  const has = !!c.cover_file;
  const src = has ? `/media/${c.cover_file}?v=${c.cover_rev || 0}` : "";
  const img = $(".cover-thumb img", el);
  $(".cover-thumb", el).classList.toggle("empty", !has);
  img.classList.toggle("hidden", !has);
  if (has && img.dataset.src !== src) { img.dataset.src = src; img.src = src; }
  const dl = $(".cover-dl", el);
  dl.classList.toggle("hidden", !has);
  dl.href = src;
  dl.download = `${String(c.n).padStart(2, "0")}-capa.jpg`;
  $(".cover-edit span", el).textContent = has ? "Editar capa" : "Fazer capa";
  $(".cover-note", el).textContent = c.cover_error ? `Erro: ${c.cover_error}`
    : !has ? "Este clipe ainda não tem capa."
    : c.cover?.in_video ? "1080×1920 · também no fim do vídeo"
    : "1080×1920 · suba no “Editar capa” do TikTok";
}

document.addEventListener("click", (e) => {
  $$("details.menu[open]").forEach((m) => {
    if (!m.contains(e.target) || e.target.closest(".menu-list > button, .menu-list > a")) m.removeAttribute("open");
  });
});

async function sendFeedback(el, jobId, n, veredito, motivo = "") {
  const cur = el._clip?.feedback?.veredito || "";
  if (veredito === cur && veredito === "ok") veredito = "";  // clicar de novo em aprovar desfaz
  try {
    const r = await postJSON(`/api/jobs/${jobId}/clips/${n}/feedback`, { veredito, motivo });
    el._clip.feedback = veredito ? r.feedback : null;
    fillClip(el, el._clip);
  } catch (ex) { alert(ex.message); }
}

// ---- atalhos de revisão: J/K (ou ←/→) navegam, A aprova, R rejeita (1–5 escolhe o motivo), espaço toca, E edita
let focusIdx = -1;
function focusClip(i) {
  const list = $$("#clips .clip");
  if (!list.length) return;
  focusIdx = Math.max(0, Math.min(list.length - 1, i));
  list.forEach((c, k) => c.classList.toggle("focused", k === focusIdx));
  list[focusIdx].scrollIntoView({ block: "center", behavior: "smooth" });
}
document.addEventListener("keydown", (e) => {
  if (!currentJob || $("#job").classList.contains("hidden") || e.ctrlKey || e.metaKey || e.altKey) return;
  const tgt = e.target instanceof Element ? e.target : null;
  if (tgt?.closest("input, textarea, select, [contenteditable]") || $("dialog[open]")) return;
  const list = $$("#clips .clip");
  const el = list[focusIdx];
  const k = e.key.toLowerCase();
  const openReject = el && $(".reject-menu", el).open;
  if (openReject && /^[1-5]$/.test(k)) {
    e.preventDefault();
    $$(".reject-menu .menu-list button", el)[+k - 1].click();
    return;
  }
  if (k === "j" || e.key === "ArrowRight") { e.preventDefault(); focusClip(focusIdx + 1); }
  else if (k === "k" || e.key === "ArrowLeft") { e.preventDefault(); focusClip(focusIdx - 1); }
  else if (!el) return;
  else if (k === "a") { e.preventDefault(); $(".approve", el).click(); }
  else if (k === "r") { e.preventDefault(); $(".reject-menu", el).open = !openReject; }
  else if (k === "e") { e.preventDefault(); $(".edit", el).click(); }
  else if (e.key === " ") { e.preventDefault(); const v = $("video", el); v.paused ? v.play() : v.pause(); }
  else if (e.key === "Escape" && openReject) { $(".reject-menu", el).open = false; }
});

function bindClip(el, jobId, n) {
  $(".approve", el).onclick = () => sendFeedback(el, jobId, n, "ok");
  $(".lance-short", el).onchange = async (e) => {
    try {
      await postJSON(`/api/jobs/${jobId}/clips/${n}/lance`, { no_short: e.target.checked });
      el._clip.no_short = e.target.checked;
    } catch (ex) { alert(ex.message); e.target.checked = !e.target.checked; }
  };
  $(".redo", el).onclick = async () => {
    const instrucao = prompt("O que mudar neste corte?\nEx.: começa 3 s antes · termina na risada · abre direto no gol · tira o começo parado");
    if (!instrucao || !instrucao.trim()) return;
    try {
      await postJSON(`/api/jobs/${jobId}/clips/${n}/redo`, { instrucao });
      openJob(jobId);
    } catch (ex) { alert(ex.message); }
  };
  $$(".reject-menu .menu-list button", el).forEach((b) => {
    b.onclick = () => sendFeedback(el, jobId, n, "rejeitado", b.dataset.motivo);
  });
  el.addEventListener("click", () => {
    const list = $$("#clips .clip");
    focusIdx = list.indexOf(el);
    list.forEach((c) => c.classList.toggle("focused", c === el));
  });
  $(".edit", el).onclick = () => openEditor(jobId, n);
  $(".cover-edit", el).onclick = () => openCover(jobId, n);
  $(".cover-thumb", el).onclick = () => openCover(jobId, n);
  $(".copy", el).onclick = async (e) => {
    const c = el._clip;
    await navigator.clipboard.writeText(c.post || `${c.title}\n\n${(c.hashtags || []).join(" ")}`);
    const label = $("span", e.currentTarget);
    label.textContent = "Copiado!";
    setTimeout(() => (label.textContent = "Copiar post"), 1500);
  };
  $(".studio", el).onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    $("span", btn).textContent = "Abrindo…";
    try {
      const p = await postJSON("/api/studio/projects", {
        name: el._clip.title, ratio: "9:16", from_clip: { job: jobId, n },
      });
      location.href = `studio.html#/p/${p.id}`;
    } catch (ex) {
      alert(ex.message);
      btn.disabled = false;
      $("span", btn).textContent = "Abrir no Estúdio";
    }
  };
  $(".posedit", el).onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const p = await postJSON("/api/pos", { job: jobId, n });
      location.href = `pos.html#/p/${p.id}`;
    } catch (ex) {
      alert(ex.message);
      btn.disabled = false;
    }
  };
  $(".del", el).onclick = async () => {
    if (!confirm(`Excluir o clipe "${el._clip.title}"?`)) return;
    try {
      await api(`/api/jobs/${jobId}/clips/${n}`, { method: "DELETE" });
      el.remove();
      openJob(jobId);
    } catch (ex) { alert(ex.message); }
  };
}

$("#copy-prompt").onclick = async (e) => {
  const text = await api(`/api/jobs/${currentJob.id}/prompt`);
  await navigator.clipboard.writeText(text);
  e.target.textContent = "Copiado!";
  setTimeout(() => (e.target.textContent = "Copiar prompt"), 1500);
};
$("#send-manual").onclick = async () => {
  $("#manual-error").textContent = "";
  try {
    await api(`/api/jobs/${currentJob.id}/manual`, { method: "POST", body: $("#manual-answer").value });
    $("#manual-answer").value = "";
    openJob(currentJob.id);
  } catch (ex) {
    $("#manual-error").textContent = ex.message;
  }
};
$("#reselect-btn").onclick = async () => {
  try {
    await postJSON(`/api/jobs/${currentJob.id}/reselect`, { instructions: $("#reselect-instructions").value });
    $("#clips").replaceChildren();
    openJob(currentJob.id);
  } catch (ex) {
    alert(ex.message);
  }
};
$("#retitle-btn").onclick = async () => {
  const n = currentJob.clips.length;
  if (!confirm(`Gerar títulos e posts novos para os ${n} clipes? O título atual continua como sugestão no editor. ` +
    "Clipes com o título gravado no vídeo são renderizados de novo.")) return;
  try {
    await postJSON(`/api/jobs/${currentJob.id}/retitle`, {});
    openJob(currentJob.id);
  } catch (ex) {
    alert(ex.message);
  }
};
$("#covers-btn").onclick = async () => {
  try {
    await postJSON(`/api/jobs/${currentJob.id}/capas`, {});
    openJob(currentJob.id);
  } catch (ex) { alert(ex.message); }
};
$("#delete-job").onclick = async () => {
  if (!confirm("Excluir este projeto e os clipes gerados?")) return;
  await api(`/api/jobs/${currentJob.id}`, { method: "DELETE" });
  location.hash = "";
};

// ================================================================ editor
const dlg = $("#editor");
const vid = $("#ed-video");
const canvas = $("#ed-canvas");
let ed = null;

async function openEditor(jobId, n) {
  ed = null;
  $("#ed-error").textContent = "";
  $("#ed-loading").classList.remove("hidden");
  $("#ed-body").classList.add("hidden");
  $("#ed-save").disabled = true;
  dlg.showModal();
  let data;
  try {
    data = await api(`/api/jobs/${jobId}/clips/${n}/editor`);
  } catch (ex) {
    $("#ed-loading").textContent = `Não deu para abrir o editor: ${ex.message}`;
    return;
  }
  const [ws, we] = data.window;
  const o = data.options;
  ed = {
    jobId, n, data, ws, we,
    sel: { s: data.clip.start, e: data.clip.end },
    fixes: { ...(o.word_fixes || {}) },
    adds: (o.word_adds || []).map((a) => ({ w: a.w, s: +a.s, e: +a.e })),
    playingSel: false, drag: null,
  };
  const { width: sw, height: sh } = data.source;
  const stage = $("#ed-stage");
  stage.style.aspectRatio = `${sw} / ${sh}`;
  stage.style.width = sw / sh < 1 ? `min(100%, calc(52vh * ${sw / sh}))` : "100%";
  vid.src = `/media/${data.proxy}`;
  vid.onloadedmetadata = () => { vid.currentTime = ed.sel.s - ws; };

  $("#ed-title").value = data.clip.title;
  $("#ed-title-options").innerHTML = (data.clip.title_options || [])
    .filter((o) => !(o.problems || []).length)
    .sort((a, b) => b.score - a.score)
    .map((o) => `<option value="${escapeHtml(o.title)}">${o.formula} · nota ${o.score} · ${o.title.length} car.</option>`)
    .join("");
  $("#ed-layout").value = o.layout || "auto";
  $("#ed-cropx").value = o.crop_x ?? 0.5;
  $("#ed-captions").checked = o.captions !== false;
  $("#ed-preset").value = o.caption_preset || "destaque";
  $("#ed-position").value = o.caption_position || "bottom";
  $("#ed-color").value = o.caption_color || "#FFE600";
  $("#ed-upper").checked = o.caption_upper !== false;
  $("#ed-title-on").checked = !!o.title_overlay;
  $("#ed-title-mode").value = o.title_mode || "always";
  $("#ed-title-pos").value = o.title_position || "top";
  $("#ed-title-style").value = o.title_style || "impacto";
  $("#ed-zoom").checked = o.auto_zoom !== false;
  $("#ed-pauses").checked = !!o.remove_pauses;
  $("#ed-pos").value = o.pos_susto || "";
  $("#ed-pos-music").checked = !!o.pos_susto_musica;
  const c0 = data.clip;
  $("#ed-pos-info").textContent = c0.pos_susto_at != null
    ? `Começa em ${c0.pos_susto_at.toFixed(1)} s do clipe (no susto marcado)` + (c0.pos_susto_music ? ` · música: ${c0.pos_susto_music}` : "")
    : o.pos_susto ? "Não entrou neste clipe: o susto marcado cai cedo demais ou sobra menos de 1,5 s (ou mais de 20 s) depois dele."
    : "Do susto até o fim, a imagem congela no grito e/ou fica preto e branco. Para a música, ponha arquivos que você tem direito de usar na pasta musicas/pos_susto da biblioteca.";
  $("#ed-keywords").checked = o.keyword_highlight !== false;
  $("#ed-kwcolor").value = o.keyword_color || "#39E75F";
  const kws = data.clip.keywords || [];
  $("#ed-kwlist").textContent = kws.length ? `Palavras-chave do Claude: ${kws.join(", ")}` : "Sem palavras-chave para este clipe.";
  const vertical = sw / sh < 1;
  $("#ed-layout").disabled = vertical;
  $("#ed-vertical-note").classList.toggle("hidden", !vertical);

  addEdit = null;
  $("#ed-add").classList.add("hidden");
  $("#ed-loading").classList.add("hidden");
  $("#ed-body").classList.remove("hidden");
  $("#ed-save").disabled = false;
  renderWords();
  updateLayoutUI();
  updateCaptionStyle();
  requestAnimationFrame(drawTimeline);
}

function closeEditor() {
  vid.pause();
  vid.removeAttribute("src");
  vid.load();
  dlg.close();
  ed = null;
}
$("#ed-close").onclick = closeEditor;
$("#ed-cancel").onclick = closeEditor;
dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeEditor(); });

const nowT = () => (ed ? ed.ws + vid.currentTime : 0);
function seek(t) {
  if (!ed) return;
  vid.currentTime = Math.min(Math.max(t, ed.ws), ed.we) - ed.ws;
  tick();
}

$("#ed-play").onclick = () => {
  if (!ed) return;
  if (!vid.paused && ed.playingSel) { vid.pause(); return; }
  ed.playingSel = true;
  vid.currentTime = ed.sel.s - ed.ws;
  vid.play();
};
$("#ed-set-in").onclick = () => setIn(nowT());
$("#ed-set-out").onclick = () => setOut(nowT());
function setIn(t) { if (ed && t < ed.sel.e - 1) { ed.sel.s = t; tick(); } }
function setOut(t) { if (ed && t > ed.sel.s + 1) { ed.sel.e = t; tick(); } }

vid.addEventListener("play", () => ($("#ed-play").textContent = "⏸ Pausar"));
vid.addEventListener("pause", () => { $("#ed-play").textContent = "▶ Tocar corte"; if (ed) ed.playingSel = false; });
vid.addEventListener("timeupdate", tick);
vid.addEventListener("seeked", tick);
function loop() { if (ed && !vid.paused) { tick(); requestAnimationFrame(loop); } }
vid.addEventListener("play", () => requestAnimationFrame(loop));

function tick() {
  if (!ed) return;
  const t = nowT();
  if (ed.playingSel && t >= ed.sel.e) { vid.pause(); vid.currentTime = ed.sel.e - ed.ws; }
  const len = ed.sel.e - ed.sel.s;
  $("#ed-time").textContent = `${fmtTime(t, true)}`;
  $("#ed-sel-label").textContent = `Corte: ${fmtTime(ed.sel.s, true)} → ${fmtTime(ed.sel.e, true)} (${len.toFixed(1)}s)`;
  updateWordClasses(t);
  updateCaptionPreview(t);
  drawTimeline();
}

document.addEventListener("keydown", (e) => {
  if (!ed || !dlg.open || e.target.matches("input, textarea, select")) return;
  if (e.code === "Space") { e.preventDefault(); vid.paused ? vid.play() : vid.pause(); }
  else if (e.key === "i" || e.key === "I") setIn(nowT());
  else if (e.key === "o" || e.key === "O") setOut(nowT());
  else if (e.key === "ArrowLeft") seek(nowT() - (e.shiftKey ? 1 : 0.1));
  else if (e.key === "ArrowRight") seek(nowT() + (e.shiftKey ? 1 : 0.1));
});

// ---- timeline
function drawTimeline() {
  if (!ed) return;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (canvas.width !== Math.round(W * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const { ws, we } = ed;
  const x = (t) => ((t - ws) / (we - ws)) * W;
  const loud = ed.data.loudness, hop = ed.data.hop;
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim() || "#b9f227";
  // volume
  ctx.fillStyle = "#3a3a4d";
  const bw = Math.max(1, (hop / (we - ws)) * W);
  loud.forEach((db, i) => {
    const t = Math.floor(ws / hop) * hop + i * hop;
    const v = Math.min(1, Math.max(0, (db + 55) / 50));
    const h = v * (H - 18);
    ctx.fillRect(x(t), H - 4 - h, bw, h);
  });
  // palavras (marquinhas)
  ctx.fillStyle = "#6b6b85";
  ed.data.words.forEach((w) => ctx.fillRect(x(w.s), 2, Math.max(1, x(w.e) - x(w.s)), 4));
  // seleção
  const xs = x(ed.sel.s), xe = x(ed.sel.e);
  ctx.fillStyle = "rgba(185, 242, 39, .16)";
  ctx.fillRect(xs, 0, xe - xs, H);
  ctx.fillStyle = accent;
  ctx.fillRect(xs - 2, 0, 4, H);
  ctx.fillRect(xe - 2, 0, 4, H);
  ctx.fillRect(xs - 6, H / 2 - 10, 8, 20);
  ctx.fillRect(xe - 2, H / 2 - 10, 8, 20);
  // legendas adicionadas à mão (arraste para mover)
  ctx.font = "700 10px system-ui, sans-serif";
  ed.adds.forEach((a) => {
    const x0 = x(a.s), bw = Math.max(6, x(a.e) - x(a.s));
    const editing = addEdit && addEdit.a === a;
    ctx.fillStyle = editing ? accent : "rgba(185, 242, 39, .6)";
    ctx.fillRect(x0, ADD_BAR.y, bw, ADD_BAR.h);
    ctx.save();
    ctx.beginPath();
    ctx.rect(x0, ADD_BAR.y, bw, ADD_BAR.h);
    ctx.clip();
    ctx.fillStyle = "#111";
    ctx.fillText(a.w, x0 + 3, ADD_BAR.y + 12);
    ctx.restore();
  });
  // cursor
  ctx.fillStyle = "#fff";
  ctx.fillRect(x(nowT()) - 1, 0, 2, H);
}

canvas.addEventListener("pointerdown", (e) => {
  if (!ed) return;
  const rect = canvas.getBoundingClientRect();
  const W = rect.width;
  const px = e.clientX - rect.left;
  const toT = (p) => ed.ws + (p / W) * (ed.we - ed.ws);
  const xs = ((ed.sel.s - ed.ws) / (ed.we - ed.ws)) * W;
  const xe = ((ed.sel.e - ed.ws) / (ed.we - ed.ws)) * W;
  const bar = addBarAt(px, e.clientY - rect.top, W);
  if (bar) { ed.drag = { kind: "add", a: bar, off: toT(px) - bar.s }; openAddPanel(bar); }
  else if (Math.abs(px - xs) < 10) ed.drag = { kind: "s" };
  else if (Math.abs(px - xe) < 10) ed.drag = { kind: "e" };
  else if (px > xs && px < xe && e.shiftKey) ed.drag = { kind: "move", off: toT(px) - ed.sel.s };
  else { seek(toT(px)); ed.drag = { kind: "seek" }; }
  canvas.setPointerCapture(e.pointerId);
  ed.drag.toT = toT;
});
canvas.addEventListener("pointermove", (e) => {
  if (!ed) return;
  const rect = canvas.getBoundingClientRect();
  const px = e.clientX - rect.left;
  const xs = ((ed.sel.s - ed.ws) / (ed.we - ed.ws)) * rect.width;
  const xe = ((ed.sel.e - ed.ws) / (ed.we - ed.ws)) * rect.width;
  const overBar = addBarAt(px, e.clientY - rect.top, rect.width);
  canvas.style.cursor = ed.drag?.kind === "add" ? "grabbing" : overBar ? "grab"
    : Math.abs(px - xs) < 10 || Math.abs(px - xe) < 10 ? "ew-resize" : "pointer";
  if (!ed.drag) return;
  const t = Math.min(Math.max(ed.drag.toT(px), ed.ws), ed.we);
  if (ed.drag.kind === "add") {
    const a = ed.drag.a, len = a.e - a.s;
    const s = Math.min(Math.max(t - ed.drag.off, ed.ws), ed.we - len);
    a.s = +s.toFixed(3);
    a.e = +(s + len).toFixed(3);
    if (addEdit && addEdit.a === a) $("#ed-add-start").value = Math.max(0, a.s - ed.sel.s).toFixed(1);
    seek(a.s);
    return;
  }
  if (ed.drag.kind === "s") { if (t < ed.sel.e - 1) { ed.sel.s = t; seek(t); } }
  else if (ed.drag.kind === "e") { if (t > ed.sel.s + 1) { ed.sel.e = t; seek(t); } }
  else if (ed.drag.kind === "move") {
    const len = ed.sel.e - ed.sel.s;
    const s = Math.min(Math.max(t - ed.drag.off, ed.ws), ed.we - len);
    ed.sel = { s, e: s + len };
    tick();
  } else seek(t);
});
canvas.addEventListener("pointerup", () => {
  if (!ed) return;
  const moved = ed.drag?.kind === "add";
  ed.drag = null;
  if (moved) refreshAdds();
});
window.addEventListener("resize", () => drawTimeline());

// ---- transcrição (palavras do Whisper + legendas adicionadas à mão)
function wordText(w) {
  const k = w.s.toFixed(2);
  return k in ed.fixes ? ed.fixes[k] : w.w;
}
function transcriptItems() {
  const items = ed.data.words.map((w) => ({ s: w.s, e: w.e, word: w }));
  ed.adds.forEach((a) => items.push({ s: a.s, e: a.e, add: a }));
  return items.sort((x, y) => x.s - y.s);
}
function renderWords() {
  const box = $("#ed-words");
  ed.items = transcriptItems();
  box.replaceChildren(...ed.items.map((it) => {
    const span = document.createElement("span");
    span.onclick = () => { seek(it.s); if (it.add) openAddPanel(it.add); };
    if (!it.add) span.ondblclick = () => editWord(span, it.word);
    if (it.add) fillAdded(span, it.add);
    else fillWord(span, it.word);
    it.span = span;
    return span;
  }));
  lastNow = null;
  updateWordClasses(nowT());
  drawTimeline();
}
function fillWord(span, w) {
  const k = w.s.toFixed(2);
  const fixed = k in ed.fixes;
  span.textContent = fixed && !ed.fixes[k] ? w.w : wordText(w);
  span.classList.toggle("fixed", fixed && !!ed.fixes[k]);
  span.classList.toggle("removed", fixed && !ed.fixes[k]);
  const covered = coveredByAdd(w);
  span.classList.toggle("covered", covered);
  span.title = covered ? "fora da legenda: no lugar dela entra a legenda adicionada" : fixed ? `original: ${w.w}` : "";
}
function fillAdded(span, a) {
  span.textContent = a.w;
  span.classList.add("added");
  span.classList.toggle("editing", !!addEdit && addEdit.a === a);
  span.title = `legenda adicionada · ${(a.s - ed.sel.s).toFixed(1)}s do corte, ${(a.e - a.s).toFixed(1)}s · clique para editar, mover ou excluir`;
}
function inlineInput(span, value, onDone) {
  const input = document.createElement("input");
  input.value = value;
  span.replaceChildren(input);
  input.focus();
  input.select();
  let closed = false;
  const done = (save) => {
    if (closed) return;
    closed = true;
    onDone(save, input.value.trim());
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); done(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
  };
  input.onblur = () => done(true);
}
function editWord(span, w) {
  inlineInput(span, wordText(w), (save, v) => {
    if (save) {
      const k = w.s.toFixed(2);
      if (v === w.w) delete ed.fixes[k];
      else ed.fixes[k] = v;
    }
    fillWord(span, w);
    updateWordClasses(nowT());
    updateCaptionPreview(nowT());
  });
}
let lastNow = null;
function updateWordClasses(t) {
  if (!ed || !ed.items) return;
  let now = null;
  ed.items.forEach((it) => {
    const s = it.span;
    if (!s || s.firstElementChild) return;
    s.classList.toggle("in", it.e > ed.sel.s && it.s < ed.sel.e);
    if (t >= it.s && t <= it.e + 0.05) now = it;
  });
  if (now !== lastNow) {
    if (lastNow && lastNow.span) lastNow.span.classList.remove("now");
    if (now && now.span) {
      now.span.classList.add("now");
      if (!vid.paused) now.span.scrollIntoView({ block: "nearest" });
    }
    lastNow = now;
  }
}

// ---- legendas adicionadas: criar, editar, mover e excluir
let addEdit = null; // { a, orig } enquanto edita uma legenda que já existe
const relNow = () => Math.max(0, nowT() - ed.sel.s).toFixed(1);
function openAddPanel(a = null) {
  if (!ed) return;
  if (addEdit && addEdit.a === a) return;
  if (addEdit) closeAddPanel(false);
  addEdit = a ? { a, orig: { ...a } } : null;
  $("#ed-add").classList.remove("hidden");
  $("#ed-add-title").textContent = a ? "Editar legenda adicionada" : "Nova legenda";
  $("#ed-add-ok").textContent = a ? "Pronto" : "Adicionar";
  $("#ed-add-del").classList.toggle("hidden", !a);
  $("#ed-add-text").value = a ? a.w : "";
  $("#ed-add-start").value = a ? Math.max(0, a.s - ed.sel.s).toFixed(1) : relNow();
  if (a) $("#ed-add-dur").value = (a.e - a.s).toFixed(1);
  refreshAdds();
  if (!a) $("#ed-add-text").focus();
}
function closeAddPanel(revert) {
  if (revert && addEdit) Object.assign(addEdit.a, addEdit.orig);
  addEdit = null;
  $("#ed-add").classList.add("hidden");
  refreshAdds();
}
function refreshAdds() {
  if (!ed) return;
  renderWords();
  updateCaptionPreview(nowT());
}
function readPanel() {  // formulário (segundos do corte) -> segundos do vídeo original
  const rel = Math.max(0, +$("#ed-add-start").value || 0);
  const dur = Math.max(0.2, +$("#ed-add-dur").value || 1.5);
  const s = Math.min(ed.sel.s + rel, ed.sel.e - 0.2);
  return { w: $("#ed-add-text").value.trim(), s: +s.toFixed(3), e: +Math.min(s + dur, ed.we).toFixed(3) };
}
function livePanel() {  // editando uma que já existe: cada mudança aparece na hora
  if (!ed || !addEdit) return;
  const v = readPanel();
  addEdit.a.s = v.s;
  addEdit.a.e = v.e;
  if (v.w) addEdit.a.w = v.w;
  refreshAdds();
}
["ed-add-text", "ed-add-start", "ed-add-dur"].forEach((id) => $(`#${id}`).addEventListener("input", livePanel));
function nudge(d) {
  if (!ed) return;
  $("#ed-add-start").value = Math.max(0, (+$("#ed-add-start").value || 0) + d).toFixed(1);
  livePanel();
  if (addEdit) seek(addEdit.a.s);
}
$("#ed-add-open").onclick = () => openAddPanel(null);
$("#ed-add-back").onclick = () => nudge(-0.1);
$("#ed-add-fwd").onclick = () => nudge(0.1);
$("#ed-add-now").onclick = () => { if (ed) { $("#ed-add-start").value = relNow(); livePanel(); } };
$("#ed-add-ok").onclick = confirmAdd;
$("#ed-add-cancel").onclick = () => closeAddPanel(true);
$("#ed-add-del").onclick = () => {
  if (!ed || !addEdit) return;
  ed.adds = ed.adds.filter((x) => x !== addEdit.a);
  addEdit = null;
  closeAddPanel(false);
};
$("#ed-add").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches("input")) { e.preventDefault(); confirmAdd(); }
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeAddPanel(true); }
});
function confirmAdd() {
  if (!ed) return;
  const v = readPanel();
  if (!v.w) { $("#ed-add-text").focus(); return; }
  if (addEdit) Object.assign(addEdit.a, v);
  else ed.adds.push(v);
  addEdit = null;
  closeAddPanel(false);
  seek(v.s);
}
// barra da legenda adicionada na linha do tempo (arrastar move)
const ADD_BAR = { y: 9, h: 16 };
function addBarAt(px, py, W) {
  if (py < ADD_BAR.y - 3 || py > ADD_BAR.y + ADD_BAR.h + 3) return null;
  const x = (t) => ((t - ed.ws) / (ed.we - ed.ws)) * W;
  return ed.adds.find((a) => px >= x(a.s) - 3 && px <= x(a.s) + Math.max(6, x(a.e) - x(a.s)) + 3) || null;
}

// a legenda adicionada toma o lugar da fala no trecho dela (igual ao captions.apply_fixes)
const coveredByAdd = (w) => ed.adds.some((a) => a.w && a.s - 0.05 <= w.s && w.s < a.e);
function expandAdds(adds) {  // igual ao captions.expand_adds: várias palavras dividem o intervalo
  const out = [];
  adds.forEach((a, gi) => {
    const parts = a.w.split(/\s+/).filter(Boolean);
    if (!parts.length || a.e <= a.s) return;
    const step = (a.e - a.s) / parts.length;
    parts.forEach((p, i) => out.push({ w: p, s: a.s + i * step, e: a.s + (i + 1) * step, added: gi + 1 }));
  });
  return out;
}

// ---- prévia da legenda e do enquadramento
function updateCaptionStyle() {
  const cap = $("#ed-caption");
  cap.className = `ed-caption ${$("#ed-position").value}`;
  cap.style.setProperty("--cap-hi", $("#ed-color").value);
  cap.style.setProperty("--cap-case", $("#ed-upper").checked ? "uppercase" : "none");
  cap.classList.toggle("hidden", !$("#ed-captions").checked);
  updateCaptionPreview(nowT());
}
["ed-position", "ed-color", "ed-upper", "ed-captions", "ed-preset"].forEach((id) =>
  $(`#${id}`).addEventListener("input", updateCaptionStyle));

function updateCaptionPreview(t) {
  if (!ed) return;
  const cap = $("#ed-caption");
  const [maxWords, maxChars] = PRESET_CHUNK[$("#ed-preset").value] || [3, 18];
  const mode = $("#ed-preset").value;
  const words = [...ed.data.words.filter((w) => !coveredByAdd(w)).map((w) => ({ ...w, w: wordText(w) })), ...expandAdds(ed.adds)]
    .filter((w) => w.w && w.e > ed.sel.s && w.s < ed.sel.e)
    .sort((a, b) => a.s - b.s);
  // mesmo agrupamento do captions.py
  const chunks = [];
  let cur = [];
  for (const w of words) {
    if (cur.length) {
      const len = cur.reduce((a, x) => a + x.w.length + 1, 0) + w.w.length;
      const gap = w.s - cur[cur.length - 1].e;
      const endSent = /[.?!…]$/.test(cur[cur.length - 1].w);
      const otherSource = cur[cur.length - 1].added !== w.added;  // legenda manual fica no próprio bloco
      if (cur.length >= maxWords || len > maxChars || gap > 0.5 || endSent || otherSource) { chunks.push(cur); cur = []; }
    }
    cur.push(w);
  }
  if (cur.length) chunks.push(cur);
  let html = "";
  chunks.forEach((chunk, ci) => {
    const next = chunks[ci + 1] ? chunks[ci + 1][0].s : ed.sel.e;
    const end = Math.min(next, chunk[chunk.length - 1].e + 0.35);
    if (t >= chunk[0].s && t < end) {
      const active = chunk.findIndex((w, i) => t < (chunk[i + 1] ? chunk[i + 1].s : end));
      html = chunk.map((w, i) => {
        const txt = escapeHtml(w.w);
        if (i !== active || mode === "minimal") return txt;
        return mode === "marcador"
          ? `<b style="background:var(--cap-hi);color:#111;padding:0 .15em;border-radius:.15em;text-shadow:none">${txt}</b>`
          : `<b>${txt}</b>`;
      }).join(" ");
    }
  });
  cap.innerHTML = html;
}

function updateLayoutUI() {
  if (!ed) return;
  const { width: sw, height: sh } = ed.data.source;
  const layout = $("#ed-layout").value;
  const landscape = sw / sh > 0.6;
  const showSlider = landscape && (layout === "manual" || layout === "split");
  $("#ed-cropx-wrap").classList.toggle("hidden", !showSlider);
  const box = $("#ed-crop");
  box.classList.toggle("hidden", !showSlider);
  if (showSlider) {
    // fração da largura que o corte ocupa (9:16 inteiro ou painel do jogo da tela dividida)
    const frac = layout === "manual" ? (sh * 9 / 16) / sw : (sh * 1080 / 1152) / sw;
    const cx = +$("#ed-cropx").value;
    const left = Math.min(Math.max(cx - frac / 2, 0), 1 - frac);
    box.style.left = `${left * 100}%`;
    box.style.width = `${frac * 100}%`;
  }
}
$("#ed-layout").addEventListener("input", updateLayoutUI);
$("#ed-cropx").addEventListener("input", updateLayoutUI);

$("#ed-save").onclick = async () => {
  if (!ed) return;
  const title = $("#ed-title").value.trim() || ed.data.clip.title;
  const body = {
    start: +ed.sel.s.toFixed(2), end: +ed.sel.e.toFixed(2), title, title_text: title,
    layout: $("#ed-layout").value, crop_x: +$("#ed-cropx").value,
    captions: $("#ed-captions").checked, caption_preset: $("#ed-preset").value,
    caption_position: $("#ed-position").value, caption_color: $("#ed-color").value,
    caption_upper: $("#ed-upper").checked, title_overlay: $("#ed-title-on").checked,
    title_mode: $("#ed-title-mode").value, title_position: $("#ed-title-pos").value, title_style: $("#ed-title-style").value,
    auto_zoom: $("#ed-zoom").checked, remove_pauses: $("#ed-pauses").checked,
    pos_susto: $("#ed-pos").value, pos_susto_musica: $("#ed-pos-music").checked,
    keyword_highlight: $("#ed-keywords").checked, keyword_color: $("#ed-kwcolor").value,
    word_fixes: ed.fixes,
    word_adds: ed.adds.map(({ w, s, e }) => ({ w, s, e })),
  };
  $("#ed-save").disabled = true;
  try {
    await postJSON(`/api/jobs/${ed.jobId}/clips/${ed.n}`, body);
    const jobId = ed.jobId;
    closeEditor();
    openJob(jobId);
  } catch (ex) {
    $("#ed-error").textContent = ex.message;
    $("#ed-save").disabled = false;
  }
};

loadConfig().then(route);

// ================================================================ capa (thumbnail)
const cdlg = $("#cover-dlg");
let cv = null;  // { jobId, n, info, p (parâmetros), timer, abort, url }
const CV_FIELDS = { text: "#cv-text", style: "#cv-style", upper: "#cv-upper", size: "#cv-size", tilt: "#cv-tilt",
  dark: "#cv-dark", handle: "#cv-handle", zoom: "#cv-zoom", px: "#cv-px", py: "#cv-py" };

async function openCover(jobId, n) {
  cv = null;
  $("#cv-error").textContent = "";
  $("#cv-loading").textContent = "Preparando a capa…";
  $("#cv-loading").classList.remove("hidden");
  $("#cv-body").classList.add("hidden");
  $("#cv-save").disabled = true;
  cdlg.showModal();
  let info;
  try {
    info = await api(`/api/jobs/${jobId}/clips/${n}/capa`);
    if (!info.cover) {
      $("#cv-loading").textContent = "Escolhendo o momento ápice e fazendo a capa…";
      info = await postJSON(`/api/jobs/${jobId}/clips/${n}/capa`, {});
    }
  } catch (ex) {
    $("#cv-loading").textContent = `Não deu para abrir a capa: ${ex.message}`;
    return;
  }
  cv = { jobId, n, info, p: { ...info.cover }, timer: null, abort: null, url: null };
  $("#cv-sub").textContent = info.title;
  $("#cv-style").innerHTML = Object.entries(info.styles).map(([k, label]) => `<option value="${k}">${label}</option>`).join("");
  for (const [k, sel] of Object.entries(CV_FIELDS)) {
    const el = $(sel);
    if (el.type === "checkbox") el.checked = cv.p[k] !== false;
    else el.value = cv.p[k] ?? "";
  }
  syncColors();
  syncRangeValues();
  $("#cv-invideo").checked = !!info.in_video;
  $("#cv-default").checked = false;
  const chips = [...new Set([info.title, ...(info.title_options || [])])].filter(Boolean);
  $("#cv-chips").replaceChildren(...chips.map((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = t;
    b.title = "Usar este texto na capa";
    b.onclick = () => { $("#cv-text").value = t; cvChanged("text"); };
    return b;
  }));
  const origin = { original: "Quadros tirados do vídeo original (sem legenda).",
    clipe: "Quadros tirados do clipe pronto, com a legenda dele. Salve o clipe no editor de novo para ter quadros limpos." }[info.origin];
  $("#cv-origin").textContent = origin || "";
  $("#cv-origin").classList.toggle("hidden", !origin);
  renderCoverFrames();
  setCoverImage(`/media/${info.file}?v=${info.rev}`);
  $("#cv-download").href = `/media/${info.file}?v=${info.rev}`;
  $("#cv-download").download = `${String(n).padStart(2, "0")}-capa.jpg`;
  $("#cv-loading").classList.add("hidden");
  $("#cv-body").classList.remove("hidden");
  $("#cv-save").disabled = false;
}

// número ao lado de cada barra do editor de capa
const CV_FORMAT = {
  "cv-size": (v) => `${Math.round(v * 100)}%`,
  "cv-tilt": (v) => `${v > 0 ? "+" : ""}${Math.round(v)}°`,
  "cv-dark": (v) => `${Math.round(v * 100)}%`,
  "cv-zoom": (v) => `${v.toFixed(2).replace(".", ",")}x`,
  "cv-px": (v) => `${Math.round(v * 100)}%`,
  "cv-py": (v) => `${Math.round(v * 100)}%`,
};
function syncRangeValues() {
  $$("#cover-dlg .cv-val").forEach((b) => { b.textContent = CV_FORMAT[b.dataset.for](+$(`#${b.dataset.for}`).value); });
}
$$("#cover-dlg input[type=range]").forEach((el) => el.addEventListener("input", syncRangeValues));

function syncColors() {
  const st = cv.info.style_colors[cv.p.style] || {};
  $("#cv-color").value = cv.p.color || st.text || "#FFFFFF";
  $("#cv-hl").value = cv.p.hl_color || st.hl || "#FFD400";
}

function renderCoverFrames() {
  const { frames, step, cover } = cv.info;
  $("#cv-frames").replaceChildren(...frames.map((f, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = (i === cv.p.frame ? "on " : "") + (i === cover.apex ? "apex" : "");
    b.title = i === cover.apex ? "Momento ápice (escolhido pelo Cortaí)" : `${(i * step).toFixed(1)} s`;
    b.innerHTML = `<img loading="lazy" alt=""><span>${(i * step).toFixed(1).replace(".", ",")} s</span>`;
    $("img", b).src = `/media/${f}`;
    b.onclick = () => { cv.p.frame = i; markFrame(); cvRefresh(); };
    return b;
  }));
  requestAnimationFrame(() => $("#cv-frames .on")?.scrollIntoView({ block: "nearest", inline: "center" }));
}

function markFrame() {
  $$("#cv-frames button").forEach((b, i) => b.classList.toggle("on", i === cv.p.frame));
}

function setCoverImage(src) {
  if (cv?.url) URL.revokeObjectURL(cv.url);
  if (cv) cv.url = src.startsWith("blob:") ? src : null;
  $("#cv-img").src = src;
}

function cvChanged(key) {
  if (!cv) return;
  const el = $(CV_FIELDS[key]);
  cv.p[key] = el.type === "checkbox" ? el.checked : el.type === "range" ? +el.value : el.value;
  if (key === "style") { cv.p.color = null; cv.p.hl_color = null; syncColors(); }
  cvRefresh();
}
for (const key of Object.keys(CV_FIELDS)) {
  $(CV_FIELDS[key]).addEventListener("input", () => cvChanged(key));
}
$("#cv-color").addEventListener("input", (e) => { cv.p.color = e.target.value; cvRefresh(); });
$("#cv-hl").addEventListener("input", (e) => { cv.p.hl_color = e.target.value; cvRefresh(); });
$("#cv-colors-reset").onclick = () => { cv.p.color = null; cv.p.hl_color = null; syncColors(); cvRefresh(); };
$("#cv-apex").onclick = () => { cv.p.frame = cv.info.cover.apex; markFrame(); renderCoverFrames(); cvRefresh(); };

// prévia: desenhada pelo mesmo motor da capa final, com um pequeno atraso para não pedir a cada tecla
function cvRefresh() {
  clearTimeout(cv.timer);
  cv.timer = setTimeout(async () => {
    cv.abort?.abort();
    const ctl = new AbortController();
    cv.abort = ctl;
    $("#cv-busy").classList.remove("hidden");
    try {
      const res = await fetch(`/api/jobs/${cv.jobId}/clips/${cv.n}/capa/previa`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(cv.p), signal: ctl.signal,
      });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      setCoverImage(URL.createObjectURL(await res.blob()));
      $("#cv-error").textContent = "";
    } catch (ex) {
      if (ex.name !== "AbortError") $("#cv-error").textContent = ex.message;
    } finally {
      if (cv?.abort === ctl) $("#cv-busy").classList.add("hidden");
    }
  }, 140);
}

// arrastar na prévia move o texto para cima/baixo
(() => {
  const stage = $("#cv-stage");
  let drag = null;
  stage.addEventListener("pointerdown", (e) => {
    if (!cv) return;
    stage.setPointerCapture(e.pointerId);
    drag = { y0: e.clientY, v0: cv.p.y, h: stage.getBoundingClientRect().height };
  });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    cv.p.y = Math.min(0.8, Math.max(0.18, drag.v0 + (e.clientY - drag.y0) / drag.h));
    cvRefresh();
  });
  const end = () => { drag = null; };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);
})();

async function saveCover() {
  if (!cv) return;
  const btn = $("#cv-save");
  btn.disabled = true;
  $("#cv-error").textContent = "";
  try {
    const body = { ...cv.p, in_video: $("#cv-invideo").checked };
    delete body.apex; delete body.frames; delete body.auto_text; delete body.custom_text; delete body.custom_frame;
    await postJSON(`/api/jobs/${cv.jobId}/clips/${cv.n}/capa`, body);
    if ($("#cv-default").checked) {
      await postJSON("/api/settings", { cover_style: cv.p.style, cover_handle: cv.p.handle || "", cover_in_video: body.in_video });
      loadConfig();
    }
    const jobId = cv.jobId;
    closeCover();
    openJob(jobId);
  } catch (ex) {
    $("#cv-error").textContent = ex.message;
    btn.disabled = false;
  }
}

function closeCover() {
  if (cv) { clearTimeout(cv.timer); cv.abort?.abort(); if (cv.url) URL.revokeObjectURL(cv.url); }
  cv = null;
  cdlg.close();
}
$("#cv-save").onclick = saveCover;
$("#cv-cancel").onclick = closeCover;
$("#cv-close").onclick = closeCover;
cdlg.addEventListener("cancel", (e) => { e.preventDefault(); closeCover(); });
