// SPDX-License-Identifier: AGPL-3.0-or-later
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = () => fs.readFileSync('static/js/address_intake.js', 'utf8');
test('pages without address intake are safe when Leaflet is unavailable', () => {
    vm.runInNewContext(source(), {document: {querySelectorAll: () => []}});
});
class Element {
    constructor() {
        this.value=''; this.checked=false; this.disabled=false; this.dataset={}; this.children=[]; this.listeners={};
        const classes=new Set();
        this.classList={add: x=>classes.add(x), remove:x=>classes.delete(x), contains:x=>classes.has(x), toggle:(x,on)=>on?classes.add(x):classes.delete(x)};
    }
    addEventListener(type, listener) {this.listeners[type]=listener;}
    async fire(type) {return this.listeners[type]?.call(this);}
    appendChild(node) {this.children.push(node);}
    replaceChildren(...nodes) {this.children=nodes;}
    focus() {this.focused=true;}
    set textContent(value) {this.text=value;this.children=[];}
    get textContent() {return this.text || this.children.map(n=>n.textContent).join('');}
    set innerHTML(value) {throw new Error('HTML rendering forbidden: '+value);}
}
function setup(replies=[], options={}) {
    const elements={};
    const partial=fs.readFileSync('templates/partials/address_intake.html','utf8');
    for (const [,id,attrs] of partial.matchAll(/id="([\w-]+)"([^>]*)/g)) {
        elements[id]=new Element();
        if (/class="[^"]*\bhidden\b/.test(attrs)) elements[id].classList.add('hidden');
    }
    const states=['empty','loading','found','not-found'].map(state=>{const e=new Element();e.dataset.addressState=state;return e;});
    const roles={'interest-role':[], 'fallback-role':[]};
    const root=new Element();
    // Labels stand in for the server's translated JSON; state strings must come from it.
    const messageKeys=[
        'inputHint', 'selectedHint', 'noSuggestion', 'suggestionUnavailable', 'checking',
        'checkingHint', 'rateLimit', 'checkAddress', 'rateLimitHint', 'checkFailed',
        'memberOne', 'memberMany', 'potentialDetail', 'found', 'noNeighbours',
        'noNeighboursHint', 'networkRetry', 'emailRequired', 'registrationFailed',
        'networkError', 'interestFailed'
    ];
    const config={locale:'fr-CH', map:{center:[47,8],zoom:8},
        messages:Object.fromEntries(messageKeys.map(key=>[key,'translated:'+key]))};
    root.querySelector=selector=>selector==='[data-address-config]'
        ? {textContent:JSON.stringify(config)} : elements[selector.slice(1)];
    root.querySelectorAll=selector=>selector==='[data-address-state]'?states:roles[selector.match(/name="([^"]+)/)?.[1]] || [];
    const requests=[]; const timers=new Map(); let timerId=0;
    const context={document:{querySelectorAll:()=>[root],createElement:tag=>{const el=new Element();el.tagName=tag;return el;},createTextNode:text=>({textContent:text})},
        window:{location:{search:'?ref=invite-42'}}, URLSearchParams, Intl, console:{warn(){}},
        setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
        fetch:async(url,init)=>{requests.push({url,payload:init&&JSON.parse(init.body)});const reply=replies.shift();if(reply instanceof Error)throw reply;return {ok:true,status:200,json:async()=>reply?.body || {},...reply};},...options};
    vm.runInNewContext(source(),context);
    return {elements,root,roles,requests,context,async type(value) {elements['address-input'].value=value;await elements['address-input'].fire('input');const callbacks=[...timers.values()];timers.clear();for(const fn of callbacks)await fn();}};
}
test('suggestions are keyboard buttons with safe API labels and allow checking', async () => {
    const {elements:e,type}=setup([{body:{suggestions:[{label:'<img src=x onerror=alert(1)>',lat:47,lon:8}]}}]);
    await type('Baden');
    const suggestion=e.suggestions.children[0];
    assert.equal(suggestion.tagName,'button');
    assert.equal(suggestion.textContent,'<img src=x onerror=alert(1)>');
    await suggestion.fire('click');
    assert.equal(e['address-input'].value,'<img src=x onerror=alert(1)>');
    assert.equal(e['btn-check'].disabled,false);
    assert.equal(e.suggestions.classList.contains('hidden'),true);
    assert.equal(e['btn-check'].focused,true);
});
test('a failing map must not stop address entry', async () => {
    const {elements:e,type}=setup([{body:{suggestions:[]}}], {L:{map(){throw new Error('blocked map');}}});
    await type('Baden');
    assert.equal(e['btn-check'].disabled,false);
});
for (const [name,reply,message] of [
    ['empty',{body:{suggestions:[]}},'noSuggestion'],
    ['HTTP failure',{ok:false,status:503},'suggestionUnavailable'],
    ['network failure',new Error('offline'),'suggestionUnavailable']
]) test(`suggestion ${name} still lets visitors check a typed address`, async () => {
    const {elements:e,type,requests}=setup([reply]);
    await type('Musterweg 1, 5400 Baden');
    assert.equal(requests[0].url,'/api/suggest_addresses?q=Musterweg%201%2C%205400%20Baden');
    assert.equal(e['btn-check'].disabled,false);
    assert.equal(e['address-status'].textContent,'translated:'+message);
    assert.equal(e.suggestions.classList.contains('hidden'),true);
});
const profile={lat:47,lon:8,address:'Musterweg 1, 5400 Baden'};
for (const potential of [true,false]) test(`address match opens registration, potential=${potential}, without a map`, async () => {
    const {elements:e,requests}=setup([{body:{profile_summary:profile,potential,cluster_info:{num_members:2,autarky_percent:60}}}]);
    e['address-input'].value=profile.address;
    await e['btn-check'].fire('click');
    assert.deepEqual(requests[0],{url:'/api/check_potential',payload:{address:profile.address}});
    assert.equal(e['step-address'].classList.contains('hidden'),true);
    assert.equal(e['step-register'].classList.contains('hidden'),false);
    assert.match(e['match-info'].textContent,/translated:/);
});
for (const [name,reply,message] of [
    ['rate limit',{ok:false,status:429},'rateLimit'],
    ['API error',{ok:false,status:400,body:{error:'<script>unsafe</script>'}},null],
    ['network error',new Error('offline'),'networkRetry']
]) test(`address ${name} permits retry and safely announces the error`, async () => {
    const {elements:e}=setup([reply,{body:{profile_summary:profile,potential:false}}]);
    await e['btn-check'].fire('click');
    assert.equal(e['form-error'].textContent,message?'translated:'+message:'<script>unsafe</script>');
    assert.equal(e['btn-check'].disabled,false);
    assert.equal(e['btn-check'].textContent,'translated:checkAddress');
    await e['btn-check'].fire('click');
    assert.equal(e['form-error'].classList.contains('hidden'),true);
    assert.equal(e['step-address'].classList.contains('hidden'),true);
});
test('registration preserves locale, referral, roles, nullable solar and independent consents', async () => {
    for (const solar of ['', 'true', 'false']) for(const share of [false,true]) for(const updates of [false,true]) {
        const {elements:e,roles,requests}=setup([{body:{profile_summary:profile}},{body:{referral_link:'https://example.invalid/?ref=new'}}]);
        await e['btn-check'].fire('click');
        e['email-input'].value='person@example.invalid'; e['phone-input'].value='+41 79 000 00 00';
        roles['interest-role']=[{value:'tenant'},{value:'solar_producer'}];
        e['has-solar'].value=solar;e['consent-neighbors'].checked=share;e['consent-updates'].checked=updates;
        await e['btn-register'].fire('click');
        assert.deepEqual(requests[1],{url:'/api/register_anonymous',payload:{email:'person@example.invalid',phone:'+41 79 000 00 00',profile,
            preferred_locale:'fr-CH',roles:['tenant','solar_producer'],has_solar:solar===''?null:solar==='true',
            consents:{share_with_neighbors:share,updates_opt_in:updates},referral_code:'invite-42'}});
        assert.equal(e['step-register'].classList.contains('hidden'),true);
        assert.equal(e['step-success'].classList.contains('hidden'),false);
        assert.equal(e['referral-link'].value,'https://example.invalid/?ref=new');
    }
});
for(const [name,reply,message] of [
    ['rate limit',{ok:false,status:429},'translated:rateLimit'],
    ['server failure',{ok:false,status:500},'translated:registrationFailed'],
    ['API error',{ok:false,status:400,body:{error:'<img src=x>'}},'<img src=x>'],
    ['network failure',new Error('offline'),'translated:networkError']
]) test(`registration ${name} keeps entered data and permits retry`, async () => {
    const {elements:e}=setup([reply,{}]);e['email-input'].value='person@example.invalid';
    await e['btn-register'].fire('click');
    assert.equal(e['form-error'].textContent,message);
    assert.equal(e['email-input'].value,'person@example.invalid');
    await e['btn-register'].fire('click');
    assert.equal(e['form-error'].classList.contains('hidden'),true);
    assert.equal(e['step-success'].classList.contains('hidden'),false);
});
for (const [name,reply,message] of [
    ['rate limit',{ok:false,status:429},'translated:rateLimit'],
    ['network failure',new Error('fetch failed'),'translated:networkRetry'],
    ['API failure',{ok:false,status:400,body:{error:'<script>unsafe</script>'}},'<script>unsafe</script>']
]) test(`unresolved-address coverage ${name} can retry with preserved fields`, async () => {
    const {elements:e,roles,requests}=setup([{ok:false,status:404,body:{error:'unresolved',can_register_interest:true}},reply,{}]);
    e['address-input'].value=profile.address;await e['btn-check'].fire('click');
    assert.equal(e['interest-fallback'].classList.contains('hidden'),false);
    assert.equal(e['interest-plz'].value,'5400');assert.equal(e['interest-municipality'].value,'Baden');
    e['interest-email'].value='person@example.invalid';roles['fallback-role']=[{value:'owner'}];
    await e['btn-interest'].fire('click');
    assert.equal(e['form-error'].textContent,message);
    await e['btn-interest'].fire('click');
    assert.deepEqual(requests[2],{url:'/api/register_interest',payload:{email:'person@example.invalid',address:profile.address,plz:'5400',municipality_name:'Baden',roles:['owner'],has_solar:null,preferred_locale:'fr-CH'}});
    assert.equal(e['interest-fallback'].classList.contains('hidden'),true);
    assert.equal(e['form-error'].classList.contains('hidden'),true);
});
test('loading the script twice does not attach another intake handler', async () => {
    const {elements:e,context,requests}=setup([{body:{profile_summary:profile}}]);
    const handler=e['btn-check'].listeners.click;
    vm.runInNewContext(source(),context);
    assert.equal(e['btn-check'].listeners.click,handler);
    await e['btn-check'].fire('click');assert.equal(requests.length,1);
});
test('an older suggestion response cannot replace a newer typed address', async () => {
    let resolve;
    const delayed=new Promise(r=>{resolve=r;});let call=0;
    const {elements:e,type}=setup([], {fetch:async()=>++call===1?delayed:{ok:true,json:async()=>({suggestions:[{label:'new address'}]})}});
    const old=type('old');await Promise.resolve();await type('new');
    resolve({ok:true,json:async()=>({suggestions:[{label:'old address'}]})});await old;
    assert.equal(e.suggestions.children[0].textContent,'new address');
});
test('keyboard focus follows the address journey without the map', async () => {
    const {elements:e}=setup([{body:{profile_summary:profile}},{}]);
    await e['btn-check'].fire('click');
    assert.equal(e['email-input'].focused,true);
    e['email-input'].value='person@example.invalid';await e['btn-register'].fire('click');
    assert.equal(e['step-success'].focused,true);
});


test('Leaflet initializes with the serialized component center and zoom', () => {
    const views = [];
    const map = {setView(center, zoom) {views.push({center: Array.from(center), zoom}); return this;}};
    const layer = {addTo() {return this;}};
    const {elements} = setup([], {L: {
        map() {return map;},
        control: {zoom() {return layer;}},
        tileLayer() {return layer;}
    }});
    assert.deepEqual(views, [{center: [47, 8], zoom: 8}]);
    assert.equal(elements.map.classList.contains('hidden'), false);
});
