'use strict';
(function(root){
  const KEY='csirt.alerts.reading.v2';
  const LEGACY='csirt.alerts.seen.v1';
  const integer=value=>Number.isSafeInteger(value)&&value>=0;
  function valid(value){
    if(!value||typeof value.generation!=='string'||!value.generation||!integer(value.sequence))return null;
    const seen={};
    if(value.seen&&typeof value.seen==='object'&&!Array.isArray(value.seen)){
      for(const [id,sequence] of Object.entries(value.seen)){
        if(/^[1-9][0-9]{0,15}$/.test(id)&&integer(sequence)&&sequence>value.sequence)seen[id]=sequence;
      }
    }
    return {generation:value.generation,sequence:value.sequence,seen};
  }
  function merge(left,right){
    if(!left)return right;
    if(!right||left.generation!==right.generation)return left;
    const result={generation:left.generation,sequence:Math.max(left.sequence,right.sequence),seen:{}};
    for(const state of [left,right])for(const [id,sequence] of Object.entries(state.seen)){
      if(sequence>result.sequence)result.seen[id]=Math.max(result.seen[id]||0,sequence);
    }
    return result;
  }
  class ReadingProfile{
    constructor(storage){this.storage=storage;this.available=true;this.state=null;this.reload();}
    read(key){
      let value;
      try{value=this.storage().getItem(key);}catch{this.available=false;return null;}
      try{return valid(JSON.parse(value));}catch{return null;}
    }
    reload(){
      const stored=this.read(KEY)||(!this.state?this.read(LEGACY):null);
      this.state=this.available?merge(stored,this.state):merge(this.state,stored);
      return this.state;
    }
    receive(value){
      this.reload();
      let incoming;
      try{incoming=valid(JSON.parse(value));}catch{return;}
      if(!incoming||!this.state||incoming.generation!==this.state.generation)return;
      this.state=merge(this.state,incoming);
      if(this.token()!==JSON.stringify(this.read(KEY)))this.save();
    }
    save(reset=false){
      if(!reset)this.state=merge(this.state,this.read(KEY));
      try{this.storage().setItem(KEY,JSON.stringify(this.state));}catch{this.available=false;}
    }
    ensure(snapshot){
      if(!this.state||this.state.generation!==snapshot.generation||this.state.sequence>snapshot.sequence){
        this.state={generation:snapshot.generation,sequence:snapshot.sequence,seen:{}};
        this.save(true);return true;
      }
      return false;
    }
    isUnread(item){return !!this.state&&item.activity_seq>this.state.sequence&&item.activity_seq>(this.state.seen[String(item.id)]||0);}
    markSeen(item){
      if(!item||!integer(item.activity_seq)||!/^[1-9][0-9]{0,15}$/.test(String(item.id)))return false;
      this.reload();
      if(!this.isUnread(item))return false;
      this.state.seen[String(item.id)]=item.activity_seq;this.save();return true;
    }
    markAll(sequence,generation){
      this.reload();
      if(!this.state||!integer(sequence)||(generation&&generation!==this.state.generation))return;
      this.state.sequence=Math.max(this.state.sequence,sequence);
      this.state.seen=Object.fromEntries(Object.entries(this.state.seen).filter(([,value])=>value>this.state.sequence));
      this.save();
    }
    compact(snapshot){
      if(snapshot.new_count===0&&Object.keys(this.state.seen).length)this.markAll(snapshot.sequence,snapshot.generation);
    }
    payload(){return {...this.state,seen:{...this.state.seen}};}
    token(){return JSON.stringify(this.state);}
  }
  const api={ReadingProfile,KEY,LEGACY,valid,merge};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.CSIRTReading=api;
})(globalThis);
