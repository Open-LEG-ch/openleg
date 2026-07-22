document.querySelectorAll("[data-install-console]").forEach((consoleElement) => {
  const tabs = [...consoleElement.querySelectorAll("[data-install-tab]")];
  const panels = [...consoleElement.querySelectorAll("[data-install-panel]")];
  const status = consoleElement.querySelector("[data-copy-status]");

  const activate = (tab, { focusTab } = {}) => {
    tabs.forEach((item) => {
      const active = item === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      item.classList.toggle("border-indigo-400", active);
      item.classList.toggle("text-white", active);
      item.classList.toggle("border-transparent", !active);
      item.classList.toggle("text-slate-400", !active);
    });
    panels.forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.installPanel !== tab.dataset.installTab);
    });
    if (focusTab) tab.focus();
  };

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activate(tab));

    tab.addEventListener("keydown", (event) => {
      const currentIndex = tabs.indexOf(tab);
      let nextIndex = null;

      if (event.key === "ArrowLeft") {
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      } else if (event.key === "ArrowRight") {
        nextIndex = (currentIndex + 1) % tabs.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = tabs.length - 1;
      } else {
        return;
      }

      event.preventDefault();
      activate(tabs[nextIndex], { focusTab: true });
    });
  });

  consoleElement.querySelectorAll("[data-copy-command]").forEach((button) => {
    button.addEventListener("click", async () => {
      const command = button.parentElement.querySelector("code").textContent.trim();
      await navigator.clipboard.writeText(command);
      status.textContent = "Befehl kopiert.";
    });
  });
});
