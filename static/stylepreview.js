// Prévia do estilo antes de renderizar: um trecho real de um lance num quadro 9:16, com a legenda (mesmo
// agrupamento do captions.py), cor, posição, caixa e o título como vão sair. Usado na aba Estilo/"Estilo de
// todos" e na aba Legendas do resumo. Só lê dados do editor do lance (prévia leve já gerada): não gasta tokens.
class StylePreview {
  constructor(root, { fields, fixes } = {}) {
    this.root = root;
    this.fields = fields;               // () => opções atuais do formulário
    this.extraFixes = fixes || (() => ({}));  // () => {chave_da_palavra: texto} (legendas editadas ainda não guardadas)
    this.data = null; this.key = null; this.raf = 0;
    root.classList.add("spv");
    root.innerHTML = `
      <div class="spv-head"><span class="muted small">Prévia</span><select class="spv-pick" aria-label="Lance da prévia"></select></div>
      <div class="spv-stage"><video playsinline muted preload="auto"></video><div class="spv-title"></div><div class="spv-cap"></div></div>
      <div class="spv-bar"><button class="ghost small spv-play">Tocar</button><span class="tc spv-time"></span>
        <label class="check small"><input type="checkbox" class="spv-sound"> som</label></div>`;
    this.video = root.querySelector("video");
    this.cap = root.querySelector(".spv-cap");
    this.titleEl = root.querySelector(".spv-title");
    this.pick = root.querySelector(".spv-pick");
    root.querySelector(".spv-play").onclick = () => (this.video.paused ? this.play() : this.video.pause());
    root.querySelector(".spv-sound").onchange = (e) => (this.video.muted = !e.target.checked);
    this.pick.onchange = () => this.load(this.jobId, +this.pick.value);
    this.video.addEventListener("play", () => { root.querySelector(".spv-play").textContent = "Pausar"; this.loop(); });
    this.video.addEventListener("pause", () => { root.querySelector(".spv-play").textContent = "Tocar"; cancelAnimationFrame(this.raf); });
    this.video.addEventListener("timeupdate", () => this.draw());
    this.video.addEventListener("loadedmetadata", () => { this.seek(this.target ?? this.clip.start + 0.05); this.target = null; });
    this.video.addEventListener("seeked", () => this.draw());
  }
  async show(job, n) {
    this.jobId = job.id;
    const clips = job.clips.filter((c) => ["done", "staged"].includes(c.status)).sort((a, b) => a.start - b.start);
    if (!clips.length) { this.root.classList.add("hidden"); return; }
    this.root.classList.remove("hidden");
    const want = n ?? (clips.find((c) => c.n === +this.pick.value) || clips[0]).n;
    this.pick.innerHTML = clips.map((c) => `<option value="${c.n}">${escapeHtml(c.title || `Lance ${c.n}`)}</option>`).join("");
    this.pick.value = String(want);
    if (this.key !== `${job.id}:${want}`) await this.load(job.id, want);
    else this.draw();
  }
  async load(jobId, n) {
    this.key = `${jobId}:${n}`;
    try { this.data = await api(`/api/jobs/${jobId}/clips/${n}/editor`); } catch { return; }
    this.clip = this.data.clip;
    const [ws] = this.data.window;
    this.ws = ws;
    this.video.src = `/media/${this.data.proxy}`;
    const vertical = this.data.source.width / this.data.source.height < 0.6;
    this.root.querySelector(".spv-stage").dataset.src = vertical ? "vertical" : "wide";
  }
  seek(t) { if (this.data) this.video.currentTime = Math.max(0, t - this.ws); }
  at(t) {  // vai a um segundo da live; se o vídeo do lance ainda carrega, vai quando ele estiver pronto
    if (this.video.readyState < 1) { this.target = t; return; }
    this.seek(t); this.draw();
  }
  play() {
    const t = this.ws + this.video.currentTime;
    if (t < this.clip.start || t > this.clip.end - 0.2) this.seek(this.clip.start);
    this.video.play();
  }
  loop() { this.draw(); if (!this.video.paused) this.raf = requestAnimationFrame(() => this.loop()); }
  draw() {
    if (!this.data) return;
    const o = { ...this.data.options, ...(this.fields?.() || {}) };
    const t = this.ws + this.video.currentTime;
    if (!this.video.paused && t >= this.clip.end) this.seek(this.clip.start);  // repete o trecho do lance
    this.root.querySelector(".spv-time").textContent = fmtTime(Math.max(0, t - this.clip.start), true);
    const stage = this.root.querySelector(".spv-stage");
    stage.dataset.layout = o.layout === "fit" ? "fit" : "cover";
    // legenda (o mesmo agrupamento do captions.py)
    const [maxWords, maxChars] = PRESET_CHUNK[o.caption_preset] || [3, 18];
    const fixes = { ...(o.word_fixes || {}), ...this.extraFixes() };
    const words = this.data.words.map((w) => {
      const k = w.s.toFixed(2);
      return { ...w, w: k in fixes ? fixes[k] : w.w };
    }).filter((w) => w.w && w.e > this.clip.start && w.s < this.clip.end);
    const chunks = [];
    let cur = [];
    for (const w of words) {
      if (cur.length) {
        const len = cur.reduce((a, x) => a + x.w.length + 1, 0) + w.w.length;
        const gap = w.s - cur[cur.length - 1].e;
        if (cur.length >= maxWords || len > maxChars || gap > 0.5 || /[.?!…]$/.test(cur[cur.length - 1].w)) { chunks.push(cur); cur = []; }
      }
      cur.push(w);
    }
    if (cur.length) chunks.push(cur);
    let html = "";
    chunks.forEach((chunk, ci) => {
      const next = chunks[ci + 1] ? chunks[ci + 1][0].s : this.clip.end;
      const end = Math.min(next, chunk[chunk.length - 1].e + 0.35);
      if (t < chunk[0].s || t >= end) return;
      const active = chunk.findIndex((w, i) => t < (chunk[i + 1] ? chunk[i + 1].s : end));
      html = chunk.map((w, i) => {
        const txt = escapeHtml(w.w);
        if (i !== active || o.caption_preset === "minimal") return txt;
        return o.caption_preset === "marcador" ? `<b class="box">${txt}</b>` : `<b>${txt}</b>`;
      }).join(" ");
    });
    const cap = this.cap;
    cap.className = `spv-cap ${o.caption_position || "bottom"} p-${o.caption_preset || "destaque"}`;
    cap.style.setProperty("--hi", o.caption_color || "#FFE600");
    cap.style.textTransform = o.caption_upper === false ? "none" : "uppercase";
    cap.innerHTML = o.captions === false ? "" : html;
    // título
    const showTitle = o.title_overlay && (o.title_mode !== "hook" || t - this.clip.start < 3);
    this.titleEl.className = `spv-title ${o.title_position || "top"} s-${o.title_style || "impacto"}`;
    this.titleEl.textContent = showTitle ? (o.title_text || this.clip.title || "") : "";
  }
}
