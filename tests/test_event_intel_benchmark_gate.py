import copy
import math
import pytest
from tracker import event_intel_benchmark as B


def fixture():
    event={'name':'Fixture Forum','event_identity':'edition-a','starts_on':'2027-04-01','ends_on':'2027-04-02','city':'Boston','country':'USA'}
    run={'status':'complete','summary':{'selection':{'kept':[event]}}}
    reference={'benchmark_version':2,'client':'Fixture','reference_complete':True,
               'reference_events':[dict(event,independent_relevance=True)],
               'release_targets':{'overall_reference_recall':1,'top_five_expert_precision':1,
                                  'critical_fact_accuracy':1,'max_run_cost_usd':10,'max_latency_seconds':60}}
    review=B.prepare_review(run)
    review.update(reviewers=['Reviewer A','Reviewer B'],critical_facts_review_complete=True,
                  unsupported_personal_claims=0,provider_reconciled_cost_usd=1,measured_latency_seconds=20)
    review['event_labels']['edition-a']=True
    review['critical_fact_labels']['edition-a']={key:True for key in B.FACT_FIELDS}
    return run,reference,review


def test_complete_fixture_passes_but_new_report_invalidates_labels():
    run,ref,review=fixture()
    assert B.evaluate(run,ref,review)['release_accepted']
    run['summary']['note']='new evidence'
    result=B.evaluate(run,ref,review)
    assert not result['release_accepted']
    assert any('snapshot' in blocker for blocker in result['blockers'])


@pytest.mark.parametrize('value',[math.nan,math.inf,-math.inf,-1,True,'1',None])
@pytest.mark.parametrize('field',['provider_reconciled_cost_usd','measured_latency_seconds'])
def test_invalid_measurements_never_pass(field,value):
    run,ref,review=fixture();review[field]=value
    assert not B.evaluate(run,ref,review)['release_accepted']


@pytest.mark.parametrize('value',[math.nan,math.inf,-1,True,'0.8',None,1.1])
def test_invalid_ratio_targets_never_pass(value):
    run,ref,review=fixture();ref['release_targets']['critical_fact_accuracy']=value
    assert not B.evaluate(run,ref,review)['release_accepted']


@pytest.mark.parametrize('reviewers',[['A',' a '],['','B'],['A'],[1,2]])
def test_two_distinct_named_reviewers_required(reviewers):
    run,ref,review=fixture();review['reviewers']=reviewers
    assert not B.evaluate(run,ref,review)['release_accepted']


@pytest.mark.parametrize('field',['critical_facts_review_complete','unsupported_personal_claims'])
def test_truthy_flags_and_boolean_counts_do_not_pass(field):
    run,ref,review=fixture();review[field]='true' if field=='critical_facts_review_complete' else False
    assert not B.evaluate(run,ref,review)['release_accepted']


def test_each_recommended_fact_requires_actual_boolean_judgment():
    run,ref,review=fixture()
    for value in (None,False,'true',1):
        review['critical_fact_labels']['edition-a']['city']=value
        assert not B.evaluate(run,ref,review)['release_accepted']


def test_missing_reference_facts_do_not_count_as_correct():
    run,ref,review=fixture()
    run['summary']['selection']['kept'][0]['city']=None
    ref['reference_events'][0]['city']=None
    review['report_sha256']=B.report_fingerprint(run)
    result=B.evaluate(run,ref,review)
    assert result['critical_fact_accuracy_on_matched_reference']==.75
    assert result['overall_recall']==0
    assert not result['release_accepted']


def test_same_series_editions_are_distinct_recall_cases():
    run,ref,review=fixture()
    ref['reference_events'].append(dict(ref['reference_events'][0],starts_on='2028-04-01',ends_on='2028-04-02'))
    result=B.evaluate(run,ref,review)
    assert result['overall_recall']==.5 and not result['release_accepted']


def test_ambiguous_same_date_editions_cannot_inflate_recall():
    run,ref,review=fixture()
    ref['reference_events'].append(dict(ref['reference_events'][0],city='London'))
    assert B.evaluate(run,ref,review)['overall_recall']==.5
    ref['reference_events'].append(copy.deepcopy(ref['reference_events'][0]))
    assert not B.evaluate(run,ref,review)['release_accepted']


def test_duplicate_recommendations_do_not_pass():
    run,ref,review=fixture()
    run['summary']['selection']['kept']*=2
    review['report_sha256']=B.report_fingerprint(run)
    assert not B.evaluate(run,ref,review)['release_accepted']
