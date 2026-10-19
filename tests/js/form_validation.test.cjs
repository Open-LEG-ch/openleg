// SPDX-License-Identifier: AGPL-3.0-or-later
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
class Element {
    constructor() {this.listeners={};this.attributes={};this.style={};this.classList={add(){}};this.labels=[];}
    addEventListener(name, fn) {this.listeners[name]=fn;}
    getAttribute(name) {return this.attributes[name] || null;}
    setAttribute(name, value) {this.attributes[name]=value;}
    append(...nodes) {this.children=nodes;}
    before(node) {this.wrapper=node;}
    setCustomValidity(text) {this.message=text;}
    focus() {this.focused=true;}
}
const labels={date:'Veuillez saisir une date valide.',number:'Veuillez saisir un nombre valide.',chooseFile:'Choisir un fichier',noFile:'Aucun fichier sélectionné'};
function setup(files=[]) {
    const listeners={};
    const document={documentElement:{lang:'fr-CH'},getElementById:() => ({textContent:JSON.stringify(labels)}),querySelectorAll:() => files,createElement:() => new Element(),addEventListener:(name,fn) => {listeners[name]=fn;}};
    vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../static/js/form_validation.js'),'utf8'),{document,HTMLInputElement:Element,HTMLTextAreaElement:Element,HTMLSelectElement:Element,Intl});
    return listeners;
}
test('partially entered date reports a date error while numbers keep number errors', () => {
    const handlers=setup();
    for (const type of ['date','number']) {
        const input=new Element();input.type=type;input.validity={badInput:true};
        handlers.invalid({target:input});
        assert.equal(input.message,type === 'date' ? labels.date : labels.number);
        handlers.input({target:input});assert.equal(input.message,'');
    }
    const empty=new Element();empty.type='date';empty.validity={};
    handlers.invalid({target:empty});assert.equal(empty.message,'');
});
test('localized file button retains field context and file status for accessibility', () => {
    const input=new Element();input.id='query-attachment';input.labels=[{textContent:'Justificatif PDF, facultatif, maximum 2 Mo'}];input.attributes['aria-describedby']='attachment-help';
    setup([input]);
    const [button,status]=input.wrapper.children;
    assert.match(button.getAttribute('aria-label'),/Justificatif PDF, facultatif, maximum 2 Mo/);
    assert.ok(status.id); assert.ok(button.getAttribute('aria-describedby').includes(status.id));
    assert.ok(button.getAttribute('aria-describedby').includes('attachment-help'));
    input.listeners.invalid();assert.equal(button.focused,true);
    input.files=[{name:'facture.pdf'}];input.listeners.change();assert.equal(status.textContent,'facture.pdf');
});
