"""Offline evaluation of a saved report. Never calls providers or invents labels."""
import argparse
import json
from pathlib import Path
from .event_intel_identity import strict_name


def evaluate(run, reference, review=None):
    review = review or {}
    summary = run.get('summary') or {}
    kept = (summary.get('selection') or {}).get('kept', [])
    top = kept[:5]
    facts, wrong_editions, matched = [], [], set()
    for event in kept:
        name = strict_name(event.get('name'))
        hits = [r for r in reference['reference_events']
                if name in {strict_name(n) for n in [r['name']]+r.get('aliases',[])}]
        if not hits:
            continue
        exact = [r for r in hits if r['starts_on']==event.get('starts_on')]
        if not exact:
            wrong_editions.append(event.get('name'))
            continue
        truth = exact[0]
        matched.add(truth['name'])
        for field in ('starts_on','ends_on','city','country'):
            facts.append(str(event.get(field) or '').casefold()==str(truth.get(field) or '').casefold())
    labels = review.get('event_labels') or {}
    judged = [labels.get(e.get('event_identity')) for e in top]
    precision = sum(x is True for x in judged)/len(top) if top and all(isinstance(x,bool) for x in judged) else None
    accuracy = sum(facts)/len(facts) if facts else None
    positives = {r['name'] for r in reference['reference_events'] if r.get('independent_relevance') is True}
    fully_labeled = all(isinstance(r.get('independent_relevance'),bool) for r in reference['reference_events'])
    recall = len(positives & matched)/len(positives) if reference.get('reference_complete') and fully_labeled and positives else None
    blockers=[]
    if recall is None:
        blockers.append('Overall discovery recall is unmeasured.')
    elif recall < reference['release_targets']['overall_reference_recall']:
        blockers.append('Overall discovery recall target failed.')
    if not review.get('critical_facts_review_complete'):
        blockers.append('Critical facts across all recommended events have not been reviewed.')
    if len(set(review.get('reviewers') or [])) < 2:
        blockers.append('Two independent reviewers have not completed adjudication.')
    if not reference.get('reference_complete'):
        blockers.append('Reference set is not complete; overall recall cannot be established.')
    if precision is None:
        blockers.append('Independent top-five relevance labels are incomplete.')
    if review.get('unsupported_personal_claims') is None:
        blockers.append('Personal-interaction claims have not been independently reviewed.')
    if wrong_editions:
        blockers.append('Known wrong editions were recommended.')
    targets=reference['release_targets']
    if precision is not None and precision < targets['top_five_expert_precision']:
        blockers.append('Top-five relevance target failed.')
    if accuracy is None or accuracy < targets['critical_fact_accuracy']:
        blockers.append('Critical facts are unmeasured or below target.')
    if review.get('unsupported_personal_claims',0):
        blockers.append('Unsupported personal claims were found.')
    for key in ('max_run_cost_usd','max_latency_seconds'):
        measurement = review.get({'max_run_cost_usd':'provider_reconciled_cost_usd','max_latency_seconds':'measured_latency_seconds'}[key])
        if targets.get(key) is None:
            blockers.append('Release target not set: '+key)
        elif measurement is None or measurement > targets[key]:
            blockers.append('Measurement is missing or exceeds release target: '+key)
    if run.get('status')!='complete' or summary.get('completion_state')=='partial':
        blockers.append('The report is not a completed, fully measured analysis.')
    return {'benchmark_version':reference['benchmark_version'], 'client':reference['client'],
        'top_five_expert_precision':precision,'critical_fact_accuracy_on_matched_reference':accuracy,
        'known_wrong_editions':wrong_editions,'matched_reference_events':sorted(matched),
        'overall_recall':recall,'release_accepted':False if blockers else True,'blockers':blockers,
        'note':'Reference matches are not a substitute for independent relevance or coverage review.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--reference',type=Path,default=Path('benchmarks/event_intelligence/position2.json'))
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    result=evaluate(json.loads(args.report.read_text()),json.loads(args.reference.read_text()),
                    json.loads(args.review.read_text()) if args.review else None)
    print(json.dumps(result,indent=2))
    return 0 if result['release_accepted'] else 2


if __name__=='__main__':
    raise SystemExit(main())
