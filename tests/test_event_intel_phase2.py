"""Failure-focused evidence and execution regressions. No paid API calls."""
import json
import os
from unittest.mock import Mock
import pytest
from tracker import event_intel_evidence as E, event_intel_harvest as H
from tracker import event_intel_jobs as J, event_intel_store as S


def test_chunking_retains_the_tail_and_bounds_every_model_input():
    text = '\n'.join('Exhibitor Company %04d https://company%d.example' % (i,i) for i in range(1000))
    pieces = list(E.chunks(text))
    assert len(pieces) > 1 and max(map(len,pieces)) <= 12000
    assert 'Company 0999' in pieces[-1]
    for line in text.splitlines():
        assert any(line in piece for piece in pieces)


def test_invented_entities_and_personal_attendance_are_withheld():
    rows = [dict(org_name='Invented',role='exhibitor'),dict(org_name='Acme',role='attendee_declared'),
            dict(org_name='Acme',role='exhibitor',person_name='Made Up',org_domain='invented.example')]
    kept,rejected = E.supported_rows(rows,'Exhibitors: Acme', 'exhibitor_list')
    assert len(kept)==1 and len(rejected)==2
    assert kept[0]['person_name'] is None and kept[0]['org_domain'] is None
    assert kept[0]['evidence']['status']=='literal_support_only'


def test_classifier_label_alone_cannot_establish_the_published_role():
    assert E.supported_rows([dict(org_name='Acme',role='speaker')], 'Acme', 'speaker_list')[0]==[]


def test_partial_chunk_failure_preserves_good_rows_and_reports_incompleteness(monkeypatch):
    monkeypatch.setattr(E,'chunks',lambda text: iter(['Exhibitors Acme', 'broken']))
    def extract(text,*args):
        return dict(rows=[dict(org_name='Acme',role='exhibitor')],note='',error=None) if 'Acme' in text else dict(rows=[],error={'kind':'timeout','detail':'Timed out'})
    monkeypatch.setattr(H,'_extract_chunk',extract)
    result=H.extract_participants('full original','https://event.example','exhibitor_list','Forum')
    assert len(result['rows'])==1 and result['error'] is None
    assert result['coverage']['chunks_read']==1 and not result['coverage']['complete']
    assert result['snapshot']['text_sha256']==E.text_hash('full original')


def test_job_context_reaches_parallel_model_calls():
    token=J.CURRENT.set({'run_id':44})
    try:
        with J.ContextExecutor(max_workers=1) as executor:
            assert executor.submit(lambda: J.CURRENT.get()['run_id']).result()==44
    finally:
        J.CURRENT.reset(token)
    assert J.CURRENT.get() is None


sql = pytest.mark.skipif(not os.getenv('DATABASE_URL'),reason='requires disposable PostgreSQL')


def new_job(label):
    email='phase2-'+label+'@position2.com'
    rid=J.start(email,'lookup','Forum',{'email':email},label)
    return rid,email


@sql
def test_submission_is_idempotent_but_rejects_different_inputs():
    rid,email=new_job('idempotent')
    assert J.start(email,'lookup','Forum',{'email':email},'idempotent')==rid
    with pytest.raises(ValueError):J.start(email,'lookup','Different',{'email':email},'idempotent')
    with J.db() as conn,conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM evi_runs WHERE email=%s',(email,))
        assert cur.fetchone()[0]==1
    J.cancel(rid,email)


@sql
def test_expired_worker_is_fenced_and_cancellation_is_owned():
    rid,email=new_job('fenced')
    job=J.claim()
    assert job['run_id']==rid
    assert J.cancel(rid,'other@position2.com') is False
    token=J.CURRENT.set(job)
    try:
        with J.db() as conn,conn.cursor() as cur:
            cur.execute("UPDATE evi_jobs SET lease_until=now()-interval '1 second' WHERE run_id=%s",(rid,))
        assert S.save_event(rid,{'name':'Stale event'}) is None
    finally:
        J.CURRENT.reset(token)
    assert S.get_events(rid)==[]
    assert J.cancel(rid,email)
    assert S.get_run(rid,email)['stage']=='cancelled'


@sql
def test_stage_result_and_provider_response_survive_resume(monkeypatch):
    rid,email=new_job('resume')
    job=J.claim()
    token=J.CURRENT.set(job)
    calls=[]
    def research(value):
        calls.append(value)
        return {'answer':value}
    try:
        assert J.stage('research',research,7)=={'answer':7}
        assert J.stage('research',research,7)=={'answer':7}
        assert calls==[7]
        reservation=J.reserve_call('system','user','test-model',100,0)
        J.finish_call(reservation['id'],{'text':'saved','usage':{'output_tokens':3}},20)
        assert J.reserve_call('system','user','test-model',100,0)['cached']['text']=='saved'
        pending=J.reserve_call('system','different','test-model',100,0)
        assert pending['cached'] is None
        with pytest.raises(RuntimeError,match='unknown outcome'):
            J.reserve_call('system','different','test-model',100,0)
    finally:
        J.CURRENT.reset(token)
    assert J.ledger(rid,email)['unknown_provider_outcomes']==1
    assert J.ledger(rid,'other@position2.com') is None
    J.cancel(rid,email)


@sql
def test_daily_account_budget_blocks_before_another_provider_call(monkeypatch):
    rid,email=new_job('budget')
    job=J.claim()
    token=J.CURRENT.set(job)
    monkeypatch.setenv('EVI_DAILY_CALL_LIMIT','1')
    try:
        J.reserve_call('system','one','test-model',100,0)
        with pytest.raises(RuntimeError,match='budget'):
            J.reserve_call('system','two','test-model',100,0)
    finally:
        J.CURRENT.reset(token)
    J.cancel(rid,email)


@sql
def test_catalog_observations_remain_run_owned_and_unverified():
    rid,email=new_job('catalog')
    event={'name':'Forum 2027','starts_on':'2027-04-01','website':'https://forum.example','country':'USA','city':'Boston'}
    key=E.record_event(rid,event)
    assert E.record_event(rid,event)==key
    observations=E.get_observations(rid,email)
    assert len(observations)==5
    assert all(row['support']=='model_reported' for row in observations)
    assert E.get_observations(rid,'other@position2.com')==[]
    J.cancel(rid,email)


def test_historical_sponsors_cannot_become_next_years_roster(monkeypatch):
    text='Thank you to our 2026 Sponsors\nAcme\n2027 sponsorship opportunities'
    monkeypatch.setattr(H,'fetch_page',lambda url:dict(status='ok',http_status=200,note='',text=text,spa=None))
    extract=Mock()
    monkeypatch.setattr(H,'extract_participants',extract)
    got=H.harvest_page({'url':'https://forum.example/sponsors','kind':'sponsor_list','edition':'2027'},'Forum')
    assert got['rows']==[] and got['source']['coverage']['edition_mismatch']
    extract.assert_not_called()


@sql
def test_worker_reclaims_a_lease_and_replays_completed_research(monkeypatch):
    from tracker import event_intel_pipeline as P
    rid,email=new_job('worker')
    old_job=J.claim()
    calls=[]
    def research():
        calls.append('paid call')
        return {'name':'Recovered Forum'}
    marker=J.CURRENT.set(old_job)
    try:
        J.stage('resolve-test',research)
        assert S.save_event(rid,{'name':'Partial old write'})
        with J.db() as conn,conn.cursor() as cur:
            cur.execute("UPDATE evi_jobs SET lease_until=now()-interval '1 second' WHERE run_id=%s",(rid,))
    finally:
        J.CURRENT.reset(marker)
    def pipeline(run_id,*args,**kwargs):
        result=J.stage('resolve-test',research)
        assert S.save_event(run_id,result)
        S.update_run(run_id,status='complete',summary={'test':'resumed'})
    monkeypatch.setattr(P,'run_job',pipeline)
    assert J.run_once()
    assert calls==['paid call']
    assert [r['name'] for r in S.get_events(rid)]==['Recovered Forum']
    assert J.ledger(rid,email)['state']=='complete'
    assert J.ledger(rid,email)['attempts']==2


@sql
def test_profile_product_and_company_characteristics_reach_prompts():
    from tracker.event_intel_discover import profile_brief
    from tracker.event_intel_workroom import profile_brief as qualification_brief
    profile=dict(client_name='Position2',classification='b2b_to_marketing',selected_product='B2B demand generation',
        what_they_sell='Growth marketing services',firmographics='B2B SaaS with a marketing team')
    pid=S.save_profile('phase2-profile@position2.com',profile)
    saved=S.get_profile(pid,'phase2-profile@position2.com')
    for render in (profile_brief,qualification_brief):
        assert profile['selected_product'] in render(saved)
        assert profile['firmographics'] in render(saved)


def test_benchmark_cannot_turn_missing_review_into_a_pass():
    from tracker.event_intel_benchmark import evaluate
    from pathlib import Path
    reference=json.loads(Path('benchmarks/event_intelligence/position2.json').read_text())
    result=evaluate({'status':'complete','summary':{'selection':{'kept':[]}}},reference)
    assert result['release_accepted'] is False
    assert result['top_five_expert_precision'] is None and result['overall_recall'] is None
    assert any('labels' in b for b in result['blockers'])


def test_similar_words_and_domains_do_not_count_as_entity_evidence():
    assert E.supported_rows([dict(org_name='Acme',role='exhibitor')], 'Exhibitors: AcmeOther', 'exhibitor_list')[0]==[]
    rows,_=E.supported_rows([dict(org_name='Acme',role='exhibitor',org_domain='acme.com')],
                          'Exhibitors: Acme https://notacme.com', 'exhibitor_list')
    assert rows[0]['org_domain'] is None


def test_cancellation_cannot_leave_an_approved_saved_selection():
    from tracker import event_intel_report as report, event_intel_rubric as rubric
    row={'name':'Cancelled Forum','starts_on':'2027-01-01','ends_on':'2027-01-02','total':90}
    profile={'max_events':15}
    run={'status':'failed','error':'Cancelled','summary':{'selection':report.selection_snapshot(rubric.rank([row]),profile)}}
    shown=report.present_run(run,profile,[row],{})
    assert shown['summary']['selection']['kept']==[]
    assert shown['summary']['selection']['incomplete'][0]['name']==row['name']


def test_reviewed_aliases_join_series_only_on_the_organizer_hosts():
    before=E.catalog_series_identity({'name':'INBOUND 2026','website':'https://www.inbound.com'})
    after=E.catalog_series_identity({'name':'UNBOUND 2027','website':'https://unbound.hubspot.com'})
    assert before[:2]==after[:2]
    unrelated=E.catalog_series_identity({'name':'INBOUND','website':'https://unrelated.example'})
    assert unrelated[2] is None and unrelated[:2]!=after[:2]


def test_known_sold_out_and_cancelled_editions_are_not_actionable():
    from tracker.event_intel_policy import eligibility
    row={'name':'UNBOUND','availability':'sold_out'}
    assert any('sold out' in r for r in eligibility(row,{}))
    assert any('cancelled' in r for r in eligibility(dict(row,availability='cancelled'),{}))


@sql
def test_version_mismatch_preserves_partial_results_and_does_not_call_models(monkeypatch):
    from tracker import event_intel_pipeline as P
    rid,email=new_job('version-change')
    assert S.save_event(rid,{'name':'Existing partial evidence'})
    with J.db() as conn,conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET payload=jsonb_set(payload,'{runtime_versions}','{}'::jsonb) WHERE run_id=%s",(rid,))
    pipeline=Mock()
    monkeypatch.setattr(P,'run_job',pipeline)
    assert J.run_once()
    pipeline.assert_not_called()
    assert S.get_events(rid)[0]['name']=='Existing partial evidence'
    assert S.get_run(rid,email)['status']=='failed'
