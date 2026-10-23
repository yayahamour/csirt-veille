"""Sources RSS maintenables, validées avant tout accès réseau."""
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

DEFAULT_SOURCES_PATH=Path(__file__).with_name('sources.toml')

@dataclass(frozen=True)
class Settings:
    db_path: Path=Path('data/csirt.sqlite3')
    poll_seconds: int=1800
    user_agent: str='CSIRT-Veille/0.2'
    max_bytes: int=4*1024*1024
    max_entries: int=500
    fetch_budget_seconds: int=45
    lease_seconds: int=120
    sources_path: Path=DEFAULT_SOURCES_PATH
    web_threads: int=4
    collection_mode: str='production'

    def __post_init__(self):
        if self.poll_seconds<1800: raise ValueError('Collecte espacée d’au moins 1800 secondes')
        if self.lease_seconds<self.fetch_budget_seconds+30: raise ValueError('Bail HTTP trop court')
        if not 1<=self.web_threads<=16: raise ValueError('CSIRT_WEB_THREADS doit être entre 1 et 16')
        if self.collection_mode!='production': raise ValueError('Seul le mode production bridé est disponible')

    @property
    def cycle_seconds(self): return 30

    @classmethod
    def from_env(cls):
        # Les anciennes variables de débridage et de matching sont ignorées.
        return cls(db_path=Path(os.getenv('CSIRT_DB_PATH','data/csirt.sqlite3')),
                   poll_seconds=int(os.getenv('CSIRT_POLL_SECONDS','1800')),
                   user_agent=os.getenv('CSIRT_USER_AGENT','CSIRT-Veille/0.2'),
                   sources_path=Path(os.getenv('CSIRT_SOURCES_PATH',str(DEFAULT_SOURCES_PATH))),
                   web_threads=int(os.getenv('CSIRT_WEB_THREADS','4')))

def load_sources(settings):
    with settings.sources_path.open('rb') as stream: document=tomllib.load(stream)
    if set(document)!={'version','sources'} or document['version']!=1: raise ValueError('version=1 et sources requis')
    sources=document['sources']
    if not isinstance(sources,list) or not sources: raise ValueError('Au moins une source requise')
    seen=set()
    for source in sources:
        allowed={'id','name','kind','enabled','poll_seconds','request','response','organization_mode','fixed_poll_interval'}
        if not isinstance(source,dict) or set(source)-allowed: raise ValueError('Champ de source inconnu')
        key=source.get('id','')
        if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',key) or key in seen:
            raise ValueError('Identifiant invalide ou dupliqué')
        seen.add(key)
        if source.get('kind')!='rss': raise ValueError('Seules les sources RSS/Atom sont acceptées')
        if not isinstance(source.get('name'),str) or not source['name'].strip(): raise ValueError('Nom requis')
        source.setdefault('enabled',True)
        source.setdefault('fixed_poll_interval',True)
        if type(source['enabled']) is not bool or type(source['fixed_poll_interval']) is not bool: raise ValueError('Booléen requis')
        source.setdefault('poll_seconds',settings.poll_seconds)
        if type(source['poll_seconds']) is not int or not 1800<=source['poll_seconds']<=31536000: raise ValueError('poll_seconds doit être au moins 1800')
        source.setdefault('organization_mode','article')
        if source['organization_mode'] not in {'article','title'}: raise ValueError('organization_mode invalide')
        request=source.get('request')
        if not isinstance(request,dict) or set(request)-{'url','method','params','headers','headers_env','json'}: raise ValueError('Requête invalide')
        url=request.get('url','')
        if not isinstance(url,str): raise ValueError('URL invalide')
        parts=urlsplit(url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.fragment: raise ValueError('URL HTTPS sans identifiants ni ancre requise')
        request.setdefault('method','GET')
        if request['method'] not in {'GET','POST'}: raise ValueError('Méthode GET ou POST requise')
        for field in ('params','headers','headers_env'):
            request.setdefault(field,{})
            if not isinstance(request[field],dict) or any(not isinstance(v,(str,int,bool,float)) for v in request[field].values()): raise ValueError(f'{field} invalide')
        for env in request['headers_env'].values():
            if not isinstance(env,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',env): raise ValueError('Variable d’en-tête invalide')
        if 'json' in request and (request['method']=='GET' or not isinstance(request['json'],dict)): raise ValueError('Corps JSON réservé à POST')
        try: json.dumps(request,allow_nan=False)
        except (ValueError,TypeError) as exc: raise ValueError('Valeurs JSON invalides') from exc
        source.setdefault('response',{})
        response=source['response']
        if not isinstance(response,dict) or set(response)-{'title','url','id','summary','published_at','updated_at','categories'}: raise ValueError('Correspondances invalides')
        for path in response.values():
            if not isinstance(path,str) or not path: raise ValueError('Chemin XML invalide')
            try: ElementTree.Element('item').find(path)
            except (SyntaxError,KeyError,TypeError) as exc: raise ValueError('Chemin XML invalide') from exc
    return sources

def source_fingerprint(source):
    content={k:source[k] for k in ('kind','request','response','organization_mode')}
    return hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
