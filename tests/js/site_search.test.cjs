// SPDX-License-Identifier: AGPL-3.0-or-later
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/site_search.js'), 'utf8');

// A small DOM: enough for the combobox, and strict about markup.
class Text {
    constructor(text) {this.text = String(text); this.parentNode = null;}
    get textContent() {return this.text;}
}
class Element {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
        this.attributes = {}; this.dataset = {}; this.listeners = {}; this.hidden = false;
        this.style = {properties: {}, setProperty(name, value) {this.properties[name] = value;}};
        this.visible = true; this.value = ''; this.id = ''; this.className = '';
    }
    set innerHTML(value) {throw new Error('HTML rendering forbidden: ' + value);}
    get textContent() {return this.children.map(child => child.textContent).join('');}
    set textContent(value) {this.children = []; this.append(String(value));}
    append(...nodes) {
        for (const node of nodes) {
            const child = typeof node === 'string' ? new Text(node) : node;
            child.parentNode = this;
            this.children.push(child);
        }
    }
    replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
    setAttribute(name, value) {this.attributes[name] = String(value);}
    getAttribute(name) {return name in this.attributes ? this.attributes[name] : null;}
    removeAttribute(name) {delete this.attributes[name];}
    addEventListener(type, listener) {(this.listeners[type] ||= []).push(listener);}
    dispatch(type, init = {}) {
        const event = {type, target: this, defaultPrevented: false, propagationStopped: false,
            preventDefault() {this.defaultPrevented = true;}, stopPropagation() {this.propagationStopped = true;}, ...init};
        for (const listener of this.listeners[type] || []) listener(event);
        return event;
    }
    matches(selector) {
        return selector.split(',').map(part => part.trim()).some(part => {
            const match = part.match(/^([a-z]*)(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
            if (!match) throw new Error('unsupported selector ' + part);
            const [, tag, attribute, value] = match;
            if (tag && this.tagName !== tag.toUpperCase()) return false;
            if (!attribute) return true;
            const key = attribute.startsWith('data-')
                ? attribute.slice(5).replace(/-([a-z])/g, (_all, letter) => letter.toUpperCase()) : null;
            const actual = key && key in this.dataset ? this.dataset[key]
                : attribute in this ? this[attribute] : this.getAttribute(attribute);
            return value === undefined ? actual !== null && actual !== undefined : String(actual) === value;
        });
    }
    *walk() {for (const child of this.children) if (child instanceof Element) {yield child; yield* child.walk();}}
    querySelector(selector) {for (const node of this.walk()) if (node.matches(selector)) return node; return null;}
    querySelectorAll(selector) {return [...this.walk()].filter(node => node.matches(selector));}
    closest(selector) {for (let node = this; node; node = node.parentNode) if (node.matches && node.matches(selector)) return node; return null;}
    contains(node) {for (; node; node = node.parentNode) if (node === this) return true; return false;}
    focus() {this.ownerDocument.activeElement = this;}
    select() {this.selected = true;}
    click() {this.clicked = (this.clicked || 0) + 1; this.dispatch('click');}
    getClientRects() {return this.visible ? [{}] : [];}
    getBoundingClientRect() {return {bottom: this.bottom || 0};}
    scrollIntoView() {}
}

const MESSAGES = {
    endpoint: '/api/search/suggestions', lang: 'de-CH', labelListbox: 'Suchvorschläge',
    messageLoading: 'Suche läuft …', messageError: 'Vorschläge sind gerade nicht verfügbar.',
    messageRateLimit: 'Zu viele Anfragen.',
};
const DATA = {
    query: 'Baden', total: 3, status: '3 Treffer. Wählen Sie mit den Pfeiltasten.',
    results_url: '/suche?q=Baden', results_label: 'Alle Treffer für «Baden» anzeigen', suggestion: null,
    groups: [
        {kind: 'municipality', label: 'Gemeinden', total: 2, hits: [
            {url: '/gemeinde/profil/4021', title: [{text: 'Baden', match: true}], meta: 'Gemeinde im Kanton AG', context: '', snippet: [{text: '5400 ', match: false}, {text: 'Baden', match: true}], lang: null, language: null},
            {url: '/gemeinde/profil/4022', title: [{text: '<img src=x onerror=alert(1)>', match: false}], meta: '', context: '', snippet: [], lang: null, language: null},
        ]},
        {kind: 'page', label: 'Seiten', total: 1, hits: [
            {url: '/kontakt?lang=fr-CH', title: [{text: 'Contact', match: true}], meta: '', context: '', snippet: [], lang: 'fr-CH', language: 'Français'},
        ]},
    ],
};

function setup({inline = false, extraScript = false, abortable = true, stored = {}, withSwitch = false} = {}) {
    const document = new Element('#document');
    document.listeners = {};
    document.activeElement = null;
    const make = tag => {const node = new Element(tag); node.ownerDocument = document; return node;};
    document.createElement = make;
    document.createTextNode = text => new Text(text);
    document.addEventListener = (type, listener) => {(document.listeners[type] ||= []).push(listener);};
    const fire = (type, init) => {
        const event = {type, defaultPrevented: false, preventDefault() {this.defaultPrevented = true;}, ...init};
        for (const listener of document.listeners[type] || []) listener(event);
        return event;
    };
    const nav = make('nav');
    nav.bottom = 88;
    const toggle = make('button');
    toggle.dataset.menuToggle = '';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', () => toggle.setAttribute('aria-expanded', 'true'));
    const form = make('form');
    Object.assign(form.dataset, MESSAGES, {siteSearch: ''}, inline ? {siteSearchLayout: 'inline'} : {});
    const input = make('input');
    input.id = 'site-search-q';
    input.name = 'q';
    const other = make('input');
    other.name = 'email';
    form.append(input);
    const flyout = make('details');
    flyout.open = true;
    const shortcut = make('input');
    shortcut.type = 'checkbox';
    shortcut.checked = true;
    shortcut.dataset.siteSearchShortcut = '';
    shortcut.hidden = true;
    const outside = make('a');
    nav.append(toggle, flyout, form, other);
    document.append(nav, outside);
    if (withSwitch) document.append(shortcut);
    document.querySelectorAll = selector => {
        if (selector === '[data-site-search]') return [form];
        if (selector === '[data-site-search-shortcut]') return withSwitch ? [shortcut] : [];
        return [];
    };
    const storage = {...stored};

    const timers = new Map();
    let timerId = 0;
    const requests = [];
    const assigned = [];
    const windowListeners = {};
    const context = {
        document, URLSearchParams, AbortController: abortable ? AbortController : undefined,
        location: {assign: url => assigned.push(url)},
        localStorage: {
            getItem: key => (key in storage ? storage[key] : null),
            setItem: (key, value) => {storage[key] = String(value);},
            removeItem: key => {delete storage[key];},
        },
        addEventListener: (type, listener) => {(windowListeners[type] ||= []).push(listener);},
        setTimeout: (fn, delay) => {timers.set(++timerId, {fn, delay}); return timerId;},
        clearTimeout: id => timers.delete(id),
        fetch: (url, init) => new Promise((resolve, reject) => {
            const entry = {url, init, resolve, reject};
            if (init.signal) init.signal.addEventListener('abort', () => {
                entry.aborted = true;
                reject(Object.assign(new Error('aborted'), {name: 'AbortError'}));
            });
            requests.push(entry);
        }),
    };
    vm.runInNewContext(source, context);
    if (extraScript) vm.runInNewContext(source, context);
    const panel = form.querySelector('div');
    const listbox = panel.querySelector('div').children.find(node => node.getAttribute && node.getAttribute('role') === 'listbox');
    const flush = () => new Promise(resolve => setImmediate(resolve));
    async function runTimers(delay) {
        for (const [id, timer] of [...timers]) {
            if (delay === undefined || timer.delay === delay) {timers.delete(id); timer.fn();}
        }
        await flush();
    }
    async function type(value) {
        input.value = value;
        input.dispatch('input');
        await runTimers(120);
    }
    async function reply(request, body, init = {}) {
        request.resolve({ok: init.status === undefined || init.status < 400, status: init.status || 200, json: async () => body});
        await flush(); await flush();
    }
    const key = (name, init = {}) => input.dispatch('keydown', {key: name, ...init});
    const options = () => [...listbox.walk()].filter(node => node.getAttribute('role') === 'option');
    const status = () => panel.querySelector('p');
    return {document, nav, toggle, form, input, other, panel, listbox, timers, requests, assigned, context,
        fire, runTimers, type, reply, key, options, status, flush, flyout, shortcut, storage, outside};
}

test('the field becomes a combobox that controls a hidden listbox', () => {
    const {input, panel, listbox} = setup();
    assert.equal(input.getAttribute('role'), 'combobox');
    assert.equal(input.getAttribute('aria-autocomplete'), 'list');
    assert.equal(input.getAttribute('aria-expanded'), 'false');
    assert.equal(input.getAttribute('aria-controls'), listbox.id);
    assert.equal(listbox.getAttribute('role'), 'listbox');
    assert.equal(listbox.getAttribute('aria-label'), 'Suchvorschläge');
    assert.equal(panel.hidden, true);
});

test('typing waits 120 ms and asks once for the last text', async () => {
    const {input, requests, timers} = setup();
    for (const value of ['Ba', 'Bad', 'Baden']) {
        input.value = value;
        input.dispatch('input');
    }
    assert.equal(requests.length, 0);
    assert.deepEqual([...timers.values()].map(timer => timer.delay), [120]);
    const [timer] = timers.values();
    timer.fn();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/search/suggestions?q=Baden&lang=de-CH');
    assert.equal(requests[0].init.headers.Accept, 'application/json');
});

test('one letter asks nothing and closes the panel', async () => {
    const {type, requests, panel} = setup();
    await type('B');
    assert.equal(requests.length, 0);
    assert.equal(panel.hidden, true);
});

test('suggestions render as grouped options with text, never markup', async () => {
    const {type, requests, reply, input, panel, listbox, options} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    assert.equal(panel.hidden, false);
    assert.equal(input.getAttribute('aria-expanded'), 'true');
    const groups = listbox.children.filter(node => node.getAttribute && node.getAttribute('role') === 'group');
    assert.equal(groups.length, 2);
    const label = groups[0].children[0];
    assert.equal(groups[0].getAttribute('aria-labelledby'), label.id);
    assert.equal(label.textContent, 'Gemeinden');
    const all = options();
    assert.deepEqual(all.map(option => option.href), ['/gemeinde/profil/4021', '/gemeinde/profil/4022', '/kontakt?lang=fr-CH', '/suche?q=Baden']);
    assert.ok(all.every(option => option.getAttribute('aria-selected') === 'false' && option.tabIndex === -1 && option.id));
    const marks = [...all[0].walk()].filter(node => node.tagName === 'MARK').map(node => node.textContent);
    assert.deepEqual(marks, ['Baden', 'Baden']);
    assert.equal(all[1].textContent, '<img src=x onerror=alert(1)>');
    assert.equal(all[2].lang, 'fr-CH');
    assert.ok(all[2].textContent.includes('Français'));
    assert.equal(all[3].textContent, 'Alle Treffer für «Baden» anzeigen');
    const live = [...input.parentNode.children].find(node => node.getAttribute && node.getAttribute('role') === 'status');
    assert.equal(live.textContent, '3 Treffer. Wählen Sie mit den Pfeiltasten.');
});

test('arrow keys move through the options and wrap around', async () => {
    const {type, requests, reply, input, key, options} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    const all = options();
    assert.equal(key('ArrowDown').defaultPrevented, true);
    assert.equal(input.getAttribute('aria-activedescendant'), all[0].id);
    assert.equal(all[0].getAttribute('aria-selected'), 'true');
    key('ArrowDown');
    assert.equal(all[0].getAttribute('aria-selected'), 'false');
    assert.equal(input.getAttribute('aria-activedescendant'), all[1].id);
    key('ArrowUp'); key('ArrowUp');
    assert.equal(input.getAttribute('aria-activedescendant'), all[3].id);
    key('ArrowDown');
    assert.equal(input.getAttribute('aria-activedescendant'), all[0].id);
});

test('Enter opens the active option, otherwise the form submits', async () => {
    const {type, requests, reply, key, assigned, panel} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    assert.equal(key('Enter').defaultPrevented, false);
    assert.deepEqual(assigned, []);
    key('ArrowDown');
    assert.equal(key('Enter').defaultPrevented, true);
    assert.deepEqual(assigned, ['/gemeinde/profil/4021']);
    assert.equal(panel.hidden, true);
});

test('Escape closes the panel, then clears the field, then reaches the menu', async () => {
    const {type, requests, reply, key, input, panel} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    let event = key('Escape');
    assert.equal(panel.hidden, true);
    assert.equal(event.propagationStopped, true);
    assert.equal(input.value, 'Baden');
    assert.equal(input.getAttribute('aria-expanded'), 'false');
    event = key('Escape');
    assert.equal(event.propagationStopped, true);
    assert.equal(input.value, '');
    event = key('Escape');
    assert.equal(event.propagationStopped, false);
});

test('an older answer never replaces a newer one', async () => {
    const {type, requests, reply, options} = setup();
    await type('Bad');
    await type('Baden');
    assert.equal(requests.length, 2);
    assert.equal(requests[0].aborted, true);
    await reply(requests[1], DATA);
    await reply(requests[0], {...DATA, results_url: '/suche?q=Bad', groups: []});
    assert.equal(options().at(-1).href, '/suche?q=Baden');
});

test('without AbortController an older answer is still ignored', async () => {
    const {type, requests, reply, options} = setup({abortable: false});
    await type('Bad');
    await type('Baden');
    await reply(requests[1], DATA);
    await reply(requests[0], {...DATA, results_url: '/suche?q=Bad', groups: []});
    assert.equal(options().at(-1).href, '/suche?q=Baden');
});

test('a slow answer shows the loading state', async () => {
    const {type, requests, runTimers, status, listbox, panel, reply} = setup();
    await type('Baden');
    await runTimers(250);
    assert.equal(panel.hidden, false);
    assert.equal(listbox.getAttribute('aria-busy'), 'true');
    assert.equal(status().textContent, 'Suche läuft …');
    await reply(requests[0], DATA);
    assert.equal(listbox.getAttribute('aria-busy'), null);
    assert.equal(status().hidden, true);
});

for (const [name, answer, message] of [
    ['server error', {status: 503}, 'Vorschläge sind gerade nicht verfügbar.'],
    ['rate limit', {status: 429}, 'Zu viele Anfragen.'],
    ['network failure', new Error('offline'), 'Vorschläge sind gerade nicht verfügbar.'],
]) test(`a ${name} says so and leaves the form usable`, async () => {
    const {type, requests, reply, status, panel, options, key} = setup();
    await type('Baden');
    if (answer instanceof Error) {requests[0].reject(answer); await new Promise(resolve => setImmediate(resolve));}
    else await reply(requests[0], {}, answer);
    assert.equal(panel.hidden, false);
    assert.equal(status().textContent, message);
    assert.equal(options().length, 0);
    assert.equal(key('Enter').defaultPrevented, false);
});

test('slash and Ctrl or Cmd+K focus the field unless another field is in use', () => {
    const {fire, input, other, document, toggle} = setup();
    let event = fire('keydown', {key: '/', target: document});
    assert.equal(event.defaultPrevented, true);
    assert.equal(document.activeElement, input);
    assert.equal(toggle.clicked, undefined);
    document.activeElement = null;
    event = fire('keydown', {key: '/', target: other});
    assert.equal(event.defaultPrevented, false);
    assert.equal(document.activeElement, null);
    for (const modifier of ['ctrlKey', 'metaKey']) {
        document.activeElement = null;
        event = fire('keydown', {key: 'k', [modifier]: true, target: document});
        assert.equal(event.defaultPrevented, true);
        assert.equal(document.activeElement, input);
    }
    event = fire('keydown', {key: 'k', ctrlKey: true, target: other});
    assert.equal(event.defaultPrevented, false);
});

test('a shortcut opens the collapsed mobile menu before focusing', () => {
    const {fire, input, document, toggle} = setup();
    input.visible = false;
    fire('keydown', {key: '/', target: document});
    assert.equal(toggle.clicked, 1);
    assert.equal(document.activeElement, input);
});

test('clicking elsewhere or opening a menu flyout closes the panel', async () => {
    const {type, requests, reply, panel, fire, document, other} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    fire('click', {target: panel.querySelector('a')});
    assert.equal(panel.hidden, false);
    fire('click', {target: other});
    assert.equal(panel.hidden, true);
    await type('Bade');
    await reply(requests[1], DATA);
    fire('toggle', {target: {open: true}});
    assert.equal(panel.hidden, true);
});

test('the panel sits under the navigation bar unless it is inline', async () => {
    let {type, requests, reply, panel} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    assert.equal(panel.style.properties['--site-search-top'], '88px');
    ({type, requests, reply, panel} = setup({inline: true}));
    await type('Baden');
    await reply(requests[0], DATA);
    assert.equal(panel.dataset.layout, 'inline');
    assert.equal(panel.style.properties['--site-search-top'], undefined);
});

test('loading the script twice enhances the field once', () => {
    const {form} = setup({extraScript: true});
    assert.equal(form.children.filter(node => node.className === 'site-search-panel').length, 1);
});

test('typing again drops the highlighted option of the old answer', async () => {
    const {type, requests, reply, input, key, assigned} = setup();
    await type('Bern');
    await reply(requests[0], DATA);
    key('ArrowDown');
    input.value = 'Zürich';
    input.dispatch('input');
    assert.equal(input.getAttribute('aria-activedescendant'), null);
    assert.equal(key('Enter').defaultPrevented, false);
    assert.deepEqual(assigned, []);
});

test('Escape before the first answer cancels it', async () => {
    const {input, key, requests, runTimers, panel} = setup();
    input.value = 'Messpunkt';
    input.dispatch('input');
    key('Escape');
    assert.equal(input.value, '');
    await runTimers();
    assert.equal(requests.length, 0);
    assert.equal(panel.hidden, true);
});

test('an answer that arrives after Escape stays closed', async () => {
    const {type, requests, reply, key, input, panel} = setup();
    await type('Messpunkt');
    key('Escape');
    assert.equal(input.value, '');
    await reply(requests[0], DATA);
    assert.equal(panel.hidden, true);
});

test('the slash shortcut can be switched off and Ctrl or Cmd+K still works', () => {
    const {fire, document, input, shortcut, storage} = setup({withSwitch: true});
    assert.equal(shortcut.hidden, false);
    assert.equal(shortcut.checked, true);
    shortcut.checked = false;
    shortcut.dispatch('change');
    assert.equal(storage['openleg.siteSearch.slash'], 'off');
    assert.equal(input.getAttribute('aria-keyshortcuts'), 'Control+K Meta+K');
    assert.equal(fire('keydown', {key: '/', target: document}).defaultPrevented, false);
    assert.equal(document.activeElement, null);
    assert.equal(fire('keydown', {key: 'k', ctrlKey: true, target: document}).defaultPrevented, true);
    shortcut.checked = true;
    shortcut.dispatch('change');
    assert.equal(storage['openleg.siteSearch.slash'], undefined);
    assert.equal(input.getAttribute('aria-keyshortcuts'), '/ Control+K Meta+K');
});

test('a stored choice keeps the slash shortcut off on every page', () => {
    const {fire, document, input} = setup({stored: {'openleg.siteSearch.slash': 'off'}});
    assert.equal(fire('keydown', {key: '/', target: document}).defaultPrevented, false);
    assert.equal(input.getAttribute('aria-keyshortcuts'), 'Control+K Meta+K');
});

test('opening the suggestions closes an open menu flyout', async () => {
    const {type, requests, reply, flyout} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    assert.equal(flyout.open, false);
});

test('moving focus out of the field closes the suggestions', async () => {
    const {type, requests, reply, form, panel, outside, options} = setup();
    await type('Baden');
    await reply(requests[0], DATA);
    form.dispatch('focusout', {relatedTarget: options()[0]});
    assert.equal(panel.hidden, false);
    form.dispatch('focusout', {relatedTarget: outside});
    assert.equal(panel.hidden, true);
});

test('older options stay visible but inert until new ones arrive', async () => {
    const {type, requests, reply, input, listbox, runTimers} = setup();
    await type('Bern');
    await reply(requests[0], DATA);
    assert.equal(listbox.getAttribute('inert'), null);
    input.value = 'Zürich';
    input.dispatch('input');
    assert.equal(listbox.getAttribute('inert'), '');
    await runTimers(120);
    await reply(requests[1], DATA);
    assert.equal(listbox.getAttribute('inert'), null);
});

test('the shortcut hint appears only once the script runs', () => {
    const {input} = setup();
    assert.equal(input.getAttribute('aria-keyshortcuts'), '/ Control+K Meta+K');
});
