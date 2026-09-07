from datetime import date, timedelta
from tracker import event_intel_access as A
from tracker import event_intel_planning as P
import pytest


def test_links_require_observed_labels_and_organizer_host():
    text = '''Register [https://event.example/register]
Become a sponsor [https://event.example/sponsor]
Agenda [https://event.example/agenda]
Register [https://evil.example/register]
Register [https://event.example.evil.example/register]
Register [https://user:pass@event.example/register]
Sponsors [https://event.example/roster]
'''
    links = A.discover(text, 'https://event.example/exhibitors', 'event.example')
    assert [link['kind'] for link in links] == ['registration','sponsor','agenda']
    assert all(link['support'] == 'observed_link_only' for link in links)
    assert A.discover(text, 'https://other.example', 'event.example') == []
    assert A.discover('No links here', 'https://event.example', 'event.example') == []


def test_budget_and_unavailable_access_block_review():
    event = {'starts_on':str(date.today()+timedelta(days=30))}
    plan = {'action':'attend','planned_budget':'100','estimated_total_cost':'101','access_status':'unavailable'}
    result = A.assess(event, plan, [], [])
    assert result['status'] == 'blocked_for_review'
    assert any('exceeds' in check for check in result['checks'])
    assert any('unavailable' in check for check in result['checks'])


def test_links_and_user_confirmation_never_imply_ready_to_book():
    plan = {'action':'meetings','planned_budget':'100','estimated_total_cost':'0','access_status':'confirmed'}
    result = A.assess({'starts_on':str(date.today()+timedelta(days=30))}, plan,
                      [{'timing':'historical'}], [{'kind':'meetings'}])
    assert result['status'] == 'needs_review'
    assert any('current-edition' in check for check in result['checks'])
    assert any('user-reported' in check for check in result['checks'])


@pytest.mark.parametrize('payload', [
    {'access_status':'confirmed'}, {'access_status':'unavailable','access_checked_on':'2099-01-01'},
    {'access_status':'invented'}, {'estimated_total_cost':'-1'},
])
def test_access_and_cost_validation(payload):
    with pytest.raises(ValueError):
        P.validate(dict(action='attend',currency='USD',**payload))


def test_unknown_and_zero_cost_remain_distinct():
    assert P.validate({'action':'attend','currency':'USD'})['estimated_total_cost'] is None
    assert P.validate({'action':'attend','currency':'USD','estimated_total_cost':'0'})['estimated_total_cost'] == '0.00'
