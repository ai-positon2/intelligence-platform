"""Full report exports retain evidence and enforce the existing account boundary."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
import app as appmod
from tracker import event_intel_store as S

BASE='/p2/strategic-agents/event-conference-intelligence/runs/8'


def client():
    c=appmod.app.test_client()
    with c.session_transaction() as session:
        session['google_user']={'email':'export@position2.com','name':'Export test'}
    return c


@pytest.mark.parametrize('status',['complete','failed','running'])
def test_export_preserves_report_and_ledgers(monkeypatch,status):
    run={'id':8,'mode':'lookup','status':status,'summary':{'completion_state':'partial'},
         'execution_ledger':{'unknown_provider_outcomes':1},
         'evidence_ledger':[{'support':'model_reported'}]}
    monkeypatch.delenv('DATABASE_URL',raising=False)
    get=Mock(side_effect=lambda rid,email: deepcopy(run))
    monkeypatch.setattr(S,'get_run',get)
    for name in ('get_events','get_participants','get_sources'):
        monkeypatch.setattr(S,name,lambda rid: [])
    c=client()
    normal=c.get(BASE)
    saved=c.get(BASE+'?download=1')
    assert saved.status_code == 200
    assert saved.json == normal.json
    assert saved.json['execution_ledger']['unknown_provider_outcomes'] == 1
    assert saved.headers['Content-Disposition'] == 'attachment; filename="event-research-8.json"'
    assert saved.headers['Cache-Control'] == 'private, no-store'
    get.assert_called_with(8,'export@position2.com')


def test_export_of_another_accounts_run_remains_not_found(monkeypatch):
    monkeypatch.setattr(S,'get_run',lambda rid,email: None)
    assert client().get(BASE+'?download=1').status_code == 404


def test_export_requires_login():
    response=appmod.app.test_client().get(BASE+'?download=1')
    assert response.status_code in (302,401,403)
    assert 'attachment' not in response.headers.get('Content-Disposition','')


def test_download_uses_ledger_estimate_and_preserves_legacy_amount(monkeypatch):
    from tracker import event_intel_evidence as E, event_intel_jobs as J
    monkeypatch.setenv('DATABASE_URL','configured-but-not-used')
    monkeypatch.setattr(S,'get_run',lambda rid,email: {'id':rid,'mode':'lookup','summary':{'spend':{'usd':20}}})
    monkeypatch.setattr(E,'get_observations',lambda *a: [])
    monkeypatch.setattr(J,'ledger',lambda *a: {'cost_estimate':{'estimated_usd':None}})
    for name in ('get_events','get_participants','get_sources'):
        monkeypatch.setattr(S,name,lambda rid: [])
    result=client().get(BASE+'?download=1')
    assert result.status_code==200
    spend=result.json['summary']['spend']
    assert spend['usd'] is None
    assert spend['legacy_fixed_rate_usd']==20
