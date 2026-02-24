(function () {
  const tabs = Array.from(document.querySelectorAll('[data-seg-tab]'));
  const panels = Array.from(document.querySelectorAll('[data-seg-panel]'));
  const tablist = document.querySelector('[role="tablist"]');

  if (!tabs.length || !panels.length || !tablist) return;

  function activate(seg, setFocus) {
    tabs.forEach((btn) => {
      const isActive = btn.dataset.segTab === seg;
      btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
      btn.tabIndex = isActive ? 0 : -1;
      btn.classList.toggle('bg-ink', isActive);
      btn.classList.toggle('text-white', isActive);
      btn.classList.toggle('bg-white/80', !isActive);
      btn.classList.toggle('text-ink', !isActive);
      if (isActive && setFocus) btn.focus();
    });
    panels.forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.segPanel !== seg);
    });
  }

  tabs.forEach((btn) => {
    btn.addEventListener('click', () => activate(btn.dataset.segTab, true));
  });

  tablist.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const current = tabs.findIndex((t) => t.getAttribute('aria-selected') === 'true');
    const dir = e.key === 'ArrowRight' ? 1 : -1;
    const next = (current + dir + tabs.length) % tabs.length;
    activate(tabs[next].dataset.segTab, true);
  });

  activate(tabs[0].dataset.segTab, false);
})();
