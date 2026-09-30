"""Event-ledger cost estimates, separate from platform-wide legacy spend math."""
from decimal import Decimal

PRICING_SOURCE='https://platform.claude.com/docs/en/about-claude/pricing'
PRICING_CHECKED='2026-09-10'
RATES={'claude-sonnet-5':(2,10), 'claude-sonnet-4-6':(3,15), 'claude-sonnet-4-5':(3,15)}
USD_PER_SEARCH=Decimal(1)/100


def _rate(model):
    return next((v for k,v in RATES.items() if model==k or model.startswith(k+'-')),None)


def _reserved_usd(call, rate):
    """What a call without reported usage is counted at: its reservation.

    reserve_call() books a byte-count allowance for the prompt, the output
    budget and generous room per search before any request is sent, so it is
    the only figure that exists for a call whose usage never came back (a
    worker that died mid-call, a reply with no usage block). The whole
    allowance is priced at the model's input rate, the rate most of a run's
    tokens are billed at, plus every reserved search. It is an allowance, not
    a measurement: the figure it produces is marked partial wherever it is
    used. None when the row carries no reservation to price."""
    tokens, searches = call.get('reserved_tokens'), call.get('reserved_searches')
    if type(tokens) is not int or tokens < 0 or type(searches) is not int or searches < 0:
        return None
    base = Decimal((rate or max(RATES.values()))[0])
    return Decimal(tokens)*base/1000000 + Decimal(searches)*USD_PER_SEARCH


def estimate(calls):
    total=Decimal(0)
    reserved_total=Decimal(0)
    unresolved=[]
    measured=0
    from_reservation=0
    unpriceable=0
    for index, call in enumerate(calls or []):
        model=str(call.get('model') or '')
        rate=_rate(model)
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
            if result.get('abandoned'):
                reason='The worker running this call stopped before its outcome was recorded.'
            fallback=_reserved_usd(call, rate)
            if fallback is None:
                unpriceable+=1
            else:
                reserved_total+=fallback
                from_reservation+=1
            unresolved.append({'call_index':index,'model':model,'reason':reason,
                               'counted_from_reservation':fallback is not None})
            continue
        inp,out,read,_=values
        base,output=map(Decimal,rate)
        total+=(Decimal(inp)*base+Decimal(out)*output+Decimal(read)*base/10)/1000000+Decimal(searches)*USD_PER_SEARCH
        measured+=1
    complete=bool(calls) and not unresolved
    # A figure exists whenever every call was either measured or reserved.
    # Only a call with neither (a legacy row with no reservation) leaves the
    # run without one, because any number then would silently omit it.
    has_figure=bool(calls) and not unpriceable
    q=lambda d: float(d.quantize(Decimal('.0001')))
    return {'estimated_usd':q(total+reserved_total) if has_figure else None,
            'partial':bool(unresolved),
            'known_calls_estimated_usd':q(total),
            'reserved_calls_estimated_usd':q(reserved_total),
            'calls_priced':measured,'calls_estimated_from_reservation':from_reservation,
            'calls_total':len(calls or []),'unresolved':unresolved,
            'complete_usage':complete,'pricing_checked':PRICING_CHECKED,'pricing_source':PRICING_SOURCE,
            'basis':('Standard direct-API list rates and reported usage; not reconciled to an invoice. Discounts, tax and negotiated rates are excluded.'
                     + (' Partial: %d call(s) without reported usage are counted at their reserved token and search allowance.' % from_reservation if from_reservation else ''))}
