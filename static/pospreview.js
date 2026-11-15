// Preview ao vivo da pós-edição: o vídeo original num quadro 9:16 com os efeitos do plano simulados por cima
// (mesmas fórmulas do render do ffmpeg em posedit.py), efeitos sonoros e trilha nos tempos certos.
// Lê o estado da TABELA (ligado/desligado, texto e tempo editados), então mostra o que vai sair antes de
// renderizar — e ajustar à mão não gasta tokens (só "Pedir outro plano" chama o Claude).
(() => {
  const W = 1080, H = 1920;
  const video = document.getElementById("src-video");
  const stage = document.getElementById("pv-stage");
  const layer = document.getElementById("pv-layer");
  const onBox = document.getElementById("pv-on");
  let plan = null, raf = 0, lastT = 0;
  const audioCache = new Map();
  const music = new Map();  // id da seção → <audio>

  const libUrl = (path) => `/api/pos/biblioteca/arquivo?p=${encodeURIComponent(path)}`;
  const clip01 = (v) => Math.min(1, Math.max(0, v));
  // envelope 0→1→0 com curva de cosseno, igual a _smooth() do render
  const smooth = (t, a, up, b, down) => 0.5 - 0.5 * Math.cos(Math.PI * Math.min(clip01((t - a) / up), clip01((b - t) / down)));

  function beats() {
    if (!plan) return [];
    const rows = new Map([...document.querySelectorAll("#beats tr")].map((tr) => [+tr.dataset.id, tr]));
    return plan.beats.map((b) => {
      const tr = rows.get(b.id);
      if (!tr) return b;
      const on = tr.querySelector("input[type=checkbox]").checked;
      const txt = tr.querySelector("input.txt");
      const tIn = tr.querySelector("input.t-in");
      const t = tIn ? window.parseHms?.(tIn.value) : NaN;
      return { ...b, on, texto: txt ? txt.value.toUpperCase() : b.texto, t: Number.isFinite(t) ? t : b.t };
    }).filter((b) => b.on);
  }
  function sections() {
    if (!plan) return [];
    const rows = new Map([...document.querySelectorAll("#trilha .tr-sec")].map((el) => [+el.dataset.id, el]));
    return plan.trilha.map((s) => {  // a música escolhida no cartão (pode ser de outra pasta de clima)
      const el = rows.get(s.id), pick = el?.querySelector(".tr-music")?.value ?? (s.arquivo ? `${s.pasta || s.clima}/${s.arquivo}` : "");
      return { ...s, on: el?.querySelector(".tr-on")?.checked ?? s.on, file: pick };
    }).filter((s) => s.on && s.file && s.clima !== "corte_seco");
  }

  function frame(t) {
    const list = onBox.checked ? beats() : [];
    // zoom e tremor (motion)
    let z = 1, wsum = 0, fx = 0, fy = 0, dx = 0, dy = 0;
    for (const b of list) {
      if (b.tipo === "zoom_punch" || b.tipo === "zoom_lento") {
        const env = b.tipo === "zoom_punch"
          ? smooth(t, b.t, 0.08, b.t + b.dur + 0.15, 0.15)
          : smooth(t, b.t, Math.max(0.5, b.dur - 0.4), b.t + b.dur + 0.8, 0.8);
        z *= 1 + (b.forca - 1) * env;
        wsum += env; fx += b.foco_x * env; fy += b.foco_y * env;
      } else if (b.tipo === "tremor" && t >= b.t && t <= b.t + b.dur) {
        const decay = 1 - (t - b.t) / b.dur;
        z *= 1.07;
        dy += b.forca * decay * Math.sin(2 * Math.PI * 14 * t);
        dx += b.forca * 0.4 * decay * Math.cos(2 * Math.PI * 9 * t);
      }
    }
    const ox = wsum > 0.001 ? fx / wsum : 0.5, oy = wsum > 0.001 ? fy / wsum : 0.5;
    const k = stage.clientWidth / W;
    video.style.transformOrigin = `${ox * 100}% ${oy * 100}%`;
    video.style.transform = z !== 1 || dx || dy ? `translate(${-dx * k}px, ${-dy * k}px) scale(${z})` : "";
    // flash de texto, selo e emote
    const parts = [];
    let blur = false;
    for (const b of list) {
      const inside = t >= b.t && t <= b.t + b.dur;
      if (!inside) continue;
      const age = t - b.t;
      if (b.tipo === "flash_texto") {
        blur = true;
        const white = age < 0.07 ? 0.45 : age < 0.17 ? 0.85 : age < 0.27 ? 0.35 : 0;
        const pop = age < 0.15 ? 0.35 + (1.08 - 0.35) * (age / 0.15) : age < 0.26 ? 1.08 - 0.08 * ((age - 0.15) / 0.11) : 1;
        parts.push(`<div class="pv-dim"></div><div class="pv-flash" style="font-size:${150 * k}px;transform:translate(-50%,-50%) scale(${pop})">${escapeHtmlPv(b.texto)}</div>`);
        if (white) parts.push(`<div class="pv-white" style="opacity:${white}"></div>`);
      } else if (b.tipo === "selo") {
        const x = (80 + b.foco_x * (W - 160)) * k, y = (160 + b.foco_y * (H - 320)) * (stage.clientHeight / H);
        const pop = age < 0.13 ? 0.3 + 0.8 * (age / 0.13) : age < 0.22 ? 1.1 - 0.1 * ((age - 0.13) / 0.09) : 1;
        parts.push(`<div class="pv-selo" style="left:${x}px;top:${y}px;font-size:${92 * k}px;transform:translate(-50%,-50%) rotate(-7deg) scale(${pop})">${escapeHtmlPv(b.texto)}</div>`);
      } else if (b.tipo === "emote" && b.arquivo) {
        const size = 300 * k;
        const x = Math.min(Math.max(b.foco_x * W - 150, 30), W - 330) * k, y = Math.min(Math.max(b.foco_y * H - 150, 120), H - 420) * k;
        parts.push(`<img class="pv-emote" src="${libUrl(`emotes/${b.arquivo}`)}" style="left:${x}px;top:${y}px;width:${size}px;height:${size}px;opacity:${clip01(age / 0.12)}">`);
      }
    }
    video.style.filter = blur ? "blur(6px)" : "";
    layer.innerHTML = parts.join("");
    // efeitos sonoros: tocam quando o vídeo passa pelo tempo deles (tocando, não ao pular)
    if (!video.paused && t >= lastT && t - lastT < 0.5) {
      for (const b of list) {
        if (b.tipo === "sfx" && b.arquivo && b.t > lastT && b.t <= t) sound(`sfx/${b.arquivo}`);
      }
    }
    lastT = t;
    syncMusic(t);
  }
  function sound(path) {
    let a = audioCache.get(path);
    if (!a) { a = new Audio(libUrl(path)); audioCache.set(path, a); }
    a.currentTime = 0; a.volume = 0.5; a.play().catch(() => {});
  }
  function syncMusic(t) {
    const live = onBox.checked && !video.paused ? sections() : [];
    const want = new Set();
    for (const s of live) {
      if (t < s.de || t > s.ate) continue;
      want.add(s.id);
      let a = music.get(s.id);
      if (a && a.dataset.file !== s.file) { a.pause(); a = null; }  // trocou a música no cartão
      if (!a) { a = new Audio(libUrl(`musicas/${s.file}`)); a.dataset.file = s.file; a.volume = 0.3; music.set(s.id, a); }
      if (Math.abs(a.currentTime - (t - s.de)) > 0.35) a.currentTime = t - s.de;
      if (a.paused) a.play().catch(() => {});
    }
    for (const [id, a] of music) if (!want.has(id) && !a.paused) a.pause();
  }
  function loop() { frame(video.currentTime); raf = requestAnimationFrame(loop); }
  function escapeHtmlPv(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  video.addEventListener("play", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(loop); });
  video.addEventListener("pause", () => { cancelAnimationFrame(raf); frame(video.currentTime); syncMusic(-1); });
  video.addEventListener("seeked", () => { lastT = video.currentTime; frame(video.currentTime); });
  video.addEventListener("loadedmetadata", () => {
    const vertical = video.videoWidth / video.videoHeight < 0.6;
    video.style.objectFit = vertical ? "cover" : "contain";  // como o render: vertical enche; o resto entra inteiro
    frame(video.currentTime);
  });
  video.addEventListener("error", () => {  // original apagado ou movido: avisa em vez de ficar uma tela preta
    if (!video.getAttribute("src")) return;
    layer.innerHTML = `<div class="pv-missing">O vídeo original deste projeto não foi encontrado (apagado ou movido de pasta).<br>
      Os vídeos já renderizados continuam abaixo; para editar de novo, crie a pós-edição a partir do arquivo.</div>`;
  });
  video.addEventListener("loadeddata", () => layer.querySelector(".pv-missing")?.remove());
  onBox.addEventListener("change", () => frame(video.currentTime));
  document.getElementById("beats").addEventListener("input", () => frame(video.currentTime));
  document.getElementById("beats").addEventListener("change", () => frame(video.currentTime));
  document.getElementById("trilha").addEventListener("change", () => frame(video.currentTime));

  // controles próprios (os nativos dariam zoom junto com a imagem)
  const play = document.getElementById("pv-play"), bar = document.getElementById("pv-bar"), clock = document.getElementById("pv-time");
  play.onclick = () => (video.paused ? video.play() : video.pause());
  video.addEventListener("play", () => (play.textContent = "Pausar"));
  video.addEventListener("pause", () => (play.textContent = "Tocar"));
  video.addEventListener("timeupdate", () => {
    if (video.duration) bar.value = String(Math.round((video.currentTime / video.duration) * 1000));
    clock.textContent = window.sec ? window.sec(video.currentTime) : video.currentTime.toFixed(1);
  });
  bar.addEventListener("input", () => { if (video.duration) video.currentTime = (+bar.value / 1000) * video.duration; });

  // aproximação do nivelador na prévia (WebAudio): compressor suave/médio/forte com ganho de compensação.
  // O render usa o dynaudnorm do ffmpeg; aqui é só para ouvir a diferença antes de renderizar.
  let ctx = null, comp = null, gain = null, level = "medio";
  const LEVEL_PV = { off: null, suave: [3, 3], medio: [5, 5], forte: [8, 7] };
  function applyLevel() {
    if (!ctx) return;
    const cfg = LEVEL_PV[level];
    comp.threshold.value = cfg ? -34 : 0; comp.ratio.value = cfg ? cfg[0] : 1;
    gain.gain.value = cfg ? Math.pow(10, cfg[1] / 20) : 1;
  }
  video.addEventListener("play", () => {
    if (ctx) { ctx.resume(); return; }
    try {
      ctx = new AudioContext();
      const src = ctx.createMediaElementSource(video);
      comp = ctx.createDynamicsCompressor(); comp.knee.value = 12; comp.attack.value = 0.01; comp.release.value = 0.3;
      gain = ctx.createGain();
      const limit = ctx.createDynamicsCompressor(); limit.threshold.value = -2; limit.ratio.value = 20; limit.attack.value = 0.002;
      src.connect(comp).connect(gain).connect(limit).connect(ctx.destination);
      applyLevel();
    } catch { ctx = null; }  // sem WebAudio: a prévia toca o som original
  });
  window.posPreview = {
    setPlan(p) { plan = p; frame(video.currentTime); }, seek(t) { video.currentTime = Math.max(0, t); },
    setLevel(l) { level = l; applyLevel(); },
  };
})();
