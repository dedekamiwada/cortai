// Editor de capa (thumbnail) compartilhado por Cortes (clipe) e Pós-edição (vídeo editado). O diálogo é criado
// aqui; a página só precisa de $, $$, api e postJSON. Abra com openCover(base, opts).
(() => {
  if (!document.getElementById("cover-dlg")) document.body.insertAdjacentHTML("beforeend", `
<dialog id="cover-dlg">
  <div class="ed-head">
    <b class="cv-head-title"><i class="ic ic-image"></i><span id="cv-heading">Capa do clipe</span></b>
    <span class="muted small" id="cv-sub"></span>
    <button class="ghost icon-btn" id="cv-close" aria-label="Fechar" title="Fechar"><i class="ic ic-x"></i></button>
  </div>
  <div id="cv-loading" class="ed-loading">Preparando a capa…</div>
  <div id="cv-body" class="cv-body hidden">
    <div class="cv-left">
      <div class="cv-stage" id="cv-stage" title="Arraste para cima ou para baixo para mover o texto">
        <img id="cv-img" alt="Prévia da capa">
        <div class="cv-safe" title="O perfil do TikTok mostra só esta parte (3:4)"></div>
        <div class="cv-busy hidden" id="cv-busy">…</div>
      </div>
      <p class="muted small">Arraste na imagem para mover o texto. A área clara é o que aparece no perfil do TikTok; embaixo fica o título automático do app.</p>
    </div>
    <div class="cv-right">
      <div class="ed-section">
        <h4>Texto</h4>
        <textarea id="cv-text" rows="3" maxlength="200"></textarea>
        <p class="muted small">Ponha *asteriscos* em volta da palavra de destaque. Enter quebra a linha.</p>
        <div class="cv-chips" id="cv-chips"></div>
        <div class="ed-row">
          <select id="cv-style"></select>
          <label class="check"><input id="cv-upper" type="checkbox"> CAIXA ALTA</label>
        </div>
        <label class="ed-range"><span class="cv-rhead">Tamanho <b class="cv-val" data-for="cv-size"></b></span><input id="cv-size" type="range" min="0.4" max="1.8" step="0.05"></label>
        <label class="ed-range"><span class="cv-rhead">Inclinação <b class="cv-val" data-for="cv-tilt"></b></span><input id="cv-tilt" type="range" min="-15" max="15" step="1"></label>
        <label class="ed-range"><span class="cv-rhead">Sombra atrás do texto <b class="cv-val" data-for="cv-dark"></b></span><input id="cv-dark" type="range" min="0" max="1" step="0.05"></label>
        <label class="ed-range"><span class="cv-rhead">Desfoque da imagem <b class="cv-val" data-for="cv-blur"></b></span><input id="cv-blur" type="range" min="0" max="1" step="0.05"></label>
        <div class="ed-row cv-colors">
          <label>Texto <input id="cv-color" type="color"></label>
          <label>Destaque <input id="cv-hl" type="color"></label>
          <button class="ghost small" id="cv-colors-reset" type="button">Cores do estilo</button>
        </div>
        <label class="ed-range">Assinatura <input id="cv-handle" maxlength="40" placeholder="@seu_perfil"></label>
      </div>
      <div class="ed-section">
        <div class="ed-words-head">
          <h4>Imagem</h4>
          <button class="ghost small" id="cv-apex" type="button" title="Voltar para o quadro que o Cortaí escolheu"><i class="ic ic-star"></i>Momento ápice</button>
        </div>
        <div class="cv-frames" id="cv-frames"></div>
        <p class="muted small hidden" id="cv-origin"></p>
        <label class="ed-range"><span class="cv-rhead">Zoom <b class="cv-val" data-for="cv-zoom"></b></span><input id="cv-zoom" type="range" min="1" max="3" step="0.05"></label>
        <div class="ed-row">
          <label class="ed-range cv-half"><span class="cv-rhead">Esquerda ↔ direita <b class="cv-val" data-for="cv-px"></b></span><input id="cv-px" type="range" min="0" max="1" step="0.01"></label>
          <label class="ed-range cv-half"><span class="cv-rhead">Cima ↕ baixo <b class="cv-val" data-for="cv-py"></b></span><input id="cv-py" type="range" min="0" max="1" step="0.01"></label>
        </div>
      </div>
      <div class="ed-section">
        <label class="check"><input id="cv-invideo" type="checkbox"> Gravar a capa no fim do vídeo (0,4 s)</label>
        <p class="muted small">O TikTok deixa subir a capa no "Editar capa" → "Enviar". Se preferir escolher pelo vídeo, ligue isto e arraste o seletor até o fim.</p>
        <label class="check"><input id="cv-default" type="checkbox"> Usar estilo e assinatura como padrão nos próximos projetos</label>
      </div>
    </div>
  </div>
  <div class="ed-foot">
    <p class="form-error" id="cv-error"></p>
    <a class="ghost" id="cv-download" download><i class="ic ic-download"></i>Baixar capa</a>
    <button class="ghost" id="cv-cancel">Cancelar</button>
    <button class="primary" id="cv-save">Salvar capa</button>
  </div>
</dialog>
`);
})();
const cdlg = document.getElementById("cover-dlg");
let cv = null;  // { base, opts, info, p (parâmetros), timer, abort, url }
const CV_FIELDS = { text: "#cv-text", style: "#cv-style", upper: "#cv-upper", size: "#cv-size", tilt: "#cv-tilt",
  dark: "#cv-dark", blur: "#cv-blur", handle: "#cv-handle", zoom: "#cv-zoom", px: "#cv-px", py: "#cv-py" };

// base: "/api/jobs/<id>/clips/<n>" (clipe de corte) ou "/api/pos/<id>" (vídeo pós-editado)
// opts: { download, onSaved(), onDefault(params) | null, inVideo: true/false (mostra a opção de gravar no vídeo) }
async function openCover(base, opts = {}) {
  cv = null;
  $("#cv-invideo").closest("label").classList.toggle("hidden", opts.inVideo === false);
  $("#cv-default").closest("label").classList.toggle("hidden", !opts.onDefault);
  $("#cv-heading").textContent = opts.heading || "Capa do clipe";
  $("#cv-error").textContent = "";
  $("#cv-loading").textContent = "Preparando a capa…";
  $("#cv-loading").classList.remove("hidden");
  $("#cv-body").classList.add("hidden");
  $("#cv-save").disabled = true;
  cdlg.showModal();
  let info;
  try {
    info = await api(`${base}/capa`);
    if (!info.cover) {
      $("#cv-loading").textContent = "Escolhendo o momento ápice e fazendo a capa…";
      info = await postJSON(`${base}/capa`, {});
    }
  } catch (ex) {
    $("#cv-loading").textContent = `Não deu para abrir a capa: ${ex.message}`;
    return;
  }
  cv = { base, opts, info, p: { ...info.cover }, timer: null, abort: null, url: null };
  $("#cv-sub").textContent = info.title;
  $("#cv-style").innerHTML = Object.entries(info.styles).map(([k, label]) => `<option value="${k}">${label}</option>`).join("");
  for (const [k, sel] of Object.entries(CV_FIELDS)) {
    const el = $(sel);
    if (el.type === "checkbox") el.checked = cv.p[k] !== false;
    else el.value = cv.p[k] ?? (el.type === "range" ? 0 : "");  // capas antigas não têm "blur"
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
    editado: "Quadros do vídeo já editado, com os efeitos e a legenda gravados.",
    clipe: "Quadros tirados do clipe pronto, com a legenda dele. Salve o clipe no editor de novo para ter quadros limpos." }[info.origin];
  $("#cv-origin").textContent = origin || "";
  $("#cv-origin").classList.toggle("hidden", !origin);
  renderCoverFrames();
  setCoverImage(`/media/${info.file}?v=${info.rev}`);
  $("#cv-download").href = `/media/${info.file}?v=${info.rev}`;
  $("#cv-download").download = opts.download || "capa.jpg";
  $("#cv-loading").classList.add("hidden");
  $("#cv-body").classList.remove("hidden");
  $("#cv-save").disabled = false;
}

// número ao lado de cada barra do editor de capa
const CV_FORMAT = {
  "cv-size": (v) => `${Math.round(v * 100)}%`,
  "cv-tilt": (v) => `${v > 0 ? "+" : ""}${Math.round(v)}°`,
  "cv-dark": (v) => `${Math.round(v * 100)}%`,
  "cv-blur": (v) => (+v ? `${Math.round(v * 100)}%` : "sem"),
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
      const res = await fetch(`${cv.base}/capa/previa`, {
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
    const body = { ...cv.p };
    if (cv.opts.inVideo !== false) body.in_video = $("#cv-invideo").checked;
    delete body.apex; delete body.frames; delete body.auto_text; delete body.custom_text; delete body.custom_frame;
    await postJSON(`${cv.base}/capa`, body);
    if (cv.opts.onDefault && $("#cv-default").checked) await cv.opts.onDefault({ ...body });
    const done = cv.opts.onSaved;
    closeCover();
    done?.();
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
