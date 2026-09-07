"""Offline, version-bound independent review packets for Phase 3 plan views.

Inputs are authenticated plan API responses saved locally by the evaluator.
This tool does not contact reviewers, providers or production services.
"""
import argparse
import hashlib
import json
from pathlib import Path


def fingerprint(view):
    # Tie labels to the exact captured evidence and saved plan, including version.
    return hashlib.sha256(json.dumps(view,sort_keys=True,default=str).encode()).hexdigest()


def required_checks(view):
    checks=['action_appropriate','access_claims_accurate','no_unsupported_personal_claims']
    if (view.get('fit') or {}).get('topic_matches'):
        checks.append('topic_matches_useful')
    if (view.get('fit') or {}).get('people'):
        checks.append('person_company_claims_accurate')
    return checks


def packet(views):
    cases=[]
    for view in views:
        cases.append({'fingerprint':fingerprint(view), 'run_id':view.get('run_id'),
                      'event_identity':(view.get('event') or {}).get('event_identity'),
                      'plan_version':(view.get('plan') or {}).get('version'),
                      'checks':{key:None for key in required_checks(view)}})
    return {'reviewers':[], 'cases':cases,
            'instructions':'Two distinct reviewers each supply a checks object per fingerprint. Leave unknown judgments null. Any disagreement or failure requires correction and a new review. This is not the Phase 2 release benchmark.'}


def evaluate(views, review):
    blockers=[]
    if not views:
        blockers.append('No captured plan views were provided.')
    hashes=[fingerprint(view) for view in views]
    if len(set(hashes)) != len(hashes):
        blockers.append('Duplicate plan snapshots do not count as independent cases.')
    reviewers=review.get('reviewers') or []
    names=[r.get('name','').strip().casefold() for r in reviewers if isinstance(r,dict)]
    if len(names)!=len(reviewers) or len(set(names)-{''})<2 or len(set(names))!=len(names) or '' in names:
        blockers.append('At least two distinct named reviewers are required.')
    for view, digest in zip(views,hashes):
        if not (view.get('profile') or {}).get('id') or not (view.get('plan') or {}).get('version'):
            blockers.append('A case lacks a saved client-specific plan.')
        for index, reviewer in enumerate(reviewers):
            if not isinstance(reviewer,dict):
                continue
            labels=(reviewer.get('cases') or {}).get(digest)
            if not isinstance(labels,dict):
                blockers.append('Reviewer %d has no labels for snapshot %s.' % (index+1,digest[:12]))
                continue
            for key in required_checks(view):
                if labels.get(key) is not True:
                    blockers.append('Reviewer %d: %s is false or unreviewed for %s.' % (index+1,key,digest[:12]))
    return {'cases':len(views),'review_passed':not blockers,'blockers':blockers,
            'release_accepted':False,
            'note':'Review applies only to these captured plans. It does not establish discovery recall, production readiness, calibrated scores or general recommendation quality.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('views',type=Path,help='JSON array of captured plan API responses')
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    views=json.loads(args.views.read_text())
    if not isinstance(views,list) or not all(isinstance(view,dict) for view in views):
        parser.error('views must be an array of objects')
    result=evaluate(views,json.loads(args.review.read_text())) if args.review else packet(views)
    print(json.dumps(result,indent=2))
    return 0 if not args.review or result['review_passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
