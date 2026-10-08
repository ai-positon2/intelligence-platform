"""The report route keeps its evidence and ledgers, and the PDF export keeps
the account boundary. The JSON download was replaced by a PDF on 2026-10-08."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
import app as appmod
from tracker import event_intel_store as S

BASE='/p2/strategic-agents/event-conference-intelligence/runs/8'
HTML='<div class="evi-sec"><h3>Answer</h3><p>Pharma Forum scored 90.</p></div>'


def client():
    c=appmod.app.test_client()
    with c.session_transaction() as session:
        session['google_user']={'email':'export@position2.com','name':'Export test'}
    return c


@pytest.mark.parametrize('status',['complete','failed','running'])
def test_the_report_preserves_its_ledgers(monkeypatch,status):
    run={'id':8,'mode':'lookup','status':status,'summary':{'completion_state':'partial'},
         'execution_ledger':{'unknown_provider_outcomes':1},
         'evidence_ledger':[{'support':'model_reported'}]}
    monkeypatch.delenv('DATABASE_URL',raising=False)
    get=Mock(side_effect=lambda rid,email: deepcopy(run))
    monkeypatch.setattr(S,'get_run',get)
    for name in ('get_events','get_participants','get_sources'):
        monkeypatch.setattr(S,name,lambda rid: [])
    got=client().get(BASE)
    assert got.status_code == 200
    assert got.json['execution_ledger']['unknown_provider_outcomes'] == 1
    assert got.headers['Cache-Control'] == 'private, no-store'
    assert 'Content-Disposition' not in got.headers
    get.assert_called_with(8,'export@position2.com')


def test_the_report_uses_the_ledger_estimate_and_keeps_the_legacy_amount(monkeypatch):
    from tracker import event_intel_evidence as E, event_intel_jobs as J
    monkeypatch.setenv('DATABASE_URL','configured-but-not-used')
    monkeypatch.setattr(S,'get_run',lambda rid,email: {'id':rid,'mode':'lookup','summary':{'spend':{'usd':20}}})
    monkeypatch.setattr(E,'get_observations',lambda *a: [])
    monkeypatch.setattr(J,'ledger',lambda *a: {'cost_estimate':{'estimated_usd':None}})
    for name in ('get_events','get_participants','get_sources'):
        monkeypatch.setattr(S,name,lambda rid: [])
    result=client().get(BASE)
    assert result.status_code==200
    spend=result.json['summary']['spend']
    assert spend['usd'] is None
    assert spend['legacy_fixed_rate_usd']==20


# ── the PDF ─────────────────────────────────────────────────────────────────

def _post(c, body):
    return c.post(BASE+'/pdf', json=body)


def test_a_finished_report_comes_back_as_a_named_pdf(monkeypatch):
    get=Mock(return_value={'id':8,'status':'complete','query':'Cvent'})
    monkeypatch.setattr(S,'get_run',get)
    r=_post(client(), {'html':HTML,'title':'Cvent: Conference Analysis','subtitle':'7 events'})
    assert r.status_code==200
    assert r.headers['Content-Type']=='application/pdf'
    assert r.headers['Content-Disposition']=='attachment; filename="cvent-conference-analysis-8.pdf"'
    assert r.headers['Cache-Control']=='private, no-store'
    assert r.data[:5]==b'%PDF-'
    get.assert_called_with(8,'export@position2.com')


def test_another_accounts_run_cannot_be_exported(monkeypatch):
    monkeypatch.setattr(S,'get_run',lambda rid,email: None)
    assert _post(client(), {'html':HTML}).status_code==404


@pytest.mark.parametrize('status',['running','queued','failed'])
def test_a_run_that_did_not_finish_is_not_exported(monkeypatch,status):
    monkeypatch.setattr(S,'get_run',lambda rid,email: {'id':8,'status':status})
    r=_post(client(), {'html':HTML})
    assert r.status_code==409 and 'not finished' in r.json['error']


@pytest.mark.parametrize('html',[None,'','   ',123,'x'*(8*1024*1024+1)])
def test_an_empty_or_oversized_report_is_refused_in_words(monkeypatch,html):
    monkeypatch.setattr(S,'get_run',lambda rid,email: {'id':8,'status':'complete'})
    r=_post(client(), {'html':html})
    assert r.status_code==400 and r.json['error']


def test_export_requires_login():
    r=appmod.app.test_client().post(BASE+'/pdf', json={'html':HTML})
    assert r.status_code in (302,401,403)
    assert r.headers.get('Content-Type')!='application/pdf'


def test_a_cross_site_post_is_refused(monkeypatch):
    monkeypatch.setattr(S,'get_run',lambda rid,email: {'id':8,'status':'complete'})
    r=client().post(BASE+'/pdf', json={'html':HTML}, headers={'Origin':'https://evil.example'})
    assert r.status_code==403


def test_the_json_download_is_gone():
    from pathlib import Path
    page=Path('templates/event_conference_intelligence.html').read_text()
    assert 'JSON' not in page[page.index('class="evi-drawer-actions"'):page.index('id="drawerBody"')]
    assert '?download=1' not in page
    assert "request.args.get('download')" not in Path('app.py').read_text()
