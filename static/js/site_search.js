/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Live suggestions for every [data-site-search] form: WAI-ARIA combobox with a
 * grouped listbox. Without this script the form still submits to /suche. */
(() => {
    'use strict';
    const root = typeof globalThis !== 'undefined' ? globalThis : window;
    if (root.openlegSiteSearch) return;
    root.openlegSiteSearch = true;

    const DEBOUNCE_MS = 120;
    const LOADING_DELAY_MS = 250;
    const MIN_LENGTH = 2;
    const MAX_LENGTH = 100;
    // WCAG 2.1.4: visitors can switch off the single-key shortcut.
    const SLASH_SETTING = 'openleg.siteSearch.slash';
    const forms = [...document.querySelectorAll('[data-site-search]')];
    const controllers = [];

    function slashEnabled() {
        try {
            return root.localStorage.getItem(SLASH_SETTING) !== 'off';
        } catch (error) {
            return true;
        }
    }

    // Stored choices apply in every tab; only without storage does the choice
    // stay local to this page.
    let slashOverride = null;
    function setSlash(enabled) {
        try {
            if (enabled) root.localStorage.removeItem(SLASH_SETTING);
            else root.localStorage.setItem(SLASH_SETTING, 'off');
            slashOverride = null;
        } catch (error) {
            slashOverride = enabled;
        }
    }
    const slashActive = () => (slashOverride === null ? slashEnabled() : slashOverride);

    const isEditable = target => Boolean(target) && (target.isContentEditable
        || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
    const isVisible = element => element.getClientRects().length > 0;

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    // Text segments become text nodes and <mark> elements, never markup.
    function segments(parent, parts) {
        for (const part of parts || []) {
            const text = String(part.text || '');
            if (part.match) parent.append(element('mark', '', text));
            else parent.append(document.createTextNode(text));
        }
        return parent;
    }

    function enhance(form, number) {
        const input = form.querySelector('input[name="q"]');
        if (!input || form.dataset.siteSearchReady === 'true') return null;
        form.dataset.siteSearchReady = 'true';
        const inline = form.dataset.siteSearchLayout === 'inline';
        const prefix = (input.id || 'site-search') + '-' + number;
        const panel = element('div', 'site-search-panel');
        panel.id = prefix + '-panel';
        panel.hidden = true;
        if (inline) panel.dataset.layout = 'inline';
        const inner = element('div', 'site-search-panel-inner');
        const status = element('p', 'site-search-status');
        const listbox = element('div', 'site-search-listbox');
        listbox.id = prefix + '-listbox';
        listbox.setAttribute('role', 'listbox');
        listbox.setAttribute('aria-label', form.dataset.labelListbox || '');
        const live = element('p', 'site-search-label');
        live.setAttribute('role', 'status');
        live.setAttribute('aria-live', 'polite');
        inner.append(status, listbox);
        panel.append(inner);
        form.append(panel, live);

        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', listbox.id);
        input.setAttribute('aria-haspopup', 'listbox');

        let options = [];
        let active = -1;
        let timer = null;
        let loadingTimer = null;
        let request = null;
        let sequence = 0;
        let shown = '';
        // The query whose answer is queued or on its way.
        let pending = '';

        function place() {
            if (inline || panel.hidden) return;
            const anchor = form.closest('nav, header') || form;
            const bottom = Math.round(anchor.getBoundingClientRect().bottom);
            panel.style.setProperty('--site-search-top', bottom + 'px');
            // Where the panel sits in the menu, it scrolls within the space left
            // below it, so the field never has to scroll away.
            const top = panel.getBoundingClientRect().top;
            const height = root.innerHeight;
            if (Number.isFinite(top) && Number.isFinite(height)) {
                panel.style.setProperty('--site-search-max', Math.max(48, Math.floor(height - top - 12)) + 'px');
            }
        }

        function setActive(index) {
            if (options[active]) options[active].setAttribute('aria-selected', 'false');
            active = index;
            const option = options[active];
            if (option) {
                option.setAttribute('aria-selected', 'true');
                input.setAttribute('aria-activedescendant', option.id);
                if (option.scrollIntoView) option.scrollIntoView({block: 'nearest'});
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        function open() {
            // A menu flyout and the suggestions never show at the same time.
            const nav = form.closest('nav') || document.querySelector('nav[data-release-nav]');
            if (nav) nav.querySelectorAll('details[open]').forEach(details => { details.open = false; });
            panel.hidden = false;
            // Expanded means options to navigate, not a loading or error note.
            input.setAttribute('aria-expanded', String(options.length > 0));
            place();
        }

        function close() {
            clearTimeout(timer);
            clearTimeout(loadingTimer);
            if (request) request.abort();
            request = null;
            pending = '';
            sequence += 1;
            setActive(-1);
            panel.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            listbox.removeAttribute('aria-busy');
        }

        function showStatus(text) {
            status.textContent = text;
            status.hidden = !text;
            live.textContent = text;
        }

        function option(href, className) {
            const link = element('a', 'site-search-option' + (className ? ' ' + className : ''));
            link.id = prefix + '-option-' + options.length;
            link.href = href;
            link.setAttribute('role', 'option');
            link.setAttribute('aria-selected', 'false');
            link.tabIndex = -1;
            options.push(link);
            return link;
        }

        function render(data) {
            options = [];
            active = -1;
            input.removeAttribute('aria-activedescendant');
            listbox.replaceChildren();
            listbox.removeAttribute('aria-busy');
            listbox.removeAttribute('inert');
            if (data.suggestion) {
                listbox.append(option(data.suggestion.url, 'site-search-correction'));
                options[options.length - 1].textContent = data.suggestion.label;
            }
            for (const [index, group] of (data.groups || []).entries()) {
                const box = element('div', 'site-search-option-group');
                box.setAttribute('role', 'group');
                const label = element('div', 'site-search-group-label', group.label);
                label.id = prefix + '-group-' + index;
                label.setAttribute('role', 'presentation');
                box.setAttribute('aria-labelledby', label.id);
                box.append(label);
                for (const hit of group.hits || []) {
                    const link = option(hit.url);
                    if (hit.lang) link.lang = hit.lang;
                    const title = segments(element('span', 'site-search-option-title'), hit.title);
                    if (hit.language) title.append(' ', element('span', 'site-search-language', hit.language));
                    link.append(title);
                    const meta = hit.context || hit.meta;
                    if (meta) link.append(element('span', 'site-search-option-meta', meta));
                    if (hit.snippet && hit.snippet.length) {
                        link.append(segments(element('span', 'site-search-option-snippet'), hit.snippet));
                    }
                    box.append(link);
                }
                listbox.append(box);
            }
            const all = option(data.results_url, 'site-search-all');
            all.textContent = data.results_label;
            listbox.append(all);
            showStatus(data.total ? '' : data.status);
            live.textContent = data.status || '';
            shown = data.query;
            open();
        }

        async function load(query) {
            if (request) request.abort();
            const controller = typeof AbortController === 'function' ? new AbortController() : null;
            request = controller;
            const mine = ++sequence;
            clearTimeout(loadingTimer);
            loadingTimer = setTimeout(() => {
                if (mine !== sequence) return;
                listbox.setAttribute('aria-busy', 'true');
                showStatus(form.dataset.messageLoading || '');
                open();
            }, LOADING_DELAY_MS);
            const params = new URLSearchParams({q: query, lang: form.dataset.lang || ''});
            let failure = '';
            let data = null;
            try {
                const response = await fetch(form.dataset.endpoint + '?' + params.toString(), {
                    headers: {Accept: 'application/json'},
                    credentials: 'same-origin',
                    // Never answer from the HTTP cache: a stored response could
                    // list a LEG the search has dropped since.
                    cache: 'no-store',
                    signal: controller ? controller.signal : undefined,
                });
                if (response.status === 429) failure = form.dataset.messageRateLimit;
                else if (!response.ok) failure = form.dataset.messageError;
                else data = await response.json();
            } catch (error) {
                if (error && error.name === 'AbortError') return;
                failure = form.dataset.messageError;
            }
            if (mine !== sequence) return;
            clearTimeout(loadingTimer);
            request = null;
            pending = '';
            if (data) {
                listbox.hidden = false;
                render(data);
                return;
            }
            options = [];
            setActive(-1);
            listbox.replaceChildren();
            // No empty listbox next to the error: the status speaks alone.
            listbox.hidden = true;
            listbox.removeAttribute('aria-busy');
            listbox.removeAttribute('inert');
            shown = '';
            pending = '';
            showStatus(failure || '');
            open();
        }

        // The shown options belong to the old text: none stays selectable by
        // Enter, and an answer still on its way is dropped.
        function invalidate() {
            clearTimeout(timer);
            clearTimeout(loadingTimer);
            if (request) request.abort();
            request = null;
            sequence += 1;
            pending = '';
            setActive(-1);
            options = [];
            shown = '';
            // Old options stay in view, dimmed, but nobody can click them, and
            // assistive technology no longer counts them as options.
            listbox.setAttribute('inert', '');
            input.setAttribute('aria-expanded', 'false');
        }

        function schedule() {
            invalidate();
            const query = input.value.trim().slice(0, MAX_LENGTH);
            if (query.length < MIN_LENGTH) {
                close();
                return;
            }
            pending = query;
            timer = setTimeout(() => load(query), DEBOUNCE_MS);
        }

        // Fresh suggestions for the current text, unless they are on their way.
        function refresh() {
            const query = input.value.trim().slice(0, MAX_LENGTH);
            if (query.length < MIN_LENGTH || query === pending) return;
            schedule();
        }

        input.addEventListener('input', schedule);
        // Refocusing asks again: a retained answer may list a LEG the search has
        // dropped since.
        input.addEventListener('focus', () => {
            if (panel.hidden) refresh();
        });
        input.addEventListener('keydown', event => {
            // An IME confirming text owns its keys, Enter included.
            if (event.isComposing || event.keyCode === 229) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                if (panel.hidden || !options.length) {
                    if (input.value.trim().length >= MIN_LENGTH) {
                        event.preventDefault();
                        refresh();
                    }
                    return;
                }
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                const count = options.length;
                const start = active === -1 ? (step === 1 ? -1 : count) : active;
                setActive((start + step + count) % count);
            } else if (event.key === 'Enter') {
                const chosen = options[active];
                if (!panel.hidden && chosen) {
                    event.preventDefault();
                    close();
                    root.location.assign(chosen.href);
                }
            } else if (event.key === 'Escape') {
                const nav = form.closest('nav');
                if (!panel.hidden) {
                    event.preventDefault();
                    event.stopPropagation();
                    close();
                } else if (nav && nav.querySelector('details[open]')) {
                    // A flyout replaced the suggestions: drop any pending answer,
                    // keep the typed query (no native clearing) and let the
                    // navigation close the flyout.
                    event.preventDefault();
                    close();
                } else if (input.value) {
                    event.preventDefault();
                    event.stopPropagation();
                    close();
                    input.value = '';
                    shown = '';
                }
            } else if (event.key === 'Tab') {
                close();
            }
        });
        form.addEventListener('focusout', event => {
            if (!form.contains(event.relatedTarget)) close();
        });
        // Keep focus in the field while a pointer chooses an option.
        panel.addEventListener('mousedown', event => event.preventDefault());
        panel.addEventListener('click', event => {
            if (event.target && event.target.closest && event.target.closest('a')) close();
        });

        return {
            form, input, panel, close, place, inline,
            focus() {
                if (!isVisible(input)) {
                    const nav = form.closest('nav');
                    const toggle = nav && nav.querySelector('[data-menu-toggle]');
                    if (toggle && toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
                }
                input.focus();
                if (input.select) input.select();
            },
        };
    }

    forms.forEach((form, number) => {
        const controller = enhance(form, number);
        if (controller) controllers.push(controller);
    });
    if (!controllers.length) return;

    const switches = [...document.querySelectorAll('[data-site-search-shortcut]')];
    function showShortcuts() {
        const keys = (slashActive() ? '/ ' : '') + 'Control+K Meta+K';
        controllers.forEach(item => item.input.setAttribute('aria-keyshortcuts', keys));
        switches.forEach(row => {
            const box = row.matches('input') ? row : row.querySelector('input');
            if (box) box.checked = slashActive();
        });
    }
    switches.forEach(row => {
        row.hidden = false;
        const box = row.matches('input') ? row : row.querySelector('input');
        if (box) box.addEventListener('change', () => {
            setSlash(box.checked);
            showShortcuts();
        });
    });
    showShortcuts();

    document.addEventListener('keydown', event => {
        if (event.defaultPrevented || event.altKey) return;
        const slash = event.key === '/' && !event.ctrlKey && !event.metaKey && slashActive();
        const commandK = (event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === 'k';
        if (!slash && !commandK) return;
        if (isEditable(event.target)) return;
        // The page's own search field wins over the one in the navigation.
        const target = controllers.find(item => item.inline && isVisible(item.input)) || controllers[0];
        event.preventDefault();
        target.focus();
    });
    document.addEventListener('click', event => {
        for (const item of controllers) {
            if (!item.form.contains(event.target)) item.close();
        }
    });
    // A navigation flyout and the suggestions never show at the same time.
    document.addEventListener('toggle', event => {
        if (event.target && event.target.open) controllers.forEach(item => item.close());
    }, true);
    // Another tab changed the shortcut: follow it here too.
    root.addEventListener('storage', event => {
        if (!event || event.key === SLASH_SETTING || event.key === null) showShortcuts();
    });
    const reposition = () => controllers.forEach(item => item.place());
    root.addEventListener('resize', reposition);
    root.addEventListener('scroll', reposition, {passive: true});
})();
