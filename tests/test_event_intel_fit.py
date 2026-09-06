from datetime import date, timedelta
import pytest
from tracker import event_intel_fit as F


def test_topics_are_bounded_and_deduplicated():
    assert F.topics('Demand Generation\ndemand generation\nAI strategy') == ['Demand Generation','AI strategy']
    for value in ('ab', 'x'*101, '\n'.join('topic '+str(i) for i in range(21)), ['topic']):
        with pytest.raises(ValueError):
            F.topics(value)


def test_excerpt_capture_requires_organizer_agenda_and_does_not_join_lines():
    text = '2027 Agenda\nAccount based marketing strategy\nAlice Smith from Acme\n© 2026 Copyright'
    excerpts = F.agenda_evidence(text,'https://event.example/agenda','event.example','agenda')
    assert excerpts[0]['text'] == 'Account based marketing strategy'
    assert excerpts[0]['observed_years'] == ['2027']
    assert 'Alice' not in excerpts[0]['text']
    assert F.agenda_evidence(text,'https://other.example','event.example','agenda') == []
    assert F.agenda_evidence(text,'https://event.example','event.example','exhibitors') == []


def test_phrase_matching_keeps_historical_and_unknown_editions_separate():
    future = date.today()+timedelta(days=400)
    excerpt = {'text':'Demand generation strategy','source_url':'https://event.example/agenda', 'observed_years':[future.year]}
    result = F.compare({'starts_on':str(future)}, {'topic_interests':['demand generation','generation strategy','AI']},[],[excerpt])
    assert result['topic_matches'][0]['topics'] == ['demand generation','generation strategy']
    assert result['topic_matches'][0]['timing'] == 'announced'
    assert result['unmatched_topics'] == ['AI']
    assert F.compare({'starts_on':str(future)}, {'topic_interests':['demand']},[],[dict(excerpt, observed_years=[])])['topic_matches'][0]['timing'] == 'edition_not_established'
    past = date.today()-timedelta(days=400)
    assert F.timing(str(past), [past.year]) == 'historical'


def person():
    return {'org_name':'Acme','org_domain':'acme.example','person_name':'Alice Smith','person_title':'CMO',
            'source_url':'https://event.example/speakers', 'role':'speaker',
            'evidence':{'status':'literal_support_only','org_domain':'acme.example','person_name':'alice smith'}}


def test_people_require_literal_name_and_domain_and_do_not_invent_titles():
    row = person()
    plan = {'target_domains':['acme.example']}
    result = F.compare({}, plan,[row],[])
    assert result['people'][0]['name'] == 'Alice Smith'
    assert result['people'][0]['title'] is None
    assert result['people'][0]['timing'] == 'edition_not_established'
    for field in ('person_name','org_domain','status'):
        changed = dict(row, evidence=dict(row['evidence'], **{field:'unsupported'}))
        assert F.compare({},plan,[changed],[])['people'] == []
    assert F.compare({}, {'target_domains':['other.example']},[row],[])['people'] == []


def test_harvest_records_agenda_excerpts_without_assigning_sessions(monkeypatch):
    from tracker import event_intel_harvest as H
    monkeypatch.setattr(H,'fetch_page',lambda url:{'status':'ok','http_status':200,'note':'',
        'text':'2027 Agenda\nDemand generation strategy\nAlice Smith of Acme'})
    monkeypatch.setattr(H,'extract_participants',lambda *a:{'rows':[],'note':'','error':None})
    result = H.harvest_page({'url':'https://event.example/agenda','kind':'agenda','edition':'2027'},'Forum','event.example')
    assert result['source']['agenda_excerpts'][0]['text'] == 'Demand generation strategy'
    assert result['source']['agenda_excerpts'][1]['text'] == 'Alice Smith of Acme'
    assert result['rows'] == []
