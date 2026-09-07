"""Offline evaluation of a saved report. Never calls providers or invents labels."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from .event_intel_identity import strict_name

FACT_FIELDS = ('starts_on','ends_on','city','country')


def report_fingerprint(run):
    return hashlib.sha256(json.dumps(run,sort_keys=True,default=str).encode()).hexdigest()


def prepare_review(run):
    kept=((run.get('summary') or {}).get('selection') or {}).get('kept',[])
    return {'report_sha256':report_fingerprint(run), 'reviewers':[],
            'event_labels':{e.get('event_identity'):None for e in kept},
            'critical_fact_labels':{e.get('event_identity'):{field:None for field in FACT_FIELDS} for e in kept},
            'critical_facts_review_complete':False,'unsupported_personal_claims':None,
            'provider_reconciled_cost_usd':None,'measured_latency_seconds':None}


def number(value, upper=None):
    try:
        return (isinstance(value,(int,float)) and not isinstance(value,bool)
                and math.isfinite(value) and value >= 0 and (upper is None or value <= upper))
    except OverflowError:
        return False


def evaluate(run, reference, review=None):
    review = review or {}
    summary = run.get('summary') or {}
    kept = (summary.get('selection') or {}).get('kept', [])
    top = kept[:5]
    facts, wrong_editions, matched, blockers = [], [], set(), []
    references=reference.get('reference_events') or []
    # Reference rows identify editions, not merely series names.
    ref_keys=[tuple(strict_name(r.get(k)) for k in ('name',)+FACT_FIELDS) for r in references]
    if len(set(ref_keys)) != len(ref_keys):
        blockers.append('The reference contains duplicate edition rows.')
    for event in kept:
        name = strict_name(event.get('name'))
        hits = [(i,r) for i,r in enumerate(references)
                if name and name in {strict_name(n) for n in [r.get('name')]+r.get('aliases',[])}]
        if not hits:
            continue
        exact = [(i,r) for i,r in hits if r.get('starts_on') and r['starts_on']==event.get('starts_on')]
        if not exact:
            wrong_editions.append(event.get('name'))
            continue
        if len(exact)>1:
            exact=[(i,r) for i,r in exact if all(event.get(k) and strict_name(event[k])==strict_name(r.get(k)) for k in ('city','country'))]
        if len(exact)!=1:
            blockers.append('Reference edition is ambiguous for '+str(event.get('name')))
            continue
        index,truth=exact[0]
        # Missing facts never match another missing value.
        checks=[bool(event.get(field) and truth.get(field)) and strict_name(event[field])==strict_name(truth[field]) for field in FACT_FIELDS]
        facts.extend(checks)
        if all(checks):
            matched.add(index)
    identities=[e.get('event_identity') for e in kept]
    valid_ids=all(isinstance(key,str) and key.strip() for key in identities)
    if not valid_ids or (valid_ids and len(set(identities)) != len(identities)):
        blockers.append('Recommended events need unique nonempty edition identities.')
    labels=review.get('event_labels') or {}
    judged=[labels.get(e.get('event_identity')) for e in top] if valid_ids else []
    precision=sum(x is True for x in judged)/len(top) if top and judged and all(isinstance(x,bool) for x in judged) else None
    accuracy=sum(facts)/len(facts) if facts else None
    positives={i for i,r in enumerate(references) if r.get('independent_relevance') is True}
    fully_labeled=all(isinstance(r.get('independent_relevance'),bool) for r in references)
    recall=len(positives & matched)/len(positives) if reference.get('reference_complete') is True and fully_labeled and positives else None
    critical=review.get('critical_fact_labels') or {}
    judgments=[(critical.get(key) or {}).get(field) for key in identities for field in FACT_FIELDS] if valid_ids else []
    global_accuracy=sum(judgments)/len(judgments) if judgments and all(isinstance(x,bool) for x in judgments) else None
    if review.get('report_sha256') != report_fingerprint(run):
        blockers.append('Review is missing or belongs to a different report snapshot.')
    if recall is None:
        blockers.append('Overall discovery recall is unmeasured.')
    if review.get('critical_facts_review_complete') is not True or global_accuracy is None:
        blockers.append('Critical facts across all recommended events have not been reviewed.')
    reviewers=review.get('reviewers') or []
    names=[name.strip().casefold() for name in reviewers if isinstance(name,str) and name.strip()]
    if len(names)!=len(reviewers) or len(set(names))<2 or len(names)!=len(set(names)):
        blockers.append('Two distinct named independent reviewers have not completed adjudication.')
    if reference.get('reference_complete') is not True:
        blockers.append('Reference set is not complete; overall recall cannot be established.')
    if precision is None:
        blockers.append('Independent top-five relevance labels are incomplete.')
    personal=review.get('unsupported_personal_claims')
    if type(personal) is not int or personal<0:
        blockers.append('Personal-interaction claims need a nonnegative integer review count.')
    elif personal:
        blockers.append('Unsupported personal claims were found.')
    if wrong_editions:
        blockers.append('Known wrong editions were recommended.')
    targets=reference.get('release_targets') or {}
    for key,metric in (('overall_reference_recall',recall),('top_five_expert_precision',precision),('critical_fact_accuracy',global_accuracy)):
        if not number(targets.get(key),1):
            blockers.append('Release ratio target is missing or invalid: '+key)
        elif metric is not None and metric<targets[key]:
            blockers.append('Release target failed: '+key)
    if accuracy is None or (number(targets.get('critical_fact_accuracy'),1) and accuracy<targets['critical_fact_accuracy']):
        blockers.append('Reference-matched critical facts are unmeasured or below target.')
    for key in ('max_run_cost_usd','max_latency_seconds'):
        measurement=review.get({'max_run_cost_usd':'provider_reconciled_cost_usd','max_latency_seconds':'measured_latency_seconds'}[key])
        if not number(targets.get(key)):
            blockers.append('Release target is missing or invalid: '+key)
        elif not number(measurement) or measurement>targets[key]:
            blockers.append('Measurement is invalid, missing or exceeds release target: '+key)
    if run.get('status')!='complete' or summary.get('completion_state')=='partial':
        blockers.append('The report is not a completed, fully measured analysis.')
    return {'evaluation_version':2,'benchmark_version':reference['benchmark_version'],'client':reference['client'],
        'top_five_expert_precision':precision,'top_events_evaluated':len(top),
        'critical_fact_accuracy_on_matched_reference':accuracy,'independently_reviewed_critical_fact_accuracy':global_accuracy,
        'known_wrong_editions':wrong_editions,'matched_reference_events':[references[i]['name'] for i in sorted(matched)],
        'overall_recall':recall,'release_accepted':not blockers,'blockers':blockers,
        'report_sha256':report_fingerprint(run),
        'note':'Metrics rely on supplied independent judgments; reviewer identity is declared, not authenticated. Reference matches alone do not establish factual quality.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--reference',type=Path,default=Path('benchmarks/event_intelligence/position2.json'))
    parser.add_argument('--review',type=Path)
    parser.add_argument('--prepare-review',action='store_true')
    args=parser.parse_args()
    run=json.loads(args.report.read_text())
    if args.prepare_review:
        print(json.dumps(prepare_review(run),indent=2))
        return 0
    result=evaluate(run,json.loads(args.reference.read_text()),json.loads(args.review.read_text()) if args.review else None)
    print(json.dumps(result,indent=2))
    return 0 if result['release_accepted'] else 2


if __name__=='__main__':
    raise SystemExit(main())
