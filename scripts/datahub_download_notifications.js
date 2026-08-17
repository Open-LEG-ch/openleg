// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Alle Benachrichtigungen im Swisseldex Datahub herunterladen.
//
// Der Datahub hat die SDAT Nachrichten nicht zugestellt, solange kein FTPES
// Ziel konfiguriert war. Die Dateien liegen weiterhin als xml.gz im
// Benachrichtigungs-Archiv. Dieses Snippet klickt sie der Reihe nach durch.
//
// Es liest keine Zugangsdaten und kein Sitzungstoken. Es bedient nur die
// Oberfläche, die Anwendung selbst stellt die authentifizierten Anfragen.
//
// VORBEREITUNG
//   1. chrome://settings/downloads öffnen und "Vor dem Download von Dateien
//      nach dem Speicherort fragen" AUSSCHALTEN. Sonst kommen 140 Dialoge.
//   2. Auf https://datahub.swisseldex.ch/users/notifications einloggen.
//   3. DevTools öffnen (F12), Reiter "Console".
//   4. Dieses Skript vollständig einfügen und mit Enter starten.
//   5. Den Tab im Vordergrund lassen, bis "FERTIG" erscheint.
//
// DANACH
//   python scripts/collect_sdat_downloads.py
//   python scripts/import_sdat.py data/sdat --dry-run

(async () => {
  const PAGE_SIZE = 250; // eine Seite für alle Einträge
  const AFTER_OPEN_MS = 1200; // Detailansicht rendern lassen
  const DOWNLOAD_TIMEOUT_MS = 15000; // auf die HTTP-Antwort warten
  const AFTER_BACK_MS = 900; // Tabelle neu aufbauen lassen

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const waitFor = async (predicate, timeoutMs = 15000, stepMs = 200) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const hit = predicate();
      if (hit) return hit;
      await sleep(stepMs);
    }
    return null;
  };

  const byText = (selector, pattern) =>
    [...document.querySelectorAll(selector)].find((el) =>
      pattern.test((el.innerText || '').trim()),
    );

  if (!location.pathname.startsWith('/users/notifications')) {
    console.error('Bitte zuerst /users/notifications öffnen.');
    return;
  }

  // --- Seitengrösse hochsetzen, damit alle Zeilen im DOM sind ---------------
  // Die Tabelle fällt beim Zurückblättern auf 25 Einträge zurück. Darum
  // ist das hier eine Funktion, die vor jedem Zugriff erneut läuft.
  const setPageSize = async () => {
    const select = [...document.querySelectorAll('select')].find((s) =>
      [...s.options].some((o) => parseInt(o.value, 10) === PAGE_SIZE),
    );
    if (!select) return false;
    const option = [...select.options].find(
      (o) => parseInt(o.value, 10) === PAGE_SIZE,
    );
    if (select.value === option.value) return true;
    select.value = option.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await sleep(2000);
    return true;
  };

  const rowButton = (id) =>
    document.querySelector(
      `button[data-actionid="notification_view"][data-rowidvalue="${id}"]`,
    );

  // Zeile suchen; wenn sie fehlt, Seitengrösse neu setzen und nochmal suchen.
  const findRow = async (id) => {
    const direct = rowButton(id);
    if (direct) return direct;
    await setPageSize();
    return await waitFor(() => rowButton(id), 8000);
  };

  if (!(await setPageSize())) {
    console.error(
      'Vollständige Liste nicht garantiert: Seitengrösse 250 ist nicht verfügbar.',
    );
    return;
  }

  // --- Alle Benachrichtigungs-IDs einsammeln --------------------------------
  const collectRows = () =>
    [...document.querySelectorAll('button[data-actionid="notification_view"]')].map(
      (button) => {
        const row = button.closest('tr');
        const cells = row ? [...row.querySelectorAll('td')] : [];
        return {
          id: button.dataset.rowidvalue,
          filename: cells[1] ? cells[1].innerText.trim() : '',
        };
      },
    );

  const rows = collectRows();
  if (!rows.length) {
    console.error('Keine Benachrichtigungen gefunden.');
    return;
  }
  if (rows.length >= PAGE_SIZE) {
    console.error(
      'Vollständige Liste nicht garantiert: Mindestens 250 Einträge gefunden. ' +
        'Bitte die Benachrichtigungen in kleineren Zeiträumen verarbeiten.',
    );
    return;
  }

  // --- Statuscodes der Download-Aufrufe beobachten ---------------------------
  // Liest ausschliesslich den HTTP-Status, keine Header und keine Inhalte.
  // Damit bricht der Lauf sofort ab, wenn der Server die Datei nicht liefert,
  // statt 140 Mal ins Leere zu klicken.
  let pendingDownload = null;
  let lastStatus = null;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__isDownload = typeof url === 'string' && url.includes('/download');
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    if (this.__isDownload) {
      const pending = pendingDownload;
      this.addEventListener('loadend', () => {
        lastStatus = this.status;
        if (pending) pending.finish(this.status);
      });
    }
    return originalSend.apply(this, args);
  };
  const waitForDownloadResponse = () =>
    new Promise((resolve) => {
      const pending = { done: false, timer: null, finish: null };
      const finish = (status) => {
        if (pending.done) return;
        pending.done = true;
        if (pendingDownload === pending) pendingDownload = null;
        clearTimeout(pending.timer);
        resolve(status);
      };
      pending.finish = finish;
      pending.timer = setTimeout(() => finish(null), DOWNLOAD_TIMEOUT_MS);
      pendingDownload = pending;
    });
  const restore = () => {
    XMLHttpRequest.prototype.open = originalOpen;
    XMLHttpRequest.prototype.send = originalSend;
  };

  try {
    console.log(`Gefunden: ${rows.length} Benachrichtigungen. Starte Download.`);
    const failed = [];
    let consecutiveServerErrors = 0;

    for (let i = 0; i < rows.length; i++) {
      const { id, filename } = rows[i];
      const label = `[${i + 1}/${rows.length}] ${filename || id}`;
      let serverError = false;

      try {
        // Detailansicht über die Tabelle öffnen: bleibt eine SPA-Navigation,
        // darum überlebt dieses Skript den Wechsel.
        const view = await findRow(id);
        if (!view) throw new Error('Zeile nicht gefunden');
        view.click();

        const download = await waitFor(() => byText('button', /Herunterladen/i));
        if (!download) throw new Error('Download-Knopf nicht gefunden');
        await sleep(AFTER_OPEN_MS);
        lastStatus = null;
        const response = waitForDownloadResponse();
        download.click();
        const status = await response;

        if (status === null) {
          serverError = true;
          consecutiveServerErrors++;
          throw new Error('Keine Download-Antwort innerhalb des Zeitlimits');
        }
        if (status === 0) {
          serverError = true;
          consecutiveServerErrors++;
          throw new Error('Download-Anfrage ohne HTTP-Antwort beendet');
        }
        if (status >= 400) {
          serverError = true;
          consecutiveServerErrors++;
          throw new Error(`Server antwortet mit HTTP ${status}`);
        }
        consecutiveServerErrors = 0;
        console.log(`${label} ok`);
      } catch (error) {
        if (!serverError) consecutiveServerErrors = 0;
        failed.push({ id, filename, error: String(error) });
        console.warn(`${label} FEHLER: ${error}`);

        if (consecutiveServerErrors >= 3) {
          console.error(
            'ABBRUCH: Der Datahub liefert die Dateien nicht aus ' +
              `(zuletzt HTTP ${lastStatus}). Das ist ein Serverproblem, ` +
              'kein Fehler dieses Skripts. Bitte support@swisseldex.ch melden.',
          );
          return;
        }
      }

      // Zurück zur Liste, egal ob der Download geklappt hat.
      const back = byText('button', /^Zur[uü]ck$/i);
      if (back) {
        back.click();
      } else {
        history.back();
      }
      await sleep(AFTER_BACK_MS);
    }

    console.log(
      `FERTIG. ${rows.length - failed.length} von ${rows.length} angestossen.`,
    );
    if (failed.length) {
      console.warn('Fehlgeschlagen:', failed);
    }
    console.log('Nächster Schritt: python scripts/collect_sdat_downloads.py');
  } finally {
    restore();
  }
})();
