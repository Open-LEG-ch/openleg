/* SPDX-License-Identifier: AGPL-3.0-or-later */
(() => {
  const nav = document.querySelector('[data-release-nav]');
  if (!nav) return;
  const toggle = nav.querySelector('[data-menu-toggle]');
  const menu = nav.querySelector('#release-menu');
  const groups = [...nav.querySelectorAll('details')];
  const closeGroups = () => groups.forEach(group => { group.open = false; });
  const closeMenu = () => {
    nav.dataset.menuOpen = 'false';
    toggle.setAttribute('aria-expanded', 'false');
    closeGroups();
  };
  closeMenu();
  nav.dataset.enhanced = 'true';
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    nav.dataset.menuOpen = String(open);
    toggle.setAttribute('aria-expanded', String(open));
    if (open) menu.querySelector('summary, a').focus();
    else closeGroups();
  });
  groups.forEach(group => {
    group.addEventListener('toggle', () => {
      if (group.open) groups.forEach(other => { if (other !== group) other.open = false; });
    });
  });
  nav.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const group = event.target.closest('details[open]');
    if (group) {
      group.open = false;
      group.querySelector('summary').focus();
    } else if (toggle.getAttribute('aria-expanded') === 'true') {
      closeMenu();
      toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    if (!nav.contains(event.target)) closeGroups();
  });
  const desktop = window.matchMedia('(min-width: 1100px)');
  desktop.addEventListener('change', () => {
    const focusInside = menu.contains(document.activeElement);
    closeMenu();
    if (!desktop.matches && focusInside) toggle.focus();
  });
})();
