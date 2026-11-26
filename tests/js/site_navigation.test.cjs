// SPDX-License-Identifier: AGPL-3.0-or-later
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// A small DOM with real event paths: pointerenter/pointerleave follow the
// ancestor chain, clicks bubble, and a summary click toggles its <details>.
class Node {
    constructor(tag, attrs = {}) {
        this.tagName = tag.toUpperCase(); this.attrs = {...attrs}; this.children = []; this.parent = null;
        this.dataset = {}; this.listeners = {}; this._open = false;
    }
    append(...nodes) { for (const node of nodes) { node.remove(); node.parent = this; this.children.push(node); } return this; }
    prepend(node) { node.remove(); node.parent = this; this.children.unshift(node); }
    before(node) { node.remove(); const siblings = this.parent.children; siblings.splice(siblings.indexOf(this), 0, node); node.parent = this.parent; }
    remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
    get classList() { return {contains: name => (this.attrs.class || '').split(' ').includes(name)}; }
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    hasAttribute(name) { return name in this.attrs; }
    contains(node) { for (; node; node = node.parent) if (node === this) return true; return false; }
    matches(selector) {
        return selector.split(',').some(part => {
            const s = part.trim();
            if (s.startsWith('#')) return this.attrs.id === s.slice(1);
            if (s.startsWith('.')) return this.classList.contains(s.slice(1));
            if (s.startsWith('[')) return s.slice(1, -1) in this.attrs;
            return this.tagName === s.toUpperCase();
        });
    }
    *walk() { for (const child of this.children) { yield child; yield* child.walk(); } }
    querySelector(selector) { for (const node of this.walk()) if (node.matches(selector)) return node; return null; }
    querySelectorAll(selector) { return [...this.walk()].filter(node => node.matches(selector)); }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    focus() { const previous = doc.activeElement; doc.activeElement = this; if (previous && previous !== this) dispatch(previous, 'focusout', {relatedTarget: this, bubbles: true}); }
    get open() { return this._open; }
    set open(value) { if (this._open === !!value) return; this._open = !!value; dispatch(this, 'toggle'); }
    get firstElementChild() { return this.children[0] || null; }
}
let doc;
function dispatch(target, type, init = {}) {
    const event = {type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...init};
    const path = [];
    for (let node = target; node; node = node.parent) path.push(node);
    // Nodes appended under doc already reach it through their parents.
    if (init.bubbles && path[path.length - 1] !== doc) path.push(doc);
    for (const node of init.bubbles ? path : [target]) for (const listener of node.listeners[type] || []) listener.call(node, event);
    return event;
}
const el = (tag, attrs, ...children) => new Node(tag, attrs).append(...children);

function setup({desktop = true} = {}) {
    doc = new Node('#document');
    doc.activeElement = null;
    const groups = [['LEG gründen', 4], ['Software', 5], ['Für Gemeinden', 3]].map(([label, count], i) =>
        el('details', {class: 'release-nav-group', name: 'release-nav'},
            el('summary', {}, el('span', {})),
            el('div', {class: 'release-nav-panel'}, ...Array.from({length: count}, (_, n) => el('a', {href: `/${i}/${n}`})))));
    const language = el('details', {class: 'release-nav-group release-language', name: 'release-nav'},
        el('summary', {}), el('div', {class: 'release-nav-panel'}, el('a', {href: '?lang=fr-CH'})));
    const toggle = el('button', {'data-menu-toggle': '', 'aria-expanded': 'false'});
    const slot = el('div', {class: 'release-search-slot'}, el('input', {}));
    const login = el('a', {class: 'release-login', href: '/login'});
    const menu = el('div', {id: 'release-menu'}, ...groups, slot, login, language);
    const nav = el('nav', {'data-release-nav': ''}, el('a', {href: '/'}), toggle, menu, el('a', {class: 'release-github'}));
    const main = el('main', {}, el('a', {href: '/content'}));
    const body = el('body', {}, nav, main);
    doc.append(body);
    doc.querySelector = selector => body.matches(selector) ? body : body.querySelector(selector);

    let now = 0; let nextId = 0; const timers = new Map();
    const media = {matches: desktop, listeners: [], addEventListener(type, fn) { this.listeners.push(fn); }};
    let hovered = body;
    const ancestors = node => { const list = []; for (; node; node = node.parent) list.push(node); return list; };
    const pointer = (target, pointerType = 'mouse') => {
        const before = ancestors(hovered); const after = ancestors(target);
        for (const node of before.filter(n => !after.includes(n))) dispatch(node, 'pointerleave', {pointerType});
        for (const node of after.filter(n => !before.includes(n)).reverse()) dispatch(node, 'pointerenter', {pointerType});
        hovered = target;
    };
    const click = (target, extra = {}) => {
        const event = dispatch(target, 'click', {bubbles: true, ...extra});
        const summary = ancestors(target).find(n => n.tagName === 'SUMMARY');
        if (summary && !event.defaultPrevented) summary.parent.open = !summary.parent.open;
        return event;
    };
    const key = (target, name) => dispatch(target, 'keydown', {key: name, bubbles: true});
    const context = {
        document: doc,
        window: {matchMedia: () => media},
        setTimeout: (fn, ms) => { timers.set(++nextId, {fn, at: now + ms}); return nextId; },
        clearTimeout: id => timers.delete(id),
    };
    vm.runInNewContext(fs.readFileSync('static/js/site_navigation.js', 'utf8'), context);
    const wait = ms => {
        const until = now + ms;
        for (;;) {
            const due = [...timers].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            timers.delete(due[0]); now = due[1].at; due[1].fn();
        }
        now = until;
    };
    const breakpoint = matches => { media.matches = matches; media.listeners.forEach(fn => fn({matches})); };
    const summaries = groups.map(group => group.querySelector('summary'));
    const panels = groups.map(group => group.querySelector('.release-nav-panel'));
    const openLabels = () => [...groups, language].filter(g => g.open).map(g => [...groups, language].indexOf(g));
    return {nav, menu, toggle, groups, language, summaries, panels, slot, login, main, body, pointer, click, key, wait, breakpoint, openLabels};
}

test('resting the pointer on a group opens its panel after a short intent delay', () => {
    const {summaries, groups, pointer, wait} = setup();
    pointer(summaries[0]);
    assert.equal(groups[0].open, false, 'passing over the bar must not open panels');
    wait(250);
    assert.equal(groups[0].open, true);
});

test('a pointer that only passes over a group leaves it closed', () => {
    const {summaries, groups, main, pointer, wait} = setup();
    pointer(summaries[0]);
    wait(40);
    pointer(main);
    wait(1000);
    assert.equal(groups[0].open, false);
});

test('an open panel stays open while the pointer moves into it and closes after leaving both', () => {
    const {summaries, panels, groups, nav, main, pointer, wait} = setup();
    pointer(summaries[1]); wait(250);
    pointer(nav); // the strip of bar between trigger and panel
    wait(100);
    pointer(panels[1].querySelector('a'));
    wait(1000);
    assert.equal(groups[1].open, true, 'the panel is hoverable');
    pointer(main);
    wait(100);
    assert.equal(groups[1].open, true, 'a short grace period forgives a slip');
    pointer(panels[1]);
    wait(1000);
    assert.equal(groups[1].open, true, 'returning within the grace period keeps it open');
    pointer(main);
    wait(600);
    assert.equal(groups[1].open, false);
});

test('resting on another label swaps panels and keeps only one open', () => {
    const {summaries, menu, pointer, wait, openLabels} = setup();
    pointer(summaries[0]); wait(250);
    pointer(menu);
    pointer(summaries[2]); wait(250);
    assert.deepEqual(openLabels(), [2]);
    pointer(menu);
    pointer(summaries[0]);
    wait(1000);
    assert.deepEqual(openLabels(), [0], 'a stale close timer must not close the panel the pointer rests on');
});

test('hovering another label leaves a panel opened by click or Enter alone', () => {
    const {summaries, menu, pointer, click, wait, openLabels} = setup();
    click(summaries[0]);
    pointer(summaries[1]); wait(250);
    assert.deepEqual(openLabels(), [0], 'a clicked panel stays until a click, a click elsewhere or Escape');
    pointer(menu); wait(1000);
    assert.deepEqual(openLabels(), [0]);
    click(summaries[0]);
    summaries[2].focus();
    click(summaries[2]); // Enter on a focused summary activates it like a click
    pointer(summaries[1]); wait(250);
    assert.deepEqual(openLabels(), [2], 'Tab must still lead into the opened panel');
});

test('crossing another label on the way to the panel keeps the open panel', () => {
    const {summaries, panels, menu, main, pointer, wait, openLabels} = setup();
    pointer(summaries[2]); wait(250);
    pointer(menu); wait(30);
    pointer(summaries[1]); wait(40); // the diagonal path from the label down to the links
    pointer(panels[2].querySelector('a'));
    wait(1000);
    assert.deepEqual(openLabels(), [2]);
    pointer(summaries[1]); wait(40);
    pointer(main);
    wait(1000);
    assert.deepEqual(openLabels(), [], 'leaving across another label still closes the panel');
});

test('pointer movement never closes or swaps a panel whose link has keyboard focus', () => {
    const {summaries, panels, groups, main, pointer, wait, openLabels} = setup();
    pointer(summaries[0]); wait(250);
    summaries[0].focus();
    const link = panels[0].querySelector('a');
    link.focus(); // Tab from the trigger into the panel
    assert.equal(groups[0].open, true);
    pointer(main); wait(1000);
    assert.equal(groups[0].open, true, 'closing would drop focus to the page body');
    pointer(summaries[1]); wait(1000);
    assert.deepEqual(openLabels(), [0]);
    assert.equal(doc.activeElement, link);
});

test('a dismissal or a narrow viewport cancels a pending hover open', () => {
    const {summaries, groups, main, pointer, wait, key, breakpoint} = setup();
    pointer(summaries[1]); wait(50);
    key(main, 'Escape');
    wait(1000);
    assert.equal(groups[1].open, false, 'Escape during the pause');
    pointer(main);
    pointer(summaries[1]); wait(50);
    breakpoint(false);
    wait(1000);
    assert.equal(groups[1].open, false, 'resize during the pause');
});

test('opening the menu leaves focus on the toggle so Tab reaches the search first', () => {
    const {toggle, click} = setup({desktop: false});
    toggle.focus();
    click(toggle);
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(doc.activeElement, toggle);
});

test('clicking a hover-opened trigger keeps the panel open until a second click', () => {
    const {summaries, groups, main, pointer, click, wait} = setup();
    pointer(summaries[0]); wait(250);
    click(summaries[0]);
    assert.equal(groups[0].open, true, 'the click confirms the open panel instead of closing it');
    pointer(main); wait(1000);
    assert.equal(groups[0].open, true, 'a clicked panel does not close on pointer leave');
    pointer(summaries[0]);
    click(summaries[0]);
    assert.equal(groups[0].open, false);
});

test('touch and click toggle a group without hover timing', () => {
    const {summaries, groups, pointer, click, wait} = setup();
    pointer(summaries[1], 'touch');
    wait(1000);
    assert.equal(groups[1].open, false);
    click(summaries[1]);
    assert.equal(groups[1].open, true);
    click(summaries[1]);
    assert.equal(groups[1].open, false);
});

test('narrow screens ignore hover and keep the menu groups as click disclosures', () => {
    const {summaries, groups, pointer, click, wait} = setup({desktop: false});
    pointer(summaries[0]); wait(1000);
    assert.equal(groups[0].open, false);
    click(summaries[0]);
    assert.equal(groups[0].open, true);
});

test('Escape closes the open panel and returns focus to its trigger', () => {
    const {summaries, panels, groups, click, key} = setup();
    summaries[1].focus();
    click(summaries[1]);
    const link = panels[1].querySelector('a');
    link.focus();
    key(link, 'Escape');
    assert.equal(groups[1].open, false);
    assert.equal(doc.activeElement, summaries[1]);
});

test('ArrowDown on a trigger opens its panel and moves to the first link', () => {
    const {summaries, panels, groups, key} = setup();
    summaries[2].focus();
    const event = key(summaries[2], 'ArrowDown');
    assert.equal(event.defaultPrevented, true, 'the page must not scroll');
    assert.equal(groups[2].open, true);
    assert.equal(doc.activeElement, panels[2].querySelector('a'));
});

test('moving keyboard focus out of an open group closes its panel', () => {
    const {summaries, panels, groups, login, click} = setup();
    summaries[0].focus();
    click(summaries[0]);
    panels[0].querySelector('a').focus();
    assert.equal(groups[0].open, true, 'tabbing into the panel keeps it open');
    summaries[1].focus();
    assert.equal(groups[0].open, false);
    click(summaries[1]);
    login.focus();
    assert.equal(groups[1].open, false);
});

test('a click outside the open panel closes it', () => {
    const {summaries, panels, groups, main, nav, click} = setup();
    click(summaries[0]);
    click(panels[0]);
    assert.equal(groups[0].open, true, 'clicks inside the panel keep it open');
    click(nav);
    assert.equal(groups[0].open, false, 'a click elsewhere on the bar closes it');
    click(summaries[0]);
    click(main);
    assert.equal(groups[0].open, false);
});

test('narrow screens move the search to the top of the menu, wide ones back before login', () => {
    const {menu, slot, login, breakpoint} = setup({desktop: false});
    assert.equal(menu.firstElementChild, slot, 'focus order follows the visible order');
    breakpoint(true);
    assert.equal(menu.children[menu.children.indexOf(login) - 1], slot);
    breakpoint(false);
    assert.equal(menu.firstElementChild, slot);
});

test('Escape dismisses a hovered panel even when focus is elsewhere on the page', () => {
    const {summaries, groups, main, pointer, key, wait} = setup();
    const link = main.querySelector('a');
    link.focus();
    pointer(summaries[2]); wait(250);
    assert.equal(groups[2].open, true);
    key(link, 'Escape');
    assert.equal(groups[2].open, false);
    assert.equal(doc.activeElement, link, 'focus stays where the visitor left it');
});

test('on narrow screens one Escape closes the open group, a second closes the menu', () => {
    const {toggle, summaries, panels, groups, click, key} = setup({desktop: false});
    click(toggle);
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    click(summaries[1]);
    const link = panels[1].querySelector('a');
    link.focus();
    key(link, 'Escape');
    assert.equal(groups[1].open, false, 'the first Escape closes the group');
    assert.equal(toggle.getAttribute('aria-expanded'), 'true', 'the menu stays open');
    assert.equal(doc.activeElement, summaries[1]);
    key(summaries[1], 'Escape');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false', 'the second Escape closes the menu');
    assert.equal(doc.activeElement, toggle);
});

test('Escape closes a hovered panel but leaves focus in the nav search field', () => {
    const {summaries, groups, slot, pointer, key, wait} = setup();
    const field = slot.querySelector('input');
    field.focus();
    pointer(summaries[1]);
    wait(200);
    assert.equal(groups[1].open, true);
    key(field, 'Escape');
    assert.equal(groups[1].open, false);
    assert.equal(doc.activeElement, field, 'typing continues in the search field');
});
