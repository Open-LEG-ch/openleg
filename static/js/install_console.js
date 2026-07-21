document.querySelectorAll("[data-install-console]").forEach((consoleElement) => {
  const tabs = [...consoleElement.querySelectorAll("[data-install-tab]")];
  const panels = [...consoleElement.querySelectorAll("[data-install-panel]")];
  const status = consoleElement.querySelector("[data-copy-status]");

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((item) => {
        const active = item === tab;
        item.setAttribute("aria-selected", String(active));
        item.classList.toggle("border-indigo-400", active);
        item.classList.toggle("text-white", active);
        item.classList.toggle("border-transparent", !active);
        item.classList.toggle("text-slate-400", !active);
      });
      panels.forEach((panel) => {
        panel.classList.toggle("hidden", panel.dataset.installPanel !== tab.dataset.installTab);
      });
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
