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
