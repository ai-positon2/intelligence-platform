"""Event-ledger cost estimates, separate from platform-wide legacy spend math."""
from decimal import Decimal

PRICING_SOURCE='https://platform.claude.com/docs/en/about-claude/pricing'
PRICING_CHECKED='2026-09-10'
RATES={'claude-sonnet-5':(2,10), 'claude-sonnet-4-6':(3,15), 'claude-sonnet-4-5':(3,15)}


def estimate(calls):
    total=Decimal(0)
    unresolved=[]
    measured=0
    for index, call in enumerate(calls or []):
        model=str(call.get('model') or '')
        rate=next((v for k,v in RATES.items() if model==k or model.startswith(k+'-')),None)
        result=call.get('result') or {}
        usage=result.get('usage') or {}
        keys=('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')
        values=[usage.get(k) for k in keys]
        searches=usage.get('web_search_requests',result.get('search_count'))
        reason=None
        if rate is None:
            reason='Model pricing is not configured.'
        elif any(type(n) is not int or n<0 for n in values+[searches]):
            reason='Complete nonnegative usage was not reported.'
        elif values[3]:
            reason='Cache-write duration was not recorded; its price cannot be determined.'
        if reason:
            unresolved.append({'call_index':index,'model':model,'reason':reason})
            continue
        inp,out,read,_=values
        base,output=map(Decimal,rate)
        total+=(Decimal(inp)*base+Decimal(out)*output+Decimal(read)*base/10)/1000000+Decimal(searches)/100
        measured+=1
    complete=bool(calls) and not unresolved
    return {'estimated_usd':float(total.quantize(Decimal('.0001'))) if complete else None,
            'known_calls_estimated_usd':float(total.quantize(Decimal('.0001'))),
            'calls_priced':measured,'calls_total':len(calls or []),'unresolved':unresolved,
            'complete_usage':complete,'pricing_checked':PRICING_CHECKED,'pricing_source':PRICING_SOURCE,
            'basis':'Standard direct-API list rates and reported usage; not reconciled to an invoice. Discounts, tax and negotiated rates are excluded.'}
