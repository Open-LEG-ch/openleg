// SPDX-License-Identifier: AGPL-3.0-or-later
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/sandbox_urls.js'), 'utf8');
const prefix = '/v/' + 'a'.repeat(32);
const origin = 'https://sandbox.example.org';
function browser(generation = prefix) {
    const requests = [], listeners = {};
    const context = {
        URL, Request,
        location: {origin, href: origin + prefix + '/dashboard'},
        document: {currentScript: {dataset: {prefix: generation}}, addEventListener(name, callback) {listeners[name] = callback;}},
        fetch(input, options) {requests.push({input, options}); return Promise.resolve({status: 200});}
    };
    vm.runInNewContext(source, context);
    return {context, requests, listeners};
}
test('final request URLs bind once without retargeting other villages or external origins', async () => {
    const {context, requests} = browser();
    const foreign = '/v/' + 'b'.repeat(32) + '/dashboard/export';
    for (const url of ['/dashboard/export', prefix + '/dashboard/export', foreign, 'https://receiver.example.org/events', new URL(origin + '/api/v1/municipalities?kanton=all')]) await context.fetch(url);
    assert.deepEqual(requests.map(item => item.input), [
        origin + prefix + '/dashboard/export', origin + prefix + '/dashboard/export',
        origin + foreign, 'https://receiver.example.org/events',
        origin + prefix + '/api/v1/municipalities?kanton=all'
    ]);
});
test('binding a Request preserves its method, CSRF header, body and caller options', async () => {
    const {context, requests} = browser();
    const input = new Request(origin + '/dashboard/profile', {method: 'POST', headers: {'X-CSRF-Token': 'fictional-csrf'}, body: 'consumption=7200'});
    const options = {credentials: 'same-origin'};
    await context.fetch(input, options);
    const sent = requests[0].input;
    assert.equal(sent.url, origin + prefix + '/dashboard/profile');
    assert.equal(sent.method, 'POST');
    assert.equal(sent.headers.get('X-CSRF-Token'), 'fictional-csrf');
    assert.equal(await sent.text(), 'consumption=7200');
    assert.equal(requests[0].options, options);
});
test('dynamic links and forms bind at browser activation', () => {
    const {listeners} = browser();
    const link = {href: origin + '/how-it-works#interner-strompreis'};
    listeners.click({target: {closest() {return link;}}});
    assert.equal(link.href, origin + prefix + '/how-it-works#interner-strompreis');
    const form = formElement('/gemeinde/register');
    listeners.submit({target: form});
    assert.equal(form.getAttribute('action'), origin + prefix + '/gemeinde/register');
});
test('OpenAPI server and path concatenate into one scoped request', async () => {
    const {context, requests} = browser();
    const server = '/api/v1', path = '/municipalities';
    await context.fetch(server + path + '?kanton=all');
    assert.equal(requests[0].input, origin + prefix + '/api/v1/municipalities?kanton=all');
    assert.equal(server, '/api/v1');
    assert.equal(path, '/municipalities');
});
test('malformed generation configuration fails before request hooks install', () => {
    assert.throws(() => browser('/v/../../other'), /Invalid village generation/);
});

function formElement(action) {
    const attributes = {action};
    return {tagName: 'FORM', action: {name: 'action'},
        getAttribute(name) {return attributes[name] ?? null;},
        setAttribute(name, value) {attributes[name] = value;}};
}
test('middle-click scopes a dynamic link before opening another tab', () => {
    const {listeners} = browser();
    const link = {href: origin + '/dashboard/invoices'};
    assert.equal(typeof listeners.auxclick, 'function');
    listeners.auxclick({target: {closest() {return link;}}});
    assert.equal(link.href, origin + prefix + '/dashboard/invoices');
});
test('submit scopes the action attribute and the submitter override', () => {
    const {listeners} = browser();
    const form = formElement('/dashboard/profile');
    const submitter = formElement('/dashboard/export');
    submitter.setAttribute('formaction', '/dashboard/export');
    listeners.submit({target: form, submitter});
    assert.equal(form.getAttribute('action'), origin + prefix + '/dashboard/profile');
    assert.equal(submitter.getAttribute('formaction'), origin + prefix + '/dashboard/export');
    assert.equal(form.action.name, 'action');
});
test('a submitter without an override keeps the form destination', () => {
    const {listeners} = browser();
    const form = formElement('/dashboard/profile');
    const submitter = formElement(null);
    listeners.submit({target: form, submitter});
    assert.equal(form.getAttribute('action'), origin + prefix + '/dashboard/profile');
    assert.equal(submitter.getAttribute('formaction'), null);
});
