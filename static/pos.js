const $ = (s, el = document) => el.querySelector(s);
// nome para exibir: o arquivo baixado vem como "Tentando+pegar+champ (1).mp4"
const prettyName = (s) => { s = s || ""; try { s = decodeURIComponent(s); } catch {} return s.replace(/\+/g, " ").replace(/\.(mp4|mkv|mov|webm|flv|avi|ts)$/i, "").trim() || s; };

const STAGES = {
  queued: "Na fila", transcribing: "Transcrevendo", watching: "Preparando frames",
  planning: "Claude montando os beats", ready: "Plano pronto", rendering: "Renderizando",
  done: "Pronto", error: "Erro",
};
const BUSY = new Set(["queued", "transcribing", "watching", "planning", "rendering"]);
const TIPOS = {
  zoom_punch: "Zoom punch", zoom_lento: "Zoom lento", tremor: "Tremor", flash_texto: "Flash de texto",
  selo: "Selo", emote: "Emote", sfx: "Efeito sonoro",
};
const CLIMAS = { tensao: "Tensão", corte_seco: "Corte seco (música para)", triste: "Triste", epico: "Épico", comico: "Cômico" };

let timer = null;
let current = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}
const postJSON = (path, body) => api(path, {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
});
// tempo no formato universal HH:MM:SS.d, igual ao resto do Cortaí
const sec = (t) => {
  t = Math.round(Math.max(0, t || 0) * 10) / 10;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), r = t - h * 3600 - m * 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${r.toFixed(1).padStart(4, "0")}`;
};

// ---------------------------------------------------------------- navegação
function route() {
  clearTimeout(timer);
  const m = location.hash.match(/^#\/p\/(.+)$/);
  $("#home").classList.toggle("hidden", !!m);
  $("#proj").classList.toggle("hidden", !m);
  if (m) openProject(decodeURIComponent(m[1]), true);
  else { current = null; loadList(); loadLibrary(); }
}
window.addEventListener("hashchange", route);
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-nav=home]")) { e.preventDefault(); location.hash = ""; }
});

// ---------------------------------------------------------------- início
async function loadList() {
  const list = $("#pos-list");
  const items = await api("/api/pos");
  if (!items.length) { list.innerHTML = `<p class="empty">Nenhuma pós-edição ainda.</p>`; return; }
  list.replaceChildren(...items.map((p) => {
    const el = document.createElement("button");
    el.className = "job-item";
    const thumb = p.result?.thumb;
    el.innerHTML = `${thumb ? `<img alt="">` : `<div class="ph"></div>`}<div class="meta"><div class="name"></div><div class="sub"></div></div>`;
    if (thumb) $("img", el).src = `/media/${thumb}?v=${p.rev}`;
    $(".name", el).textContent = prettyName(p.name);
    $(".sub", el).textContent = `${STAGES[p.status] || p.status} · ${new Date(p.created * 1000).toLocaleString("pt-BR")}`;
    el.onclick = () => (location.hash = `#/p/${p.id}`);
    return el;
  }));
  if (items.some((p) => BUSY.has(p.status)) && !current) timer = setTimeout(loadList, 3000);
}

// vindo de "Importar…" de uma indicação: preenche o formulário de importação
function prefillImport() {
  const q = new URLSearchParams(location.search);
  const alvo = q.get("importar");
  if (!alvo) return;
  const parts = alvo.split("/");
  $("#imp-folder").value = parts.slice(0, -1).join("/");
  $("#imp-name").value = parts[parts.length - 1].replace(/\.[^.]+$/, "");
  $("#imp-msg").textContent = `Procure "${q.get("buscar") || ""}" no YouTube, cole o link e marque o trecho que quer usar.`;
  $("#import-box").scrollIntoView({ block: "center" });
  $("#imp-url").focus();
}

async function loadLibrary() {
  try {
    const lib = await api("/api/pos/biblioteca");
    $("#lib-dir").value = lib.dir;
    const mus = Object.entries(lib.musicas).map(([c, f]) => `${CLIMAS[c]} ${f.length}`).join(", ");
    $("#lib-summary").textContent = `${lib.sfx.length} efeitos sonoros · ${lib.emotes.length} emotes · músicas: ${mus}`;
    renderMemes(lib);
    prefillImport();
  } catch (ex) { $("#lib-summary").textContent = ex.message; }
}
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// tempo HH:MM:SS(.d), MM:SS ou segundos → segundos (NaN se inválido)
const parseHms = (v) => {
  v = String(v || "").trim().replace(",", ".");
  if (!/^\d+(\.\d+)?(:\d{1,2}(\.\d+)?){0,2}$/.test(v)) return NaN;
  return v.split(":").map(parseFloat).reduce((a, p) => a * 60 + p, 0);
};
function renderMemes(lib) {
  $("#meme-list").innerHTML = (lib.memes || []).map((m) => `
    <div class="meme ${m.tem ? "ok" : ""}">
      <div><b>${esc(m.nome)}</b> <span class="chip ${m.direitos === "alto" ? "warn" : ""}">direitos ${esc(m.direitos)}</span>
        ${m.tem ? `<span class="chip accent">na biblioteca</span>` : ""}</div>
      <div class="muted small">${esc(m.uso)}</div>
      <div class="muted small">${esc(m.origem)} · arquivo <code>${esc(m.arquivo)}</code></div>
      ${m.tem ? "" : `<button class="ghost small" data-meme="${esc(m.id)}">Importar este…</button>`}
    </div>`).join("");
  $$("#meme-list [data-meme]").forEach((b) => (b.onclick = () => {
    const m = lib.memes.find((x) => x.id === b.dataset.meme);
    const parts = m.arquivo.split("/");
    $("#imp-folder").value = parts.slice(0, -1).join("/");
    $("#imp-name").value = parts[parts.length - 1].replace(/\.[^.]+$/, "");
    $("#imp-msg").textContent = `Procure "${m.buscar}" no YouTube ou no TikTok, cole o link e marque o trecho do som.`;
    $("#import-box").scrollIntoView({ behavior: "smooth", block: "center" });
    $("#imp-url").focus();
  }));
}
$("#imp-go").onclick = async () => {
  const inicio = parseHms($("#imp-start").value), fim = parseHms($("#imp-end").value);
  if (isNaN(inicio) || isNaN(fim)) return ($("#imp-msg").textContent = "Use o formato HH:MM:SS (ex.: 00:00:03.5).");
  $("#imp-go").disabled = true;
  $("#imp-msg").textContent = "Baixando só o trecho e ajustando o volume…";
  try {
    const r = await postJSON("/api/pos/biblioteca/importar", { url: $("#imp-url").value, inicio, fim,
      pasta: $("#imp-folder").value, nome: $("#imp-name").value });
    $("#imp-msg").textContent = `Salvo em ${r.arquivo}.`;
    loadLibrary();
  } catch (ex) { $("#imp-msg").textContent = ex.message; }
  finally { $("#imp-go").disabled = false; }
};
$("#lib-save").onclick = async () => {
  try { await postJSON("/api/pos/biblioteca", { dir: $("#lib-dir").value }); loadLibrary(); }
  catch (ex) { alert(ex.message); }
};
$("#lib-open").onclick = () => postJSON("/api/pos/biblioteca/abrir", {}).catch((ex) => alert(ex.message));

$("#pos-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#form-error").textContent = "";
  const f = e.target;
  try {
    const p = await postJSON("/api/pos", { local_path: f.local_path.value, instructions: f.instructions.value });
    location.hash = `#/p/${p.id}`;
  } catch (ex) { $("#form-error").textContent = ex.message; }
};

// ---------------------------------------------------------------- projeto
async function openProject(id, first = false) {
  clearTimeout(timer);
  try { current = await api(`/api/pos/${id}`); }
  catch (ex) { $("#proj-status").textContent = ex.message; return; }
  renderProject(current, first);
  if (BUSY.has(current.status)) timer = setTimeout(() => openProject(id), 1500);
}

function renderProject(p, first) {
  $("#proj-title").textContent = prettyName(p.name);
  if (first) $("#src-video").src = `/api/pos/${p.id}/source`;
  const busy = BUSY.has(p.status);
  const st = $("#proj-status");
  st.innerHTML = `<div class="msg"></div>${busy ? `<div class="bar ${["planning", "queued", "watching"].includes(p.status) ? "indeterminate" : ""}"><i></i></div>` : ""}${p.error ? `<div class="err"></div>` : ""}`;
  const stage = STAGES[p.status] || p.status;
  $(".msg", st).textContent = p.message && p.message !== stage ? `${stage} — ${p.message}` : stage;
  if (busy) $(".bar > i", st).style.width = `${Math.round((p.progress || 0) * 100)}%`;
  if (p.error) $(".err", st).textContent = p.error;

  $("#plan").classList.toggle("hidden", !p.plan);
  $("#render-btn").disabled = busy;
  $("#replan-btn").disabled = busy;
  // não redesenha a tabela durante o render: o usuário pode estar olhando
  if (p.plan && (first || !busy || !$("#beats").children.length)) renderPlan(p.plan);

  const res = p.result;
  $("#results").classList.toggle("hidden", !res || p.status !== "done");
  if (res && p.status === "done") renderResults(p);
}

function renderPlan(plan) {
  $("#historia").textContent = plan.historia;
  $("#beats").replaceChildren(...plan.beats.map((b) => {
    const tr = document.createElement("tr");
    tr.dataset.id = b.id;
    tr.className = b.on ? "" : "off";
    tr.innerHTML = `<td><input type="checkbox"></td><td class="t-cell"><input class="t-in tc" maxlength="12" title="Tempo do efeito (HH:MM:SS.d): ajuste à mão"><button class="ghost icon-btn small t" title="Ver este momento">▶</button></td>
      <td class="what"></td><td class="edit"><b></b> <span class="detail"></span><div class="muted small why"></div></td>`;
    const box = $("input", tr);
    box.checked = b.on;
    box.onchange = () => tr.classList.toggle("off", !box.checked);
    $(".t-in", tr).value = sec(b.t);
    $(".t-in", tr).onchange = (e) => {
      const t = parseHms(e.target.value);
      e.target.classList.toggle("invalid", isNaN(t));
      if (!isNaN(t)) e.target.value = sec(t);
    };
    $(".t", tr).onclick = () => {
      const t = parseHms($(".t-in", tr).value);
      const v = $("#src-video"); v.currentTime = Math.max(0, (isNaN(t) ? b.t : t) - 1); v.play();
    };
    $(".what", tr).textContent = b.o_que;
    $(".edit b", tr).textContent = TIPOS[b.tipo] || b.tipo;
    $(".edit b", tr).className = `chip ${b.tipo === "sfx" ? "info" : b.tipo === "flash_texto" ? "warn" : "accent"}`;
    const detail = $(".detail", tr);
    if (b.tipo === "flash_texto" || b.tipo === "selo") {
      const input = document.createElement("input");
      input.className = "txt";
      input.value = b.texto;
      input.maxLength = b.tipo === "selo" ? 14 : 24;
      detail.replaceChildren(input);
    } else if (b.arquivo) {
      detail.textContent = b.arquivo;
    } else if (b.forca) {
      detail.textContent = b.tipo === "tremor" ? `${b.forca}px` : `${b.forca}x`;
    }
    $(".why", tr).textContent = [b.motivo, b.aviso && `Atenção: ${b.aviso}`].filter(Boolean).join(" · ");
    return tr;
  }));
  renderTrilha(plan);
  window.posPreview?.setPlan(plan);
  markMissing(plan);
}

// som/emote do plano que não está na biblioteca (ex.: os sintetizados que saíram): desliga e avisa
async function markMissing(plan) {
  let lib;
  try { lib = await api("/api/pos/biblioteca"); } catch { return; }
  const have = { sfx: new Set(lib.sfx), emote: new Set(lib.emotes) };
  for (const b of plan.beats) {
    if (!have[b.tipo] || have[b.tipo].has(b.arquivo)) continue;
    const tr = $(`#beats tr[data-id="${b.id}"]`);
    if (!tr) continue;
    $("input[type=checkbox]", tr).checked = false;
    tr.classList.add("off");
    $(".why", tr).textContent = `${b.arquivo} não está mais na biblioteca: importe um som real ou deixe desligado`;
  }
}

// ---- trilha: um cartão por trecho (o momento, a música escolhida ali e o que combina) + barra do clipe
const MOMENTO = { tensao: "Antes do lance", epico: "Jogada boa / virada", comico: "Falha / caos", triste: "Derrota",
  corte_seco: "Corte seco" };
const CLIMA_COR = { tensao: "var(--warn)", epico: "var(--accent)", comico: "var(--info)", triste: "#9a8cff", corte_seco: "var(--line-strong)" };
async function renderTrilha(plan) {
  $("#fim-note").textContent = `o vídeo tem ${sec(plan.fim)}`;
  const span = Math.max(0.1, plan.fim);
  $("#trilha-bar").innerHTML = plan.trilha.map((s) => `<i style="left:${(s.de / span) * 100}%;width:${((s.ate - s.de) / span) * 100}%;background:${CLIMA_COR[s.clima] || "var(--line-strong)"}"
    title="${esc(MOMENTO[s.clima] || s.clima)} · ${sec(s.de)} → ${sec(s.ate)}"></i>`).join("");
  let lib = { musicas: {} }, sug = {};
  try { [lib, sug] = await Promise.all([api("/api/pos/biblioteca"), api(`/api/pos/${current.id}/musicas`)]); } catch { /* sem biblioteca: só mudo */ }
  const options = (sel) => `<option value="">Sem música (silêncio)</option>` + Object.entries(lib.musicas).filter(([, fs]) => fs.length)
    .map(([c, fs]) => `<optgroup label="${esc(MOMENTO[c] || c)}">${fs.map((f) => `<option value="${esc(`${c}/${f}`)}"${`${c}/${f}` === sel ? " selected" : ""}>${esc(f)}</option>`).join("")}</optgroup>`).join("");
  const anyMusic = Object.values(lib.musicas).some((fs) => fs.length);
  const suggested = new Set();  // sugestões só no 1º trecho de cada tipo (sem repetir a mesma lista)
  $("#trilha").replaceChildren(...plan.trilha.map((s) => {
    const el = document.createElement("div");
    el.className = `tr-sec${s.on ? "" : " off"}`;
    el.dataset.id = s.id;
    el.style.setProperty("--c", CLIMA_COR[s.clima] || "var(--line-strong)");
    const seco = s.clima === "corte_seco";
    const sel = s.arquivo ? `${s.pasta || s.clima}/${s.arquivo}` : "";
    const picks = suggested.has(s.clima) ? [] : (sug[s.clima] || []).slice(0, 3);
    suggested.add(s.clima);
    el.innerHTML = `
      <label class="tr-top"><input type="checkbox" class="tr-on"${s.on ? " checked" : ""}>
        <b>${esc(MOMENTO[s.clima] || s.clima)}</b><span class="tc">${sec(s.de)} → ${sec(s.ate)}</span></label>
      ${seco ? `<p class="muted small">A música para de repente aqui: o silêncio dá impacto ao lance.</p>` : `
      <label class="tr-pick">Música <select class="tr-music">${options(sel)}</select></label>
      ${anyMusic ? "" : `<p class="muted small">Sua biblioteca ainda não tem músicas. Importe uma das sugestões abaixo.</p>`}
      ${picks.length ? `<div class="tr-sug"><span class="muted small">Combina aqui:</span>${picks.map((m) => `
        <span class="tr-song" title="${esc(m.momento)} · ${esc(m.como)}"><b>${esc(m.titulo)}</b> <span class="muted">${esc(m.artista)}</span>
          <span class="chip ${m.licenca === "livre" ? "accent" : "warn"}">${m.licenca === "livre" ? "livre" : "comercial"}</span>
          ${m.na_biblioteca ? "" : `<a class="ghost small" href="pos.html?importar=${encodeURIComponent(m.arquivo_sugerido)}&buscar=${encodeURIComponent(m.buscar)}">Importar</a>`}</span>`).join("")}</div>` : ""}`}`;
    $(".tr-on", el).onchange = (e) => el.classList.toggle("off", !e.target.checked);
    return el;
  }));
  window.posPreview?.setPlan(plan);
}

function renderResults(p) {
  const outs = [["com", "Com trilha"], ["sem", "Sem trilha (para a música do TikTok)"]]
    .filter(([k]) => p.result[k]);
  $("#outs").replaceChildren(...outs.map(([k, label]) => {
    const el = $("#out-tpl").content.firstElementChild.cloneNode(true);
    const r = p.result[k];
    const src = `/media/${r.file}?v=${p.rev}`;
    $("video", el).src = src;
    $(".out-name", el).textContent = label;
    $(".out-stats", el).textContent = r.lufs !== undefined ? ` · ${r.lufs} LUFS · pico ${r.peak} dB` : "";
    const dl = $(".dl", el);
    dl.href = src;
    dl.download = `${p.name.replace(/[^\p{L}\p{N} _-]/gu, "").slice(0, 60)} - ${k === "com" ? "com trilha" : "sem trilha"}.mp4`;
    return el;
  }));
}

$("#render-btn").onclick = async () => {
  $("#plan-error").textContent = "";
  const off = [...$("#beats").children].filter((tr) => !$("input[type=checkbox]", tr).checked).map((tr) => +tr.dataset.id);
  const offTrilha = $$("#trilha .tr-sec").filter((el) => !$(".tr-on", el).checked).map((el) => +el.dataset.id);
  const musicas = Object.fromEntries($$("#trilha .tr-sec").filter((el) => $(".tr-music", el)).map((el) => [el.dataset.id, $(".tr-music", el).value]));
  const textos = {}, tempos = {};
  [...$("#beats").children].forEach((tr) => {
    const t = $("input.txt", tr); if (t) textos[tr.dataset.id] = t.value;
    const tt = parseHms($("input.t-in", tr)?.value); if (!isNaN(tt)) tempos[tr.dataset.id] = tt;
  });
  try {
    await postJSON(`/api/pos/${current.id}/render`, { off, off_trilha: offTrilha, textos, tempos, musicas });
    openProject(current.id);
  } catch (ex) { $("#plan-error").textContent = ex.message; }
};
$("#replan-btn").onclick = async () => {
  $("#plan-error").textContent = "";
  try {
    await postJSON(`/api/pos/${current.id}/replan`, { instructions: $("#replan-text").value });
    $("#beats").replaceChildren();
    openProject(current.id);
  } catch (ex) { $("#plan-error").textContent = ex.message; }
};
$("#proj-delete").onclick = async () => {
  if (!confirm(`Excluir a pós-edição "${current.name}"? O vídeo original não é apagado.`)) return;
  await api(`/api/pos/${current.id}`, { method: "DELETE" });
  location.hash = "";
};

route();

// usados pelo preview (pospreview.js)
window.parseHms = parseHms;
window.sec = sec;
