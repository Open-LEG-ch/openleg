// SPDX-License-Identifier: AGPL-3.0-or-later
// Exercise the shipped widget and its event handlers without a browser dependency.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.value = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    reportValidity() { return true; }
}
function descendants(node, tag) {
    return node.children.flatMap(child => typeof child === 'object' ? [...(child.tagName === tag ? [child] : []), ...descendants(child, tag)] : []);
}
const labels = {parameters:'Paramètres', requestBody:'Contenu JSON', schema:'Schéma', execute:'Exécuter', response:'Réponse', responses:'Réponses', invalidJson:'JSON non valide', loading:'Chargement', networkError:'Erreur réseau', requestFailed:'La requête a échoué.', errors:{'bfs_number required':'Veuillez indiquer le numéro OFS de la commune.'}};
function widget(operation, method='get', response={status:200,body:'<script>untrusted</script>'}) {
    const root = new Element('root');
    const requests = [];
    const context = {window:{}, URLSearchParams, Option: class extends Element {constructor(label, value) {super('option');this.textContent=label;this.value=value;}},
        document:{createElement:tag => new Element(tag), createTextNode:text => text, documentElement:{lang:'fr-CH'}},
        fetch:async (url, options) => {requests.push({url,options});return {status:response.status,text:async () => response.body};}};
    vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../static/js/api_reference.js'),'utf8'),context);
    context.window.initializeApiReference(root,{info:{title:'API',description:'Description'},servers:[{url:'/api/v1'}],paths:{'/test':{[method]:operation}}},labels);
    return {root,requests,form:descendants(root,'form')[0]};
}
test('decimal parameters keep fractions and documented bounds', () => {
    const {root} = widget({parameters:[{name:'ratio',in:'query',schema:{type:'number',minimum:0,maximum:100}},{name:'count',in:'query',schema:{type:'integer'}}]});
    const [ratio,count] = descendants(root,'input');
    assert.equal(ratio.type,'number'); assert.equal(ratio.step,'any');
    assert.equal(ratio.min,0); assert.equal(ratio.max,100); assert.equal(count.step,'1');
});
test('nested request examples retain required fields, defaults, enums and array structure', () => {
    const schema = {type:'object',required:['bfs_number'],properties:{bfs_number:{type:'integer',example:261},grid_level:{type:'string',enum:['NE7','NE5'],default:'NE7'},scenario:{type:'object',properties:{size:{type:'integer',default:10}}},buildings:{type:'array',minItems:2,items:{type:'object',properties:{lat:{type:'number',example:46.9}}}}}};
    const {root} = widget({requestBody:{required:true,content:{'application/json':{schema}}}},'post');
    const value = JSON.parse(descendants(root,'textarea')[0].value);
    assert.equal(value.bfs_number,261);assert.equal(value.grid_level,'NE7');assert.equal(value.scenario.size,10);
    assert.equal(value.buildings.length,2); assert.equal(value.buildings[0].lat,46.9);
    assert.ok(descendants(root,'pre').some(node => node.textContent.includes('"required"') && node.textContent.includes('"bfs_number"')));
});
test('query execution preserves decimal value and selected language', async () => {
    const {root,requests,form} = widget({parameters:[{name:'ratio',in:'query',schema:{type:'number'}}]});
    descendants(root,'input')[0].value='20.5';
    await form.listeners.submit({preventDefault(){}});
    assert.equal(requests.length,1); assert.equal(requests[0].url,'/api/v1/test?ratio=20.5&lang=fr-CH');
    assert.equal(descendants(root,'pre').at(-1).textContent,'<script>untrusted</script>');
    assert.equal(descendants(root,'p').at(-1).textContent,'Réponse · HTTP 200');
});
test('invalid JSON does not issue a request', async () => {
    const {root,requests,form} = widget({requestBody:{content:{'application/json':{schema:{type:'object'}}}}},'post');
    descendants(root,'textarea')[0].value='{invalid';
    await form.listeners.submit({preventDefault(){}});
    assert.equal(requests.length,0); assert.equal(descendants(root,'p').at(-1).textContent,labels.invalidJson);
});

test('human API errors are localized while the protocol payload stays unchanged', async () => {
    const payload={error:'bfs_number required',bfs_number:2554};
    const {root,form}=widget({},'get',{status:400,body:JSON.stringify(payload)});
    await form.listeners.submit({preventDefault(){}});
    assert.equal(descendants(root,'p').at(-1).textContent,'Réponse · HTTP 400 · Veuillez indiquer le numéro OFS de la commune.');
    assert.deepEqual(JSON.parse(descendants(root,'pre').at(-1).textContent),payload);
});

test('unknown errors use a safe localized fallback and preserve diagnostic text', async () => {
    const payload={error:'<script>hostile</script>'};
    const {root,form}=widget({},'get',{status:500,body:JSON.stringify(payload)});
    await form.listeners.submit({preventDefault(){}});
    assert.equal(descendants(root,'p').at(-1).textContent,'Réponse · HTTP 500 · La requête a échoué.');
    assert.equal(descendants(root,'pre').at(-1).textContent,JSON.stringify(payload,null,2));
    assert.equal(descendants(root,'script').length,0);
});

test('non-object JSON and inherited property names always use the localized fallback', async () => {
    for (const payload of [null, [], {error:'constructor'}, {error:'toString'}, {error:'__proto__'}]) {
        const {root,form}=widget({},'get',{status:500,body:JSON.stringify(payload)});
        await form.listeners.submit({preventDefault(){}});
        assert.equal(descendants(root,'p').at(-1).textContent,'Réponse · HTTP 500 · La requête a échoué.');
        assert.equal(descendants(root,'pre').at(-1).textContent,JSON.stringify(payload,null,2));
    }
});
