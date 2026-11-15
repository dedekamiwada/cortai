// Chamar pra live: editor com prévia ao vivo (fundo + legenda palavra por palavra + selo LIVE + som).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* sem corpo */ }
    throw new Error(msg);
  }
  return res.json();
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const upload = (file) => fetch(`/api/uploads?filename=${encodeURIComponent(file.name || "arquivo.webm")}`, { method: "POST", body: file })
  .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || "falha no envio"); return d.upload_id; });

const PRESET_WORDS = { destaque: 3, marcador: 3, uma: 1, minimal: 6 };
const TEMPLATES = {
  gancho: ["Esse é o mapa dos 100 níveis mais difíceis de parkour do Minecraft.", "Eu tô tentando {desafio} e ninguém acredita.",
    "Hoje é o dia {n} tentando {meta}."],
  agora: ["Nesse momento eu tô em live tentando zerar.", "Tô ao vivo agora: {campanha}.", "Tô ao vivo agora e falta pouco."],
  chamada: ["Se a bolinha estiver vermelha, clica aí e vem assistir.", "Clica na minha foto: a live tá rolando agora.",
    "Entra na live e me ajuda a passar."],
};
let cur = null, saveTimer = null, raf = 0, serie = null;

// ---------------------------------------------------------------- navegação
function route() {
  const m = location.hash.match(/^#\/c\/(.+)$/);
  $("#home").classList.toggle("hidden", !!m);
  $("#editor").classList.toggle("hidden", !m);
  if (m) open(decodeURIComponent(m[1])); else { cur = null; loadList(); }
}
window.addEventListener("hashchange", route);
$$("[data-nav=home]").forEach((b) => (b.onclick = () => (location.hash = "")));

async function loadList() {
  const items = await api("/api/promo");
  $("#promo-list").innerHTML = items.length ? "" : `<p class="empty">Nenhuma chamada ainda. Crie a primeira: leva 1 minuto.</p>`;
  for (const p of items) {
    const el = document.createElement("button");
    el.className = "job-item";
    el.innerHTML = `${p.result?.thumb ? `<img alt="" src="/media/${p.result.thumb}?v=${p.rev}">` : `<div class="ph"></div>`}
      <div class="meta"><div class="name"></div><div class="sub"></div></div>`;
    $(".name", el).textContent = p.name;
    $(".sub", el).textContent = `${p.status === "done" ? "Pronta" : p.status === "rendering" ? "Gerando…" : "Rascunho"} · ${new Date(p.created * 1000).toLocaleString("pt-BR")}`;
    el.onclick = () => (location.hash = `#/c/${p.id}`);
    $("#promo-list").appendChild(el);
  }
}
$("#new-promo").onclick = async () => { const p = await postJSON("/api/promo", {}); location.hash = `#/c/${p.id}`; };

// ---------------------------------------------------------------- editor
async function open(id) {
  cur = await api(`/api/promo/${id}`);
  const [cfg, lib, jobsList] = await Promise.all([api("/api/config"), api("/api/pos/biblioteca").catch(() => ({ musicas: {} })), api("/api/jobs")]);
  serie = await api("/api/serie").catch(() => null);
  $("#pm-preset").innerHTML = Object.entries(cfg.caption_presets).map(([k, l]) => `<option value="${k}">${escapeHtml(l)}</option>`).join("");
  $("#pm-music").innerHTML = `<option value="">Sem música</option>` + Object.entries(lib.musicas).filter(([, fs]) => fs.length)
    .map(([c, fs]) => `<optgroup label="${escapeHtml(c)}">${fs.map((f) => `<option value="${escapeHtml(`${c}/${f}`)}">${escapeHtml(f)}</option>`).join("")}</optgroup>`).join("");
  $("#pm-job").innerHTML = `<option value="">Escolha uma live…</option>` + jobsList.filter((j) => j.status === "done")
    .map((j) => `<option value="${j.id}">${escapeHtml(j.name)}</option>`).join("");
  fill();
  chips();
  showTab(localStorage.getItem("cortai.pmTab") || "roteiro");
  loadBackground();
  renderResult();
}
function fill() {
  const p = cur;
  $("#pm-name").value = p.name;
  $("#pm-gancho").value = p.roteiro.gancho; $("#pm-agora").value = p.roteiro.agora; $("#pm-chamada").value = p.roteiro.chamada;
  const v = p.visual;
  $("#pm-preset").value = v.caption_preset; $("#pm-pos").value = v.caption_position; $("#pm-color").value = v.caption_color;
  $("#pm-upper").checked = v.caption_upper; $("#pm-zoom").checked = v.zoom; $("#pm-selo").checked = v.selo;
  $("#pm-selo-texto").value = v.selo_texto; $("#pm-selo-pos").value = v.selo_pos;
  $("#pm-music").value = p.musica.arquivo || ""; $("#pm-music-vol").value = p.musica.volume; $("#pm-bg-vol").value = p.musica.fundo ?? 0.35;
  $("#pm-job").value = p.fundo.tipo === "projeto" ? p.fundo.job : "";
  $("#pm-start").value = fmtTime(p.fundo.start);
  $("#pm-avatar").src = v.avatar ? `/media/_promo/${p.id}/${v.avatar}?v=${Date.now()}` : "";
  vols();
  voiceInfo();
  tele();
  draw();
}
function chips() {
  const vars = {
    n: serie?.proximo ?? "", meta: serie?.meta || "pegar Champion", desafio: serie?.meta ? `${serie.meta}` : "zerar o desafio",
    campanha: serie?.campanha?.episodios ? `${serie.campanha.vitorias} vitórias e ${serie.campanha.derrotas} derrotas até agora` : "olha onde eu cheguei",
  };
  for (const part of ["gancho", "agora", "chamada"]) {
    $(`#chips-${part}`).innerHTML = TEMPLATES[part].map((t) => t.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? ""))
      .map((t) => `<button type="button" class="chip-btn">${escapeHtml(t)}</button>`).join("");
    $$(`#chips-${part} button`).forEach((b) => (b.onclick = () => { $(`#pm-${part}`).value = b.textContent; changed(); }));
  }
}
function showTab(tab) {
  try { localStorage.setItem("cortai.pmTab", tab); } catch { /* sem armazenamento */ }
  $$("#pm-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  $$(".pm-side .tab-page").forEach((s) => s.classList.toggle("hidden", s.dataset.tab !== tab));
}
$$("#pm-tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

function collect() {
  return {
    name: $("#pm-name").value,
    roteiro: { gancho: $("#pm-gancho").value, agora: $("#pm-agora").value, chamada: $("#pm-chamada").value },
    visual: { caption_preset: $("#pm-preset").value, caption_position: $("#pm-pos").value, caption_color: $("#pm-color").value,
      caption_upper: $("#pm-upper").checked, zoom: $("#pm-zoom").checked, selo: $("#pm-selo").checked,
      selo_texto: $("#pm-selo-texto").value, selo_pos: $("#pm-selo-pos").value },
    musica: { arquivo: $("#pm-music").value, volume: +$("#pm-music-vol").value, fundo: +$("#pm-bg-vol").value },
  };
}
// cada mudança: prévia na hora (com o que está na tela) e salva em seguida (sem botão de salvar)
function changed() {
  Object.assign(cur, { ...cur, ...collect(), visual: { ...cur.visual, ...collect().visual } });
  vols(); tele(); draw();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { const p = await postJSON(`/api/promo/${cur.id}`, collect()); cur.linha = p.linha; cur.status = p.status; draw(); $("#pm-status").textContent = ""; }
    catch (ex) { $("#pm-status").textContent = ex.message; }
  }, 450);
}
$$("#editor input, #editor textarea, #editor select").forEach((el) => {
  if (el.type === "file" || el.id === "pm-job" || el.id === "pm-start" || el.id === "pm-post") return;
  el.addEventListener("input", changed);
});
function vols() {
  $("#pm-music-v").textContent = `${Math.round($("#pm-music-vol").value * 100)}%`;
  $("#pm-bg-v").textContent = `${Math.round($("#pm-bg-vol").value * 100)}%`;
  $("#pm-video").volume = +$("#pm-bg-vol").value;
}
function tele() {
  $("#pm-tele").innerHTML = ["gancho", "agora", "chamada"].map((k) => `<p>${escapeHtml($(`#pm-${k}`).value)}</p>`).join("");
}

// ---------------------------------------------------------------- fundo
$("#pm-job").onchange = async () => {
  const id = $("#pm-job").value;
  $("#pm-clips").innerHTML = "";
  if (!id) return;
  const job = await api(`/api/jobs/${id}`);
  $("#pm-clips").innerHTML = `<span class="muted small">Começar num corte:</span>` + job.clips.filter((c) => c.status === "done").slice(0, 12)
    .map((c) => `<button type="button" class="chip-btn" data-t="${c.start}">${escapeHtml(c.title)} · ${fmtTime(c.start)}</button>`).join("");
  $$("#pm-clips button").forEach((b) => (b.onclick = () => { $("#pm-start").value = fmtTime(+b.dataset.t); $("#pm-apply-bg").click(); }));
};
$("#pm-apply-bg").onclick = async () => {
  const start = parseTime($("#pm-start").value);
  if (!$("#pm-job").value) return ($("#pm-bg-msg").textContent = "Escolha a live.");
  if (isNaN(start)) return ($("#pm-bg-msg").textContent = "Use HH:MM:SS.");
  $("#pm-bg-msg").textContent = "Preparando a prévia…";
  try {
    await postJSON(`/api/promo/${cur.id}`, { fundo: { tipo: "projeto", job: $("#pm-job").value, start, dur: 30 } });
    cur = await api(`/api/promo/${cur.id}`);
    await loadBackground();
    $("#pm-bg-msg").textContent = "";
  } catch (ex) { $("#pm-bg-msg").textContent = ex.message; }
};
$("#pm-bg-file").onchange = (e) => attachFile(e.target.files[0], "fundo", $("#pm-bg-msg"));
$("#pm-avatar-file").onchange = (e) => attachFile(e.target.files[0], "avatar", $("#pm-status"));
async function attachFile(file, tipo, msg) {
  if (!file) return;
  msg.textContent = tipo === "voz" ? "Enviando e ouvindo a sua fala…" : "Enviando…";
  try {
    const id = await upload(file);
    cur = await postJSON(`/api/promo/${cur.id}/arquivo`, { tipo, upload_id: id });
    fill();
    if (tipo === "fundo") await loadBackground();
    msg.textContent = tipo === "voz" ? `Pronto: ${cur.voz.palavras.length} palavras sincronizadas.` : "";
  } catch (ex) { msg.textContent = ex.message; }
}
async function loadBackground() {
  const has = !!cur.fundo.tipo;
  $("#pm-empty").classList.toggle("hidden", has);
  if (!has) return;
  try {
    const pv = await api(`/api/promo/${cur.id}/previa`);
    const src = `/media/${pv.file}?k=${encodeURIComponent(pv.key)}`;
    if ($("#pm-video").getAttribute("src") !== src) $("#pm-video").src = src;
  } catch (ex) { $("#pm-bg-msg").textContent = ex.message; }
}

// ---------------------------------------------------------------- voz
let rec = null;
$("#pm-rec").onclick = async () => {
  if (rec) { rec.stop(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { $("#pm-voice-msg").textContent = "Não consegui usar o microfone aqui. Grave no celular/OBS e use “Enviar áudio”."; return; }
  const chunks = [];
  rec = new MediaRecorder(stream);
  rec.ondataavailable = (e) => chunks.push(e.data);
  rec.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    rec = null;
    $("#pm-rec").lastChild.textContent = "Gravar";
    attachFile(new File(chunks, "voz.webm", { type: "audio/webm" }), "voz", $("#pm-voice-msg"));
  };
  rec.start();
  $("#pm-rec").lastChild.textContent = "Parar e usar";
  $("#pm-voice-msg").textContent = "Gravando… leia o roteiro acima.";
};
$("#pm-voice-file").onchange = (e) => attachFile(e.target.files[0], "voz", $("#pm-voice-msg"));
$("#pm-voice-del").onclick = async () => { cur = await postJSON(`/api/promo/${cur.id}/voz/remover`, {}); fill(); };
let voiceAudio = null;
function voiceInfo() {
  const has = !!cur.voz?.arquivo;
  $("#pm-voice-del").classList.toggle("hidden", !has);
  voiceAudio = has ? new Audio(`/media/_promo/${cur.id}/${cur.voz.arquivo}`) : null;
  if (has && !$("#pm-voice-msg").textContent) $("#pm-voice-msg").textContent = `Voz: ${cur.voz.palavras.length} palavras sincronizadas.`;
}

// ---------------------------------------------------------------- prévia
let t0 = 0, musicAudio = null;
$("#pm-play").onclick = () => {
  const v = $("#pm-video");
  if (!v.paused) { v.pause(); voiceAudio?.pause(); musicAudio?.pause(); $("#pm-play").textContent = "Tocar"; return; }
  v.currentTime = 0;
  t0 = performance.now();
  v.play();
  if (voiceAudio) { voiceAudio.currentTime = 0; voiceAudio.play(); }
  const m = $("#pm-music").value;
  musicAudio?.pause();
  musicAudio = m ? new Audio(`/api/pos/biblioteca/arquivo?p=${encodeURIComponent(`musicas/${m}`)}`) : null;
  if (musicAudio) { musicAudio.volume = +$("#pm-music-vol").value * (voiceAudio ? 0.6 : 1); musicAudio.play(); }
  $("#pm-play").textContent = "Parar";
  cancelAnimationFrame(raf);
  loop();
};
function loop() {
  const dur = cur.linha?.duracao || 10;
  const t = (performance.now() - t0) / 1000;
  if (t > dur) { $("#pm-play").click(); draw(0); return; }
  draw(t);
  raf = requestAnimationFrame(loop);
}
function draw(t = 0) {
  if (!cur) return;
  const v = cur.visual, L = cur.linha || { palavras: [], duracao: 10, selo_t: 8 };
  $("#pm-dur").textContent = `vídeo de ${fmtDur(L.duracao)}`;
  $("#pm-time").textContent = fmtTime(t, true);
  $("#pm-video").style.transform = v.zoom ? `scale(${1 + 0.08 * Math.min(1, t / L.duracao)})` : "";
  // legenda: blocos do estilo escolhido, palavra da vez destacada
  const n = PRESET_WORDS[v.caption_preset] || 3, words = L.palavras;
  let html = "";
  for (let i = 0; i < words.length; i += n) {
    const chunk = words.slice(i, i + n), next = words[i + n];
    const end = next ? next.s : chunk[chunk.length - 1].e + 0.35;
    if (t >= chunk[0].s && t < end) {
      html = chunk.map((w, k) => (t >= w.s && (k === chunk.length - 1 || t < chunk[k + 1].s) && v.caption_preset !== "minimal"
        ? (v.caption_preset === "marcador" ? `<b class="box">${escapeHtml(w.w)}</b>` : `<b>${escapeHtml(w.w)}</b>`) : escapeHtml(w.w))).join(" ");
    }
  }
  const cap = $("#pm-cap");
  cap.className = `spv-cap ${v.caption_position} p-${v.caption_preset}`;
  cap.style.setProperty("--hi", v.caption_color);
  cap.style.textTransform = v.caption_upper ? "uppercase" : "none";
  cap.innerHTML = html;
  // selo LIVE (e avatar) a partir da chamada
  const on = v.selo && t >= L.selo_t;
  const age = t - L.selo_t, pop = age < 0.14 ? 0.3 + 0.82 * (age / 0.14) : age < 0.24 ? 1.12 - 0.12 * ((age - 0.14) / 0.1) : 1;
  const y = { centro: 42, topo: 24, baixo: 62 }[v.selo_pos] || 42;
  $("#pm-avatar").classList.toggle("hidden", !(on && v.avatar));
  $("#pm-avatar").style.top = `${y}%`;
  const badge = $("#pm-badge");
  badge.classList.toggle("hidden", !on);
  badge.textContent = `● ${(v.selo_texto || "LIVE").toUpperCase()}`;
  badge.style.top = `calc(${y}% + ${v.avatar ? 12 : 0}%)`;
  badge.style.transform = `translate(-50%, -50%) scale(${pop})`;
}

// ---------------------------------------------------------------- gerar
$("#pm-render").onclick = async () => {
  clearTimeout(saveTimer);
  try {
    await postJSON(`/api/promo/${cur.id}`, collect());
    await postJSON(`/api/promo/${cur.id}/gerar`, {});
    $("#pm-status").textContent = "Gerando o vídeo…";
    $("#pm-render").disabled = true;
    const poll = async () => {
      cur = await api(`/api/promo/${cur.id}`);
      if (cur.status === "rendering") return setTimeout(poll, 1000);
      $("#pm-render").disabled = false;
      $("#pm-status").textContent = cur.status === "error" ? `Erro: ${cur.error}` : "Pronto.";
      renderResult();
    };
    poll();
  } catch (ex) { $("#pm-status").textContent = ex.message; }
};
function renderResult() {
  const r = cur.result;
  $("#pm-result").classList.toggle("hidden", !r);
  if (!r) return;
  const src = `/media/${r.file}?v=${cur.rev}`;
  if ($("#pm-out").getAttribute("src") !== src) $("#pm-out").src = src;
  Object.assign($("#pm-dl"), { href: src, download: `${cur.name.replace(/[^\p{L}\p{N} _-]/gu, "").slice(0, 60) || "chamada"}.mp4` });
  if (document.activeElement !== $("#pm-post")) $("#pm-post").value = r.post;
}
$("#pm-copy").onclick = async () => { await navigator.clipboard.writeText($("#pm-post").value); $("#pm-copy").textContent = "Copiado!"; setTimeout(() => ($("#pm-copy").textContent = "Copiar texto"), 1400); };
$("#pm-delete").onclick = async () => {
  if (!confirm(`Excluir a chamada "${cur.name}"?`)) return;
  await api(`/api/promo/${cur.id}`, { method: "DELETE" });
  location.hash = "";
};

route();
