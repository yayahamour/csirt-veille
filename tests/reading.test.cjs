const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {ReadingProfile,KEY,LEGACY}=require('../csirt/web/reading.js');
class Storage{
  constructor(){this.items=new Map();}
  getItem(key){return this.items.get(key)??null;}
  setItem(key,value){this.items.set(key,value);}
}
const start=(profile,sequence=10)=>profile.ensure({generation:'database-a',sequence});

test('nouveau navigateur : historique initial vu, futures alertes non vues',()=>{
  const profile=new ReadingProfile(()=>new Storage());
  assert.equal(start(profile),true);
  assert.equal(profile.isUnread({id:1,activity_seq:10}),false);
  assert.equal(profile.isUnread({id:2,activity_seq:11}),true);
});
test('deux navigateurs indépendants et progression après réouverture',()=>{
  const a=new Storage(),b=new Storage();
  const alice=new ReadingProfile(()=>a),bob=new ReadingProfile(()=>b);
  start(alice);start(bob);
  alice.markSeen({id:1,activity_seq:11});
  assert.equal(alice.isUnread({id:1,activity_seq:11}),false);
  assert.equal(bob.isUnread({id:1,activity_seq:11}),true);
  const reopened=new ReadingProfile(()=>a);
  assert.equal(reopened.isUnread({id:1,activity_seq:11}),false);
  assert.equal(reopened.isUnread({id:1,activity_seq:12}),true);
});
test('lecture ciblée : laisser les alertes plus anciennes non vues',()=>{
  const storage=new Storage(),profile=new ReadingProfile(()=>storage);start(profile);
  profile.markSeen({id:2,activity_seq:13});
  assert.equal(profile.isUnread({id:1,activity_seq:11}),true);
  assert.equal(profile.isUnread({id:2,activity_seq:13}),false);
});
test('deux onglets fusionnent leurs lectures au lieu de les écraser',()=>{
  const storage=new Storage();const a=new ReadingProfile(()=>storage);start(a);
  const b=new ReadingProfile(()=>storage);
  a.markSeen({id:1,activity_seq:11});b.markSeen({id:2,activity_seq:12});a.reload();
  assert.equal(a.isUnread({id:1,activity_seq:11}),false);
  assert.equal(a.isUnread({id:2,activity_seq:12}),false);
  b.markAll(12);a.markSeen({id:3,activity_seq:13});b.reload();
  assert.equal(b.state.sequence,12);
  assert.equal(b.isUnread({id:3,activity_seq:13}),false);
});
test('écritures concurrentes : les événements récupèrent une lecture écrasée',()=>{
  const storage=new Storage(),a=new ReadingProfile(()=>storage);start(a);
  const b=new ReadingProfile(()=>storage);
  const one=JSON.stringify({generation:'database-a',sequence:10,seen:{'1':11}});
  const two=JSON.stringify({generation:'database-a',sequence:10,seen:{'2':12}});
  a.state.seen['1']=11;b.state.seen['2']=12;
  storage.setItem(KEY,two);b.receive(one);a.receive(two);
  const reopened=new ReadingProfile(()=>storage);
  assert.equal(reopened.isUnread({id:1,activity_seq:11}),false);
  assert.equal(reopened.isUnread({id:2,activity_seq:12}),false);
});
test('un acquittement ancien ne se reporte pas sur une nouvelle base',()=>{
  const storage=new Storage(),profile=new ReadingProfile(()=>storage);start(profile);
  profile.ensure({generation:'database-b',sequence:2});
  profile.markAll(100,'database-a');
  assert.equal(profile.state.sequence,2);
});
test('tout marquer comme vu conserve les arrivées après la photographie',()=>{
  const storage=new Storage(),profile=new ReadingProfile(()=>storage);start(profile);
  profile.markAll(12);
  assert.equal(profile.isUnread({id:1,activity_seq:12}),false);
  assert.equal(profile.isUnread({id:2,activity_seq:13}),true);
});
test('migration de l’ancien repère local sans perdre la progression',()=>{
  const storage=new Storage();storage.setItem(LEGACY,JSON.stringify({generation:'database-a',sequence:7}));
  const profile=new ReadingProfile(()=>storage);
  assert.equal(profile.ensure({generation:'database-a',sequence:10}),false);
  assert.equal(profile.isUnread({id:1,activity_seq:8}),true);
  profile.markSeen({id:1,activity_seq:8});
  assert.ok(storage.getItem(KEY));assert.equal(profile.state.sequence,7);
});
test('changement de base : ne pas appliquer les anciennes lectures aux nouveaux identifiants',()=>{
  const storage=new Storage(),profile=new ReadingProfile(()=>storage);start(profile);
  profile.markSeen({id:1,activity_seq:12});
  assert.equal(profile.ensure({generation:'database-b',sequence:2}),true);
  assert.deepEqual(profile.payload(),{generation:'database-b',sequence:2,seen:{}});
});
test('stockage refusé : garder les lectures en mémoire pour cette ouverture',()=>{
  const profile=new ReadingProfile(()=>{throw new Error('blocked');});start(profile);
  profile.markSeen({id:1,activity_seq:11});profile.reload();
  assert.equal(profile.available,false);
  assert.equal(profile.isUnread({id:1,activity_seq:11}),false);
});
test('quota atteint après migration : ne pas restaurer un ancien profil stocké',()=>{
  const storage=new Storage();storage.setItem(KEY,JSON.stringify({generation:'database-a',sequence:10,seen:{}}));
  storage.setItem=()=>{throw new Error('quota');};
  const profile=new ReadingProfile(()=>storage);profile.ensure({generation:'database-b',sequence:2});
  profile.markSeen({id:1,activity_seq:3});profile.reload();
  assert.equal(profile.state.generation,'database-b');
  assert.equal(profile.isUnread({id:1,activity_seq:3}),false);
});
test('toutes les lectures acquittées compactent les exceptions',()=>{
  const storage=new Storage(),profile=new ReadingProfile(()=>storage);start(profile);
  profile.markSeen({id:1,activity_seq:11});
  profile.compact({generation:'database-a',sequence:12,new_count:0});
  assert.deepEqual(profile.payload(),{generation:'database-a',sequence:12,seen:{}});
});
test('profil corrompu réinitialisé et valeurs invalides ignorées',()=>{
  const storage=new Storage();storage.setItem(KEY,'bad-json');
  const profile=new ReadingProfile(()=>storage);start(profile);
  assert.equal(profile.available,true);
  assert.equal(profile.markSeen({id:'invalid',activity_seq:11}),false);
  assert.equal(profile.markSeen({id:1,activity_seq:NaN}),false);
});

test('application : compteur en arrière-plan, tableau actualisé au retour, sans rechargement',async()=>{
  const storage=new Storage(),elements=new Map(),listeners=new Map();
  function element(id){
    if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',value:'',checked:false,hidden:false,
      addEventListener(){},setAttribute(){},classList:{toggle(){}},requestSubmit(){}});
    return elements.get(id);
  }
  const document={visibilityState:'visible',title:'Observatoire · CSIRT',activeElement:null,
    getElementById:element,querySelectorAll:()=>[],querySelector:()=>element('thead'),
    addEventListener:(name,handler)=>listeners.set(name,handler)};
  const window={localStorage:storage,addEventListener:(name,handler)=>listeners.set(name,handler)};
  let interval,sequence=0,revision='a';const calls=[];
  const context=vm.createContext({document,window,location:{hash:'#incidents'},URLSearchParams,AbortController,
    CSIRTReading:{ReadingProfile,KEY,LEGACY},setTimeout:()=>1,clearTimeout(){},
    setInterval:(handler,ms)=>{assert.equal(ms,60000);interval=handler;},
    fetch:async(url,options)=>{
      calls.push(url);
      const snapshot={generation:'database-a',sequence,revision,new_count:sequence,collector:{active:false}};
      let data=snapshot;
      if(url==='/dashboard')data={period:{starts_at:'2026-10-22T15:00:00Z',ends_at:'2026-10-23T15:00:00Z'},incidents:sequence,observations:sequence,multiple_sources:0,sources:4,statuses:[],by_source:[]};
      if(url==='/sources')data=[];
      if(url.startsWith('/incidents?'))data={total:sequence,items:[],snapshot};
      if(url==='/reading/updates')assert.equal(JSON.parse(options.body).sequence,0);
      return {ok:true,json:async()=>data};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../csirt/web/app.js'),'utf8'),context);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));await settle();
  const initialTables=calls.filter(url=>url.startsWith('/incidents?')).length;
  assert.equal(document.title,'Observatoire · CSIRT');
  document.visibilityState='hidden';sequence=2;revision='b';interval();await settle();
  assert.equal(document.title,'(2) Observatoire · CSIRT');
  assert.equal(calls.filter(url=>url.startsWith('/incidents?')).length,initialTables);
  document.visibilityState='visible';listeners.get('visibilitychange')();await settle();
  assert.equal(calls.filter(url=>url.startsWith('/incidents?')).length,initialTables+1);
  assert.equal(element('nav-count').textContent,2);
  assert.equal(element('result-count').textContent,'2 incidents · toutes les alertes');
});
