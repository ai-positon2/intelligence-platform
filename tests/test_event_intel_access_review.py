from tracker import event_intel_access_review as R


def link(index=0, **changes):
    return dict({'url':'https://event.example/register/'+str(index),'source_url':'https://event.example/agenda','kind':'registration'},**changes)


def test_reads_are_bounded_deduplicated_and_do_not_follow_external_candidates():
    calls=[]
    result=R.inspect([link(i) for i in range(7)]+[link(),link(url='https://other.example/register')],
                     'event.example','2027-04-01',lambda url:calls.append(url) or {'status':'ok','text':'Registration is open'})
    assert len(calls)==4 and result['not_checked']==3
    assert result['complete'] is False


def test_terms_preserve_qualifiers_and_never_become_confirmed_availability():
    text='2027 Registration\nEarly bird fee USD 499 excludes tax\nRegistration is not sold out\nMembers only, approval required\nRegister by October 1\n© 2026 Copyright'
    result=R.inspect([link()],'event.example','2027-04-01',lambda url:{'status':'ok','text':text})['checks'][0]
    assert result['edition_status']=='requires_review'
    assert result['observed_years']==['2027']
    assert {c['field'] for c in result['claims']}=={'price','availability','eligibility','deadline'}
    assert any(c['text']=='Registration is not sold out' for c in result['claims'])
    assert all(c['support']=='literal_destination_text' for c in result['claims'])
    assert 'available' not in result
    assert result['snapshot']['text_sha256']


def test_failed_redirected_and_truncated_reads_remain_explicit():
    for data in ({'status':'blocked','note':'Forbidden'}, {'status':'ok','final_url':'https://other.example','text':'fee USD 10'}):
        result=R.inspect([link()],'event.example','2027',lambda url:data)['checks'][0]
        assert result['status']=='blocked' and result['claims']==[]
    result=R.inspect([link()],'event.example','2027',lambda url:{'status':'ok','text':'fee USD 10','truncated':True})['checks'][0]
    assert result['truncated'] and result['edition_status']=='requires_review'


def test_failed_fetch_has_no_stale_fallback():
    def fail(url):
        raise RuntimeError('synthetic failure')
    result=R.inspect([link()],'event.example','2027',fail)['checks'][0]
    assert result['status']=='error' and result['claims']==[]
