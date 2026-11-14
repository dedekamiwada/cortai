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
const sec = (t) => `${t.toFixed(1).replace(".", ",")} s`;

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

async function loadLibrary() {
  try {
    const lib = await api("/api/pos/biblioteca");
    $("#lib-dir").value = lib.dir;
    const mus = Object.entries(lib.musicas).map(([c, f]) => `${CLIMAS[c]} ${f.length}`).join(", ");
    $("#lib-summary").textContent = `${lib.sfx.length} efeitos sonoros · ${lib.emotes.length} emotes · músicas: ${mus}`;
  } catch (ex) { $("#lib-summary").textContent = ex.message; }
}
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
    tr.innerHTML = `<td><input type="checkbox"></td><td><button class="ghost t"></button></td>
      <td class="what"></td><td class="edit"><b></b> <span class="detail"></span><div class="muted small why"></div></td>`;
    const box = $("input", tr);
    box.checked = b.on;
    box.onchange = () => tr.classList.toggle("off", !box.checked);
    $(".t", tr).textContent = sec(b.t);
    $(".t", tr).onclick = () => { const v = $("#src-video"); v.currentTime = Math.max(0, b.t - 1); v.play(); };
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
  $("#trilha").replaceChildren(...plan.trilha.map((s) => {
    const tr = document.createElement("tr");
    tr.dataset.id = s.id;
    tr.className = s.on ? "" : "off";
    const fonte = s.fonte === "gerada" ? "trilha original gerada" : s.fonte === "mudo" ? "sem música na biblioteca (mudo)" : s.arquivo || "";
    tr.innerHTML = `<td><input type="checkbox"></td><td>${sec(s.de)} → ${sec(s.ate)}</td><td><b></b></td><td class="muted small"></td>`;
    const box = $("input", tr);
    box.checked = s.on;
    box.onchange = () => tr.classList.toggle("off", !box.checked);
    $("b", tr).textContent = CLIMAS[s.clima] || s.clima;
    $("td.muted", tr).textContent = fonte;
    return tr;
  }));
  $("#fim-note").textContent = `O vídeo termina em ${sec(plan.fim)}.`;
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
  const offTrilha = [...$("#trilha").children].filter((tr) => !$("input", tr).checked).map((tr) => +tr.dataset.id);
  const textos = {};
  [...$("#beats").children].forEach((tr) => { const t = $("input.txt", tr); if (t) textos[tr.dataset.id] = t.value; });
  try {
    await postJSON(`/api/pos/${current.id}/render`, { off, off_trilha: offTrilha, textos });
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
