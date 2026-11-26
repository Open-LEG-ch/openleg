/* SPDX-License-Identifier: AGPL-3.0-or-later */
(() => {
  const nav = document.querySelector('[data-release-nav]');
  if (!nav) return;
  const toggle = nav.querySelector('[data-menu-toggle]');
  const menu = nav.querySelector('#release-menu');
  const groups = [...nav.querySelectorAll('details')];
  const desktop = window.matchMedia('(min-width: 1100px)');
  // Wide screens open a group's panel when the pointer rests on its label,
  // also while another panel is open, so a pointer crossing the bar changes
  // nothing. A panel opened this way closes after a grace period once the
  // pointer leaves label and panel; a clicked or keyed one stays open. The
  // pointer never closes or replaces a panel whose link holds keyboard focus.
  const OPEN_DELAY = 120;
  const CLOSE_DELAY = 300;
  let openTimer = 0;
  let closeTimer = 0;
  let hoverOpened = null;
  const cancelTimers = () => {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
  };
  const holdsFocus = group => group.querySelector('.release-nav-panel').contains(document.activeElement);
  const openGroup = (group, byHover) => {
    cancelTimers();
    groups.forEach(other => { if (other !== group) other.open = false; });
    group.open = true;
    hoverOpened = byHover ? group : null;
  };
  const closeGroups = () => {
    cancelTimers();
    groups.forEach(group => { group.open = false; });
  };
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
    // Focus stays on the toggle, so Tab enters the menu at its first control.
    if (!open) closeGroups();
  });
  const hovering = event => event.pointerType === 'mouse' && desktop.matches;
  groups.forEach(group => {
    const summary = group.querySelector('summary');
    group.addEventListener('toggle', () => {
      if (group.open) groups.forEach(other => { if (other !== group) other.open = false; });
    });
    summary.addEventListener('pointerenter', event => {
      if (!hovering(event) || group.open) return;
      clearTimeout(openTimer);
      openTimer = setTimeout(() => {
        const current = groups.find(other => other.open);
        if (desktop.matches && !(current && holdsFocus(current))) openGroup(group, true);
      }, OPEN_DELAY);
    });
    summary.addEventListener('click', event => {
      cancelTimers();
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
      if (hovering(event) && group.open) clearTimeout(closeTimer);
    });
    group.addEventListener('pointerleave', event => {
      if (!hovering(event)) return;
      clearTimeout(openTimer);
      if (!group.open || group !== hoverOpened) return;
      closeTimer = setTimeout(() => { if (!holdsFocus(group)) group.open = false; }, CLOSE_DELAY);
    });
  });
  // Escape works anywhere, so a panel opened by hover can be dismissed without
  // moving the pointer; focus returns to the trigger only from inside the nav.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    cancelTimers();
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
