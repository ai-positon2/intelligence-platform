from tracker import event_intel_plan_review as R


def view():
    return {'run_id':1,'profile':{'id':1},'plan':{'version':1,'action':'attend'},
            'event':{'event_identity':'fixture'},'fit':{'topic_matches':[{'text':'fixture'}],'people':[]}}


def reviewed(v):
    labels={key:True for key in R.required_checks(v)}
    return {'reviewers':[{'name':name,'cases':{R.fingerprint(v):dict(labels)}} for name in ('Reviewer A','Reviewer B')]}


def test_packet_is_unreviewed_and_cannot_pass_without_people():
    v=view()
    p=R.packet([v])
    assert all(value is None for value in p['cases'][0]['checks'].values())
    assert not R.evaluate([v],p)['review_passed']


def test_two_independent_labels_apply_only_to_exact_snapshot():
    v=view(); review=reviewed(v)
    assert R.evaluate([v],review)['review_passed']
    assert not R.evaluate([v],review)['release_accepted']
    changed=dict(v,plan={'version':2,'action':'sponsor'})
    assert not R.evaluate([changed],review)['review_passed']
    review['reviewers'][1]['name']=' reviewer a '
    assert not R.evaluate([v],review)['review_passed']


def test_negative_missing_or_non_boolean_labels_never_pass():
    v=view()
    for value in (None,False,1,'true'):
        review=reviewed(v)
        review['reviewers'][1]['cases'][R.fingerprint(v)]['action_appropriate']=value
        assert not R.evaluate([v],review)['review_passed']
    assert not R.evaluate([],{'reviewers':[]})['review_passed']
