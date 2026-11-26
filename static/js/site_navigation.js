/* SPDX-License-Identifier: AGPL-3.0-or-later */
(() => {
  const nav = document.querySelector('[data-release-nav]');
  if (!nav) return;
  const toggle = nav.querySelector('[data-menu-toggle]');
  const menu = nav.querySelector('#release-menu');
  const groups = [...nav.querySelectorAll('details')];
  const desktop = window.matchMedia('(min-width: 1100px)');
  // Wide screens open a group's panel when the pointer rests on it.
  // A hover-opened panel closes after a grace period once the pointer leaves
  // both the trigger and the panel; a clicked or keyed one stays open.
  const OPEN_DELAY = 120;
  const CLOSE_DELAY = 300;
  let timer = 0;
  let hoverOpened = null;
  const wait = (action, delay) => {
    clearTimeout(timer);
    timer = setTimeout(action, delay);
  };
  const openGroup = (group, byHover) => {
    groups.forEach(other => { if (other !== group) other.open = false; });
    group.open = true;
    hoverOpened = byHover ? group : null;
  };
  const closeGroups = () => groups.forEach(group => { group.open = false; });
  const closeMenu = () => {
    nav.dataset.menuOpen = 'false';
    toggle.setAttribute('aria-expanded', 'false');
    closeGroups();
  };
  // The search sits before the login on wide screens and opens the narrow
  // menu; moving the node keeps the focus order equal to the visible order.
  const search = menu.querySelector('.release-search-slot');
  const login = menu.querySelector('.release-login');
  const placeSearch = () => {
    if (!search) return;
    if (desktop.matches) login.before(search);
    else menu.prepend(search);
  };
  closeMenu();
  placeSearch();
  nav.dataset.enhanced = 'true';
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    nav.dataset.menuOpen = String(open);
    toggle.setAttribute('aria-expanded', String(open));
    if (open) menu.querySelector('summary, a').focus();
    else closeGroups();
  });
  const hovering = event => event.pointerType === 'mouse' && desktop.matches;
  groups.forEach(group => {
    const summary = group.querySelector('summary');
    group.addEventListener('toggle', () => {
      if (group.open) groups.forEach(other => { if (other !== group) other.open = false; });
    });
    summary.addEventListener('pointerenter', event => {
      if (!hovering(event) || group.open) return;
      // Once a panel is open, the visitor is browsing: swap without a delay.
      if (groups.some(other => other.open)) {
        clearTimeout(timer);
        openGroup(group, true);
      } else wait(() => openGroup(group, true), OPEN_DELAY);
    });
    summary.addEventListener('click', event => {
      clearTimeout(timer);
      // The pointer already opened this panel; the click keeps it open.
      if (group.open && group === hoverOpened) event.preventDefault();
      hoverOpened = null;
    });
    summary.addEventListener('keydown', event => {
      if (event.key !== 'ArrowDown') return;
      event.preventDefault();
      openGroup(group, false);
      group.querySelector('a').focus();
    });
    // On wide screens the panel covers the page, so it closes once focus leaves.
    group.addEventListener('focusout', event => {
      if (desktop.matches && event.relatedTarget && !group.contains(event.relatedTarget)) {
        group.open = false;
      }
    });
    group.addEventListener('pointerenter', event => {
      if (hovering(event) && group.open) clearTimeout(timer);
    });
    group.addEventListener('pointerleave', event => {
      if (!hovering(event)) return;
      clearTimeout(timer);
      if (group.open && group === hoverOpened) wait(() => { group.open = false; }, CLOSE_DELAY);
    });
  });
  // Escape works anywhere, so a panel opened by hover can be dismissed without
  // moving the pointer; focus returns to the trigger only from inside the nav.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const group = groups.find(candidate => candidate.open);
    const inside = nav.contains(document.activeElement);
    if (group) {
      group.open = false;
      if (inside) group.querySelector('summary').focus();
    } else if (inside && toggle.getAttribute('aria-expanded') === 'true') {
      closeMenu();
      toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    const outside = desktop.matches ? group => !group.contains(event.target) : () => !nav.contains(event.target);
    groups.forEach(group => { if (group.open && outside(group)) group.open = false; });
  });
  desktop.addEventListener('change', () => {
    const focusInside = menu.contains(document.activeElement);
    closeMenu();
    placeSearch();
    if (!desktop.matches && focusInside) toggle.focus();
  });
})();
