import pytest
from tracker.event_intel_costs import estimate


def call(model='claude-sonnet-5',**over):
    usage=dict(input_tokens=1000000,output_tokens=1000000,cache_read_input_tokens=1000000,
               cache_creation_input_tokens=0,web_search_requests=100)
    usage.update(over)
    return {'model':model,'result':{'usage':usage,'search_count':999}}


def test_model_specific_rates_and_billed_search_count():
    assert estimate([call()])['estimated_usd']==13.2
    assert estimate([call('claude-sonnet-4-6')])['estimated_usd']==19.3
    assert estimate([call(),call('claude-sonnet-4-6')])['estimated_usd']==32.5


@pytest.mark.parametrize('row',[{'model':'claude-sonnet-5','result':None},call('unknown-model'),
                              call(input_tokens=True),call(output_tokens=-1),call(cache_creation_input_tokens=500),
                              call(web_search_requests=None)])
def test_missing_or_unpriceable_usage_is_not_zero(row):
    r=estimate([call(),row])
    assert r['estimated_usd'] is None and not r['complete_usage']
    assert r['known_calls_estimated_usd']==13.2
    assert len(r['unresolved'])==1


def test_empty_ledger_is_not_free_research():
    assert estimate([])['estimated_usd'] is None


def test_zero_reported_usage_is_valid_zero_estimate():
    r=estimate([call(input_tokens=0,output_tokens=0,cache_read_input_tokens=0,web_search_requests=0)])
    assert r['estimated_usd']==0 and r['complete_usage']


def test_a_call_without_usage_is_counted_at_its_reservation_and_flagged_partial():
    """One call that never reported usage used to blank the whole run's
    figure. It is now counted at the allowance reserved for it, and the
    figure says it is partial rather than passing for a measurement."""
    reserved={'model':'claude-sonnet-5','result':None,'reserved_tokens':500000,'reserved_searches':8}
    r=estimate([call(),reserved])
    # 13.2 measured + 500k tokens at $2/M ($1.00) + 8 searches ($0.08)
    assert r['estimated_usd']==14.28
    assert r['partial'] and not r['complete_usage']
    assert r['known_calls_estimated_usd']==13.2
    assert r['reserved_calls_estimated_usd']==1.08
    assert r['calls_estimated_from_reservation']==1
    assert r['unresolved'][0]['counted_from_reservation']
    assert 'Partial' in r['basis']


def test_an_abandoned_call_says_why_it_has_no_usage():
    row={'model':'claude-sonnet-5','result':{'abandoned':True},'reserved_tokens':1000000,'reserved_searches':0}
    r=estimate([row])
    assert r['estimated_usd']==2.0 and r['partial']
    assert 'stopped before its outcome' in r['unresolved'][0]['reason']


def test_an_unpriced_model_reservation_is_priced_at_the_highest_configured_rate():
    row={'model':'mystery-model','result':None,'reserved_tokens':1000000,'reserved_searches':0}
    assert estimate([row])['estimated_usd']==3.0


def test_a_complete_ledger_is_not_partial():
    r=estimate([call()])
    assert not r['partial'] and r['calls_estimated_from_reservation']==0
