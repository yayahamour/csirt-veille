import json

import httpx
from fastapi.testclient import TestClient

from csirt.alert_status import publication_assessment,summarize
from csirt.api import create_app
from csirt.collector import collect_source
from csirt.db import connect
from conftest import rss


def publication(status='unknown',categories=(),title='Exemple',source='frenchbreaches'):
    return {'id':1,'source_id':source,'source_name':source,'title':title,'status':status,
            'categories':list(categories),'url':'https://example.org/publication','published_at':'2026-10-21T08:00:00+00:00'}


def test_claim_index_repeats_publisher_marker_without_numeric_probability():
    result=publication_assessment(publication('claimed',['credible']))
    assert result['status']=='claimed'
    assert result['claim_index']['value']=='credible'
    assert result['claim_index']['numeric_score'] is None
    assert result['basis']==[{'field':'category','value':'credible','status':'claimed'}]
    assert result['independent_verification'] is False
    result=publication_assessment(publication('unreliable_claim',title='🔴 Exemple'))
    assert result['claim_index']['value']=='unreliable'
    assert result['basis'][0]['value']=='🔴'


def test_confirmation_is_never_guessed_from_the_article_title():
    result=publication_assessment(publication(title='Exemple ne confirme pas la fuite'))
    assert result['status']=='unknown' and result['claim_index']['value']=='unspecified'
    result=publication_assessment(publication('confirmed',['confirmed']))
    assert result['claim_index']['value']=='not_applicable'


def test_different_source_positions_are_preserved_and_flagged():
    result=summarize([publication('claimed',['credible']),publication('unreliable_claim',['unreliable'],source='bonjour-la-fuite')])
    assert result['status']=='mixed' and result['divergent']
    assert result['claim_index']['value']=='mixed'
    assert len(result['sources'])==2
    assert result['counts']['claimed']==1 and result['counts']['unreliable_claim']==1
    result=summarize([publication('claimed',['credible']),publication('confirmed',title='🟢 Exemple',source='bonjour-la-fuite')])
    assert result['status']=='confirmed' and result['divergent'] is False
    assert result['claim_index']['value']=='credible'
    assert result['sources'][0]['status']=='claimed'


def test_incident_status_tracks_source_updates_and_filter_matches_divergence(settings):
    def collect(source,title,extra,guid):
        with connect(settings) as db:
            db.execute('UPDATE sources SET next_poll_at=0 WHERE id=?',(source,))
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=rss(title=title,extra=extra,guid=guid)))) as client:
            assert collect_source(settings,client,source)['result']=='ok'
    collect('frenchbreaches','Exemple','<category>credible</category>','one')
    collect('bonjour-la-fuite','🔴 Exemple','','two')
    with TestClient(create_app(settings)) as api:
        assert api.get('/incidents?alert_status=mixed').json()['total']==1
        assert api.get('/incidents/1').json()['alert_assessment']['divergent']
        collect('bonjour-la-fuite','🟢 Exemple','','two')
        assert api.get('/incidents?alert_status=mixed').json()['total']==0
        assert api.get('/incidents?alert_status=confirmed').json()['total']==1
        assert api.get('/incidents/1').json()['alert_assessment']['sources'][0]['url']


def test_missing_status_and_html_like_categories_remain_plain_data(settings):
    p=publication(categories=['<script>alert(1)</script>'])
    assert publication_assessment(p)['status']=='unknown'
    assert summarize([p])['claim_index']['numeric_score'] is None
    assert summarize([])['status']=='unknown'
