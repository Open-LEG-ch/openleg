// SPDX-License-Identifier: AGPL-3.0-or-later
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/sandbox_operator.js'), 'utf8');

for (const [kind, label] of [['human', 'Personne'], ['service', 'Service'], ['agent', 'Agent']]) {
    test(`credential creation and rotation display the French ${kind} label`, async () => {
        const elements = {};
        const element = id => elements[id] ??= {
            value: '', options: [], listeners: {},
            addEventListener(event, callback) {this.listeners[event] = callback;},
            add(option) {this.options.push(option);}
        };
        const root = element('operator-console');
        root.dataset = {community: 'leg', csrf: 'csrf', actorLabels: JSON.stringify({
            human: 'Personne', service: 'Service', agent: 'Agent'
        })};
        root.querySelectorAll = () => [];
        let name = 'Integration';
        const context = {
            document: {getElementById: element},
            Option: class {},
            FormData: class {
                get(key) {return {name, actor_kind: kind}[key] ?? '';}
                getAll() {return ['membership.read'];}
            },
            fetch: async () => ({
                status: 201,
                text: async () => JSON.stringify({
                    credential: {id: 'client', name, actor_kind: kind, agent_label: 'Assistant', active: true},
                    token: 'token', webhook_secret: 'secret'
                })
            })
        };
        vm.runInNewContext(source, context);
        await element('credential-create').listeners.submit({preventDefault() {}, target: {}});
        const clients = element('credential-id');
        assert.equal(clients.options[0].textContent, `Integration · ${label} · Assistant · true`);
        name = 'Renamed';
        await element('credential-manage').listeners.submit({
            preventDefault() {}, submitter: {value: 'rotate'}
        });
        assert.equal(clients.options.length, 1);
        assert.equal(clients.options[0].textContent, `Renamed · ${label} · Assistant · true`);
    });
}
