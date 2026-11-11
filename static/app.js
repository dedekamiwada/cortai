const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const STAGES = {
  queued: "Na fila",
  downloading: "Baixando",
  transcribing: "Transcrevendo",
  selecting: "Claude escolhendo candidatos",
  reviewing: "Claude assistindo aos candidatos",
  waiting_manual: "Esperando sua resposta do Claude",
  rendering: "Renderizando",
  done: "Pronto",
  error: "Erro",
};
const BUSY = new Set(["queued", "downloading", "transcribing", "selecting", "reviewing", "rendering"]);
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
    // padrões (de fábrica + os salvos pelo usuário) no formulário e no painel "estilo de todos"
    fillFields($("#job-form"), cfg.defaults);
    fillFields($("#apply-form"), cfg.defaults);
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
const STYLE_FIELDS = ["layout", "caption_preset", "caption_position", "caption_color", "keyword_color", "title_mode",
  "title_position", "title_style", "captions", "caption_upper", "keyword_highlight", "title_overlay", "auto_zoom", "remove_pauses", "normalize_audio"];

// ---------------------------------------------------------------- estilos
async function loadStyles() {
  clearTimeout(stylePoll);
  const list = await api("/api/styles");
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
    range_start: parseTime(f.range_start.value), range_end: parseTime(f.range_end.value),
    language: f.language.value, backend: f.backend.value, whisper_model: f.whisper_model.value,
    ...readFields(f, STYLE_FIELDS),
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
    btn.textContent = "Gerar cortes";
  }
});

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
    $(".name", el).textContent = j.name;
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
  $("#job-title").textContent = job.name;
  const st = $("#job-status");
  const busy = BUSY.has(job.status);
  const indeterminate = ["selecting", "downloading", "queued"].includes(job.status) ||
    (job.status === "reviewing" && job.message.startsWith("Claude"));
  st.innerHTML = `<div class="msg"></div>${busy ? `<div class="bar ${indeterminate ? "indeterminate" : ""}"><i></i></div>` : ""}
    ${job.error ? `<div class="err"></div>` : ""}`;
  const extra = job.backend ? ` · IA: ${{ cli: "Claude Code", api: "API Anthropic", manual: "manual" }[job.backend]}` : "";
  const dur = job.source.duration ? ` · vídeo de ${fmtTime(job.source.duration)}` : "";
  $(".msg", st).textContent = `${STAGES[job.status] || job.status} — ${job.message || ""}${dur}${extra}`;
  if (busy) $(".bar > i", st).style.width = `${Math.round((job.progress || 0) * 100)}%`;
  if (job.error) $(".err", st).textContent = job.error;

  $("#manual").classList.toggle("hidden", job.status !== "waiting_manual");
  const ready = ["done", "error"].includes(job.status) && !!job.source.duration;
  $("#job-tools").classList.toggle("hidden", !ready);
  if (!ready) $$(".panel").forEach((p) => p.classList.add("hidden"));
  $("#zip-btn").href = `/api/jobs/${job.id}/export.zip`;
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
  const score = $(".score", el);
  score.textContent = c.score;
  score.className = `score ${c.score >= 75 ? "" : c.score >= 50 ? "mid" : "low"}`;
  $(".dur", el).textContent = `${fmtTime(c.start)} → ${fmtTime(c.end)} · ${(c.out_duration ?? c.end - c.start).toFixed(0)}s`;
  const extras = [LAYOUTS[c.layout] || c.layout];
  if (c.zooms_applied) extras.push(`${c.zooms_applied} zoom${c.zooms_applied > 1 ? "s" : ""}`);
  if (c.removed > 0.2) extras.push(`−${c.removed.toFixed(1)}s de pausas`);
  if (c.manual) extras.push("manual");
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
  $(".edit", el).disabled = !(done || c.status === "error");
  $(".clip-error", el).textContent = c.error || "";
  el._clip = c;
}

function bindClip(el, jobId, n) {
  $(".edit", el).onclick = () => openEditor(jobId, n);
  $(".copy", el).onclick = async (e) => {
    const c = el._clip;
    await navigator.clipboard.writeText(c.post || `${c.title}\n\n${(c.hashtags || []).join(" ")}`);
    e.target.textContent = "Copiado!";
    setTimeout(() => (e.target.textContent = "Copiar post"), 1500);
  };
  $(".studio", el).onclick = async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Abrindo…";
    try {
      const p = await postJSON("/api/studio/projects", {
        name: el._clip.title, ratio: "9:16", from_clip: { job: jobId, n },
      });
      location.href = `studio.html#/p/${p.id}`;
    } catch (ex) {
      alert(ex.message);
      e.target.disabled = false;
      e.target.textContent = "🎬 Estúdio";
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
    playingSel: false, drag: null,
  };
  const { width: sw, height: sh } = data.source;
  const stage = $("#ed-stage");
  stage.style.aspectRatio = `${sw} / ${sh}`;
  stage.style.width = sw / sh < 1 ? `min(100%, calc(52vh * ${sw / sh}))` : "100%";
  vid.src = `/media/${data.proxy}`;
  vid.onloadedmetadata = () => { vid.currentTime = ed.sel.s - ws; };

  $("#ed-title").value = data.clip.title;
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
  $("#ed-keywords").checked = o.keyword_highlight !== false;
  $("#ed-kwcolor").value = o.keyword_color || "#39E75F";
  const kws = data.clip.keywords || [];
  $("#ed-kwlist").textContent = kws.length ? `Palavras-chave do Claude: ${kws.join(", ")}` : "Sem palavras-chave para este clipe.";
  const vertical = sw / sh < 1;
  $("#ed-layout").disabled = vertical;
  $("#ed-vertical-note").classList.toggle("hidden", !vertical);

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
  if (Math.abs(px - xs) < 10) ed.drag = { kind: "s" };
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
  canvas.style.cursor = Math.abs(px - xs) < 10 || Math.abs(px - xe) < 10 ? "ew-resize" : "pointer";
  if (!ed.drag) return;
  const t = Math.min(Math.max(ed.drag.toT(px), ed.ws), ed.we);
  if (ed.drag.kind === "s") { if (t < ed.sel.e - 1) { ed.sel.s = t; seek(t); } }
  else if (ed.drag.kind === "e") { if (t > ed.sel.s + 1) { ed.sel.e = t; seek(t); } }
  else if (ed.drag.kind === "move") {
    const len = ed.sel.e - ed.sel.s;
    const s = Math.min(Math.max(t - ed.drag.off, ed.ws), ed.we - len);
    ed.sel = { s, e: s + len };
    tick();
  } else seek(t);
});
canvas.addEventListener("pointerup", () => { if (ed) ed.drag = null; });
window.addEventListener("resize", () => drawTimeline());

// ---- transcrição
function wordText(w) {
  const k = w.s.toFixed(2);
  return k in ed.fixes ? ed.fixes[k] : w.w;
}
function renderWords() {
  const box = $("#ed-words");
  box.replaceChildren(...ed.data.words.map((w, i) => {
    const span = document.createElement("span");
    span.dataset.i = i;
    span.onclick = () => seek(w.s);
    span.ondblclick = () => editWord(span, w);
    fillWord(span, w);
    return span;
  }));
  updateWordClasses(nowT());
}
function fillWord(span, w) {
  const k = w.s.toFixed(2);
  const fixed = k in ed.fixes;
  span.textContent = fixed && !ed.fixes[k] ? w.w : wordText(w);
  span.classList.toggle("fixed", fixed && !!ed.fixes[k]);
  span.classList.toggle("removed", fixed && !ed.fixes[k]);
  span.title = fixed ? `original: ${w.w}` : "";
}
function editWord(span, w) {
  const input = document.createElement("input");
  input.value = wordText(w);
  span.replaceChildren(input);
  input.focus();
  input.select();
  const done = (save) => {
    if (save) {
      const k = w.s.toFixed(2);
      const v = input.value.trim();
      if (v === w.w) delete ed.fixes[k];
      else ed.fixes[k] = v;
    }
    fillWord(span, w);
    updateCaptionPreview(nowT());
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); done(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
  };
  input.onblur = () => done(true);
}
let lastNow = -1;
function updateWordClasses(t) {
  const spans = $("#ed-words").children;
  let nowIdx = -1;
  ed.data.words.forEach((w, i) => {
    const s = spans[i];
    if (!s || s.firstElementChild) return;
    s.classList.toggle("in", w.e > ed.sel.s && w.s < ed.sel.e);
    if (t >= w.s && t <= w.e + 0.05) nowIdx = i;
  });
  if (nowIdx !== lastNow) {
    if (spans[lastNow]) spans[lastNow].classList.remove("now");
    if (spans[nowIdx]) {
      spans[nowIdx].classList.add("now");
      if (!vid.paused) spans[nowIdx].scrollIntoView({ block: "nearest" });
    }
    lastNow = nowIdx;
  }
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
  const words = ed.data.words
    .map((w) => ({ ...w, w: wordText(w) }))
    .filter((w) => w.w && w.e > ed.sel.s && w.s < ed.sel.e);
  // mesmo agrupamento do captions.py
  const chunks = [];
  let cur = [];
  for (const w of words) {
    if (cur.length) {
      const len = cur.reduce((a, x) => a + x.w.length + 1, 0) + w.w.length;
      const gap = w.s - cur[cur.length - 1].e;
      const endSent = /[.?!…]$/.test(cur[cur.length - 1].w);
      if (cur.length >= maxWords || len > maxChars || gap > 0.5 || endSent) { chunks.push(cur); cur = []; }
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
    keyword_highlight: $("#ed-keywords").checked, keyword_color: $("#ed-kwcolor").value,
    word_fixes: ed.fixes,
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
