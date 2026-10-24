import test from 'node:test';
import assert from 'node:assert/strict';
import {AWARD_VARIANTS,renderAwardArt,trophyVariantClass} from '../work-trophy-art.js';
import {locale,setLocale,t} from '../i18n.js';

const expected=['enamel','crystal','order','cup','relic','crown','shield','compass','laurel','sun','peak','book','phoenix','orbit','obelisk'];
test('fifteen stable immutable award IDs in agreed order',()=>{
  assert.deepEqual(AWARD_VARIANTS.map(v=>v.id),expected);
  assert.ok(Object.isFrozen(AWARD_VARIANTS));
  assert.ok(AWARD_VARIANTS.every(v=>Object.isFrozen(v)&&v.name&&v.description));
});
test('metadata uses live locale getters without freezing translated strings at import',()=>{
  const previous=locale();
  try {
    assert.equal(typeof Object.getOwnPropertyDescriptor(AWARD_VARIANTS[0],'name').get,'function');
    assert.equal(typeof Object.getOwnPropertyDescriptor(AWARD_VARIANTS[0],'description').get,'function');
    setLocale('en');assert.equal(AWARD_VARIANTS[0].name,t('Золотая эмаль'));
    setLocale('ru');assert.equal(AWARD_VARIANTS[0].name,'Золотая эмаль');
  } finally {setLocale(previous);}
});
test('all full trophies and simplified compact emblems have distinct authored geometry',()=>{
  const full=expected.map(id=>renderAwardArt(id).replace(/class="[^"]*"/g,''));
  const compact=expected.map(id=>renderAwardArt(id,{compact:true}).replace(/class="[^"]*"/g,''));
  assert.equal(new Set(full).size,15);assert.equal(new Set(compact).size,15);
  for(const id of expected){
    const big=renderAwardArt(id),small=renderAwardArt(id,{compact:true});
    assert.match(big,/viewBox="0 0 96 82"/);assert.match(small,/viewBox="0 0 24 24"/);
    assert.ok(small.length<big.length,'compact is genuinely simplified');
    assert.doesNotMatch(big+small,/<(?:script|image|foreignObject|animate)|\bon\w+=|(?:https?:|javascript:|url\()/i);
  }
});
test('variant IDs and category colours are whitelisted, never interpolated as markup',()=>{
  for(const value of ['" onload="alert(1)','<script>','__proto__','toString',null,undefined,{}]){
    assert.equal(trophyVariantClass(value),'wta-variant-enamel');
    assert.equal(renderAwardArt(value),renderAwardArt('enamel'));
  }
  for(const color of ['red','var(--x)','url(https://example.com)','" onload="x',null,{}])assert.equal(renderAwardArt('relic',{categoryColor:color}),renderAwardArt('relic'));
  assert.match(renderAwardArt('relic',{categoryColor:'#a0B'}),/fill="#a0B"/);
  assert.match(renderAwardArt('relic',{categoryColor:'#123ABC',compact:true}),/fill="#123ABC"/);
});
test('pure renderer is stable and creates no SVG ID collisions between instances',()=>{
  const a=renderAwardArt('compass',{categoryColor:'#123456'});
  assert.equal(a,renderAwardArt('compass',{categoryColor:'#123456'}));
  assert.doesNotMatch(a,/\bid=/);
  assert.match(a,/aria-hidden="true" focusable="false"/);
});
