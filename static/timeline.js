// Tempo no formato universal HH:MM:SS (ISO 8601). Com `decimals`, HH:MM:SS.d (décimo de segundo) para edição.
const pad2 = (n) => String(n).padStart(2, "0");
function fmtTime(s, decimals = false) {
  s = Math.max(0, Number(s) || 0);
  if (decimals) s = Math.round(s * 10) / 10; else s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s - h * 3600 - m * 60;
  return `${pad2(h)}:${pad2(m)}:${decimals ? sec.toFixed(1).padStart(4, "0") : pad2(Math.floor(sec))}`;
}
// Duração: curta em segundos ("36 s"), longa em HH:MM:SS
const fmtDur = (s) => (s < 60 ? `${Math.round(s)} s` : fmtTime(s));
// Aceita "1:02:03.5", "02:03", "123.4", "12,5" (vírgula decimal). Vazio = 0; texto inválido = NaN.
function parseTime(v) {
  v = String(v ?? "").trim().replace(",", ".");
  if (!v) return 0;
  if (!/^\d+(\.\d+)?(:\d{1,2}(\.\d+)?){0,2}$/.test(v)) return NaN;
  const parts = v.split(":").map(parseFloat);
  if (parts.slice(1).some((p) => p >= 60)) return NaN;  // 1:75 não é um tempo
  return parts.reduce((acc, part) => acc * 60 + part, 0);
}

// ---------------------------------------------------------------- linha do tempo com zoom
// Uso: new Timeline(elemento, { onSeek(t), onSelect(id), onTrim(id, s, e, final), onMark(s, e), onMarker(m, ev) })
// e depois .set({ start, end, segments: [{id, s, e, cls, label, editable}], markers: [{t, cls, title, label}],
//                 mark: {s, e} | null, selected }) e .setTime(t) para a cabeça de reprodução.
const TL_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
const TL_MAX_PPS = 60;  // zoom máximo: 60 px por segundo
const TL_SNAP_PX = 8;

class Timeline {
  constructor(root, opts = {}) {
    this.o = opts;
    this.root = root;
    this.d = { start: 0, end: 1, segments: [], markers: [], mark: null, selected: null };
    this.t = null;
    this.pps = 0;       // px por segundo atual
    this.fitted = true;  // ainda no "ver tudo" (refaz o ajuste quando a largura muda)
    root.classList.add("tlx");
    root.innerHTML = `
      <div class="tlx-bar">
        <button type="button" class="ghost small icon-btn" data-z="out" title="Diminuir o zoom (−)" aria-label="Diminuir o zoom">−</button>
        <input type="range" class="tlx-zoom" min="0" max="1000" value="0" aria-label="Zoom da linha do tempo">
        <button type="button" class="ghost small icon-btn" data-z="in" title="Aumentar o zoom (+)" aria-label="Aumentar o zoom">+</button>
        <button type="button" class="ghost small" data-z="fit" title="Ver o vídeo inteiro">Ver tudo</button>
        <button type="button" class="ghost small" data-z="sel" title="Zoom no trecho selecionado ou marcado">Zoom no trecho</button>
        <span class="tlx-scale muted small"></span>
        <span class="tlx-hover"></span>
      </div>
      <div class="tlx-view"><div class="tlx-canvas">
        <div class="tlx-ruler"></div><div class="tlx-items"></div><div class="tlx-ghost hidden"></div>
        <div class="tlx-head hidden"><span></span></div>
      </div></div>
      <div class="tlx-over" title="Visão geral: arraste para navegar"><div class="tlx-over-items"></div><div class="tlx-over-win"></div><div class="tlx-over-head hidden"></div></div>`;
    this.$ = (sel) => root.querySelector(sel);
    this.view = this.$(".tlx-view");
    this.canvas = this.$(".tlx-canvas");
    this.zoom = this.$(".tlx-zoom");
    this.root.querySelectorAll("[data-z]").forEach((b) => (b.onclick = () => this.zoomCmd(b.dataset.z)));
    this.zoom.oninput = () => this.setPps(this.sliderToPps(+this.zoom.value), this.view.clientWidth / 2);
    this.view.addEventListener("scroll", () => this.drawOverview());
    this.view.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    this.canvas.addEventListener("pointerdown", (e) => this.onDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.onHover(e));
    this.canvas.addEventListener("pointerleave", () => (this.$(".tlx-hover").textContent = ""));
    this.$(".tlx-over").addEventListener("pointerdown", (e) => this.onOverview(e));
    this.ro = new ResizeObserver(() => { if (this.fitted) this.fit(); else this.render(); });
    this.ro.observe(this.view);
  }

  get span() { return Math.max(1, this.d.end - this.d.start); }
  get fitPps() { return Math.max(0.001, (this.view.clientWidth - 2) / this.span); }
  x(t) { return (t - this.d.start) * this.pps; }
  tAt(clientX) {
    const r = this.canvas.getBoundingClientRect();
    return Math.min(this.d.end, Math.max(this.d.start, this.d.start + (clientX - r.left) / this.pps));
  }
  sliderToPps(v) { return this.fitPps * Math.pow(TL_MAX_PPS / this.fitPps, v / 1000); }
  ppsToSlider(p) {
    const r = Math.log(p / this.fitPps) / Math.log(TL_MAX_PPS / this.fitPps);
    return Math.round(Math.min(1, Math.max(0, r || 0)) * 1000);
  }

  set(data) {
    const first = data.start !== this.d.start || data.end !== this.d.end;
    this.d = { ...this.d, ...data };
    if (first || !this.pps) this.fit(); else this.render();
  }
  fit() { this.fitted = true; this.pps = this.fitPps; this.render(); this.view.scrollLeft = 0; }
  setPps(p, anchorPx) {
    const tAnchor = this.d.start + (this.view.scrollLeft + anchorPx) / this.pps;
    this.pps = Math.min(TL_MAX_PPS, Math.max(this.fitPps, p));
    this.fitted = this.pps <= this.fitPps * 1.001;
    this.render();
    this.view.scrollLeft = this.x(tAnchor) - anchorPx;  // o ponto sob o cursor fica parado
    this.drawOverview();
  }
  zoomCmd(z) {
    const mid = this.view.clientWidth / 2;
    if (z === "in") this.setPps(this.pps * 2, this.headAnchor() ?? mid);
    else if (z === "out") this.setPps(this.pps / 2, this.headAnchor() ?? mid);
    else if (z === "fit") this.fit();
    else if (z === "sel") {
      const seg = this.d.segments.find((s) => s.id === this.d.selected);
      const r = seg ? { s: seg.s, e: seg.e } : this.d.mark;
      if (r) this.zoomTo(r.s, r.e);
    }
  }
  headAnchor() {  // zoom pelos botões mantém a cabeça de reprodução no lugar, se ela estiver visível
    if (this.t == null) return null;
    const px = this.x(this.t) - this.view.scrollLeft;
    return px >= 0 && px <= this.view.clientWidth ? px : null;
  }
  zoomTo(s, e) {
    const pad = Math.max(2, (e - s) * 0.25);
    this.pps = Math.min(TL_MAX_PPS, Math.max(this.fitPps, this.view.clientWidth / (e - s + 2 * pad)));
    this.fitted = false;
    this.render();
    this.view.scrollLeft = this.x(s - pad);
    this.drawOverview();
  }
  reveal(t) {  // mantém o tempo `t` visível (ex.: vídeo tocando)
    const px = this.x(t), l = this.view.scrollLeft, w = this.view.clientWidth;
    if (px < l || px > l + w - 40) this.view.scrollLeft = px - w * 0.2;
  }

  render() {
    const { start, end } = this.d;
    const width = Math.max(this.view.clientWidth - 2, Math.ceil(this.span * this.pps));
    this.canvas.style.width = `${width}px`;
    this.zoom.value = this.ppsToSlider(this.pps);
    // régua: passo que deixa ~90 px entre os rótulos
    const step = TL_STEPS.find((s) => s * this.pps >= 90) || TL_STEPS[TL_STEPS.length - 1];
    const minor = TL_STEPS.slice().reverse().find((s) => s < step && step % s === 0 && s * this.pps >= 12);
    const ticks = [];
    const first = Math.ceil(start / (minor || step)) * (minor || step);
    for (let t = first; t <= end; t += minor || step) {
      const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
      ticks.push(`<span class="tlx-tick${major ? " major" : ""}" style="left:${this.x(t)}px">${major ? fmtTime(t) : ""}</span>`);
    }
    this.$(".tlx-ruler").innerHTML = ticks.join("");
    this.$(".tlx-scale").textContent = step >= 60 ? `marcas a cada ${step / 60} min` : `marcas a cada ${step} s`;
    const items = [];
    for (const s of this.d.segments) {
      const w = Math.max(3, (s.e - s.s) * this.pps);
      items.push(`<div class="tlx-seg ${s.cls || ""}${s.id === this.d.selected ? " sel" : ""}${s.editable ? " edit" : ""}"
        data-id="${s.id}" style="left:${this.x(s.s)}px;width:${w}px" title="${escapeHtml(`${s.label || ""} · ${fmtTime(s.s)} → ${fmtTime(s.e)}`)}">
        ${w > 46 ? `<span class="tlx-seg-label">${escapeHtml(s.label || "")}</span>` : ""}
        ${s.editable && s.id === this.d.selected ? `<i class="tlx-h l" data-h="s"></i><i class="tlx-h r" data-h="e"></i>` : ""}</div>`);
    }
    this.d.markers.forEach((m, i) => {
      items.push(`<button type="button" class="tlx-mk ${m.cls || ""}${m.label ? " has-label" : ""}" data-mk="${i}" style="left:${this.x(m.t)}px"
        title="${escapeHtml(`${m.title || ""} · ${fmtTime(m.t)}`)}">${m.label ? `<span>${escapeHtml(m.label)}</span>` : ""}</button>`);
    });
    if (this.d.mark && this.d.mark.e > this.d.mark.s) {
      const { s, e } = this.d.mark;
      items.push(`<div class="tlx-mark" style="left:${this.x(s)}px;width:${(e - s) * this.pps}px"><span>${fmtTime(s)} → ${fmtTime(e)}</span></div>`);
    }
    this.$(".tlx-items").innerHTML = items.join("");
    this.drawHead();
    this.drawOverview();
  }
  drawOverview() {
    const W = this.$(".tlx-over").clientWidth || 1, k = W / this.span;
    const segs = this.d.segments.map((s) => `<i class="${s.cls || ""}" style="left:${(s.s - this.d.start) * k}px;width:${Math.max(2, (s.e - s.s) * k)}px"></i>`);
    const mks = this.d.markers.filter((m) => m.cls && !/match/.test(m.cls)).map((m) => `<b class="${m.cls}" style="left:${(m.t - this.d.start) * k}px"></b>`);
    this.$(".tlx-over-items").innerHTML = segs.join("") + mks.join("");
    const win = this.$(".tlx-over-win");
    const total = this.canvas.offsetWidth || 1;
    win.style.left = `${(this.view.scrollLeft / total) * W}px`;
    win.style.width = `${Math.max(6, (this.view.clientWidth / total) * W)}px`;
    if (this.t != null) {
      const h = this.$(".tlx-over-head");
      h.classList.remove("hidden");
      h.style.left = `${(this.t - this.d.start) * k}px`;
    }
  }
  setTime(t, follow = false) {
    this.t = t;
    this.drawHead();
    if (follow) this.reveal(t);
    const h = this.$(".tlx-over-head");
    if (t != null) { h.classList.remove("hidden"); h.style.left = `${((t - this.d.start) / this.span) * (this.$(".tlx-over").clientWidth || 1)}px`; }
  }
  drawHead() {
    const head = this.$(".tlx-head");
    if (this.t == null) { head.classList.add("hidden"); return; }
    head.classList.remove("hidden");
    head.style.left = `${this.x(this.t)}px`;
    head.firstElementChild.textContent = fmtTime(this.t, true);
  }

  snap(t, ev, skip) {
    if (ev.altKey) return t;
    const cands = [...this.d.markers.map((m) => m.t), ...(this.t != null ? [this.t] : [])];
    for (const s of this.d.segments) if (s.id !== skip) cands.push(s.s, s.e);
    let best = t, dist = TL_SNAP_PX / this.pps;
    for (const c of cands) if (Math.abs(c - t) < dist) { best = c; dist = Math.abs(c - t); }
    return best;
  }
  onHover(e) { this.$(".tlx-hover").textContent = fmtTime(this.tAt(e.clientX), true); }
  onWheel(e) {
    if (!(e.ctrlKey || e.metaKey)) return;  // roda sem Ctrl rola a página; Shift + roda rola a linha do tempo
    e.preventDefault();
    const r = this.view.getBoundingClientRect();
    this.setPps(this.pps * (e.deltaY < 0 ? 1.25 : 0.8), e.clientX - r.left);
  }
  onOverview(e) {
    const over = this.$(".tlx-over"), r = over.getBoundingClientRect();
    const move = (ev) => {
      const t = this.d.start + ((ev.clientX - r.left) / r.width) * this.span;
      this.view.scrollLeft = this.x(t) - this.view.clientWidth / 2;
    };
    move(e);
    try { over.setPointerCapture(e.pointerId); } catch { /* ponteiro já solto */ }
    over.onpointermove = move;
    over.onpointerup = () => { over.onpointermove = null; over.onpointerup = null; };
  }
  onDown(e) {
    if (e.button !== 0) return;
    const handle = e.target.closest(".tlx-h"), segEl = e.target.closest(".tlx-seg"), mk = e.target.closest(".tlx-mk");
    const onRuler = e.target.closest(".tlx-ruler") || e.target.closest(".tlx-head");
    const cap = (moveFn, upFn) => {
      try { this.canvas.setPointerCapture(e.pointerId); } catch { /* ponteiro já solto */ }
      this.canvas.onpointermove = (ev) => { this.onHover(ev); moveFn(ev); };
      this.canvas.onpointerup = (ev) => { this.canvas.onpointermove = null; this.canvas.onpointerup = null; upFn?.(ev); };
    };
    e.preventDefault();
    if (handle && segEl) {  // arrastar a borda do lance
      const seg = this.d.segments.find((s) => String(s.id) === segEl.dataset.id);
      const side = handle.dataset.h;
      cap((ev) => {
        const t = this.snap(this.tAt(ev.clientX), ev, seg.id);
        if (side === "s") seg.s = Math.min(t, seg.e - 1); else seg.e = Math.max(t, seg.s + 1);
        this.render();
        this.o.onTrim?.(seg.id, seg.s, seg.e, false);
      }, () => this.o.onTrim?.(seg.id, seg.s, seg.e, true));
      return;
    }
    if (mk) { const m = this.d.markers[+mk.dataset.mk]; if (this.o.onMarker) this.o.onMarker(m, e); else this.o.onSeek?.(m.t); return; }
    if (segEl) { this.o.onSelect?.(isNaN(+segEl.dataset.id) ? segEl.dataset.id : +segEl.dataset.id, e); return; }
    if (onRuler) {  // régua: arrastar = navegar no vídeo
      const go = (ev) => this.o.onSeek?.(this.tAt(ev.clientX));
      go(e);
      cap(go);
      return;
    }
    // área vazia: clique = ir ao ponto; arrastar = marcar um trecho novo
    const t0 = this.snap(this.tAt(e.clientX), e), x0 = e.clientX;
    const ghost = this.$(".tlx-ghost");
    let moved = false;
    cap((ev) => {
      if (Math.abs(ev.clientX - x0) < 4 && !moved) return;
      moved = true;
      const t1 = this.snap(this.tAt(ev.clientX), ev);
      const s = Math.min(t0, t1), en = Math.max(t0, t1);
      ghost.classList.remove("hidden");
      ghost.style.left = `${this.x(s)}px`; ghost.style.width = `${(en - s) * this.pps}px`;
      ghost.textContent = `${fmtTime(s)} → ${fmtTime(en)} · ${fmtDur(en - s)}`;
    }, (ev) => {
      ghost.classList.add("hidden");
      if (!moved) { this.o.onSeek?.(t0, ev); return; }
      const t1 = this.snap(this.tAt(ev.clientX), ev);
      const s = Math.min(t0, t1), en = Math.max(t0, t1);
      if (en - s >= 1) this.o.onMark?.(s, en, ev);
    });
  }
}
