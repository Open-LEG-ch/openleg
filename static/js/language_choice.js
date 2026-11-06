/* SPDX-License-Identifier: AGPL-3.0-or-later */
(() => {
  const preserveSection = event => {
    const link = event.currentTarget;
    const destination = new URL(link.href, window.location.href);
    if (destination.pathname === window.location.pathname) {
      destination.hash = window.location.hash;
      link.href = destination.href;
    }
  };
  document.querySelectorAll('[data-language-choice]').forEach(link => {
    link.addEventListener('click', preserveSection);
    link.addEventListener('auxclick', preserveSection);
  });
  document.querySelectorAll('[data-language-menu]').forEach(menu => {
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.open) {
        menu.open = false;
        menu.querySelector('summary').focus();
      }
    });
    document.addEventListener('click', event => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
})();
