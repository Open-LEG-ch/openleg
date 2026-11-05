// SPDX-License-Identifier: AGPL-3.0-or-later
// Installed only by the disposable gateway, before ordinary product scripts.
(() => {
    "use strict";
    const prefix = document.currentScript.dataset.prefix;
    if (!/^\/v\/[a-f0-9]{32}$/.test(prefix)) throw new Error("Invalid village generation");
    const generation = /^\/v\/[a-f0-9]{32}(?:\/|$)/;
    function scoped(value) {
        const url = new URL(value, location.href);
        if (url.origin === location.origin && !generation.test(url.pathname)) {
            url.pathname = prefix + url.pathname;
        }
        return url.href;
    }
    const originalFetch = globalThis.fetch;
    globalThis.fetch = function (input, options) {
        if (input instanceof Request) {
            const url = scoped(input.url);
            if (url !== input.url) input = new Request(url, input);
        } else {
            input = scoped(input);
        }
        return originalFetch.call(globalThis, input, options);
    };
    // Product scripts can also add links and forms after initial rendering.
    document.addEventListener("click", event => {
        const link = event.target.closest?.("a[href]");
        if (link) link.href = scoped(link.href);
    }, true);
    document.addEventListener("submit", event => {
        const form = event.target;
        if (form.tagName === "FORM") form.action = scoped(form.action);
    }, true);
})();
