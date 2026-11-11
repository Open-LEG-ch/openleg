// Render the HTTP docs response with the shipped widget, without a browser dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {html, spec} = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
    constructor() { this.children = []; this.textContent = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute() {}
    addEventListener() {}
}
const root = new Element();
const context = {
    window: {},
    Option: Element,
    document: {
        createElement: () => new Element(),
        createTextNode: text => text,
        getElementById: () => root,
    },
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../static/js/api_reference.js'), 'utf8'), context);
context.loadApiReference = (target, url, labels) => context.window.initializeApiReference(target, spec, labels);
const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\b[^>]*>/gi)]
    .map(match => match[1]).find(source => source.includes('loadApiReference('));
vm.runInNewContext(script, context);
function texts(node) {
    if (typeof node === 'string') return [node];
    return [node.textContent, ...node.children.flatMap(texts)];
}
process.stdout.write(JSON.stringify(texts(root)));
