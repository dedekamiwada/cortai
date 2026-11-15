// Abas genéricas: cada aba mostra um grupo de blocos que já existem na página. Uma aba só aparece se tiver
// conteúdo (algum bloco sem .hidden), pode mostrar um contador e lembra a última aberta. O esconder das abas
// usa .tab-off, separado do .hidden que cada bloco controla sozinho (os dois não brigam).
function makeTabs(nav, tabs, storeKey) {
  let active = null;
  try { active = localStorage.getItem(storeKey); } catch { /* sem armazenamento: começa na primeira */ }
  const els = (t) => t.els().filter(Boolean);
  const hasContent = (t) => (t.visible ? t.visible() : els(t).some((el) => !el.classList.contains("hidden")));
  function show(id) {
    active = id;
    try { localStorage.setItem(storeKey, id); } catch { /* idem */ }
    refresh();
    tabs.find((t) => t.id === id)?.onShow?.();
  }
  function refresh() {
    const shown = tabs.filter(hasContent);
    if (!shown.some((t) => t.id === active)) active = shown[0]?.id ?? null;
    nav.classList.toggle("hidden", shown.length < 2);
    nav.innerHTML = shown.map((t) => {
      const n = t.count?.();
      const label = typeof t.label === "function" ? t.label() : t.label;
      return `<button type="button" role="tab" data-tab="${t.id}" aria-selected="${t.id === active}">${label}${n ? `<span class="tab-n">${n}</span>` : ""}</button>`;
    }).join("");
    nav.querySelectorAll("button").forEach((b) => (b.onclick = () => show(b.dataset.tab)));
    // com uma aba só não há barra e tudo aparece; com várias, só o grupo da aba ativa
    for (const t of tabs) for (const el of els(t)) el.classList.toggle("tab-off", shown.length > 1 && t.id !== active);
  }
  return { refresh, show, get active() { return active; } };
}
