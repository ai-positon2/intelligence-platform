"""Market Radar's cost ledger: every paid call, before and after.

    with ledger.track(run_id, "profile", "anthropic", model="claude-sonnet-5-5",
                      prompt_tokens=n, max_output_tokens=4000) as call:
        reply = client.messages.create(...)
        call.record(usage=reply.usage.model_dump())

`track` reserves the call's worst-case cost first, and refuses it (raising
BudgetExceeded, with nothing spent) if that could take the run past its hard
cap. The cap is checked under a per-run lock, so stages running in parallel
cannot both slip under it. After the call, the measured cost replaces the
reservation.

A call that ends without a measured cost (the worker died, the reply had no
usage, the code forgot to record) stays counted at its reservation and makes
the run's total partial. It never counts as zero: a ledger that quietly
drops calls it could not measure under-reports exactly the runs that went
wrong. A call known not to be billed (the provider refused it) is recorded
with billed=False and counts as zero.

Rows: mr_provider_calls (tracker/market_radar_store.py). Prices:
tracker/market_radar_costs.py.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from decimal import Decimal

from . import market_radar_costs as costs
from .market_radar_store import _tx

MODEL_PROVIDER = "anthropic"
# Statuses whose cost is the measured one; every other status counts at its
# reservation.
MEASURED = ("done", "failed")


class BudgetExceeded(RuntimeError):
    """This call could take the run past its cost cap, so it was not made."""

    def __init__(self, run_id, cap, committed, requested):
        self.run_id, self.cap, self.committed, self.requested = run_id, cap, committed, requested
        super().__init__(
            "Run %s: this call could cost up to $%s, and $%s of the $%s cap is already "
            "committed, so it was skipped." % (run_id, costs.usd(requested), costs.usd(committed),
                                                costs.usd(cap)))


def _reservation(provider, model, prompt_tokens, max_output_tokens, searches, units, batch,
                 reserved_usd):
    if reserved_usd is not None:
        value = Decimal(str(reserved_usd))
        if value < 0:
            raise ValueError("reserved_usd cannot be negative")
        return value
    if provider == MODEL_PROVIDER:
        if not model:
            raise ValueError("a model call needs its model")
        return costs.worst_case_model_usd(model, prompt_tokens, max_output_tokens, searches, batch)
    value = costs.provider_cost(provider, units)
    return Decimal(0) if value is None else value   # credit providers: no dollars


def _committed(cur, run_id):
    cur.execute("""SELECT COALESCE(SUM(CASE WHEN status IN %s THEN COALESCE(actual_usd, 0)
                                            ELSE COALESCE(reserved_usd, 0) END), 0)
                   FROM mr_provider_calls WHERE run_id=%s""", (MEASURED, run_id))
    return Decimal(cur.fetchone()[0])


def reserve(run_id, stage, provider, *, model=None, prompt_tokens=0, max_output_tokens=0,
            searches=0, units=0, batch=False, reserved_usd=None, conn=None):
    """Book a call's worst-case cost against the run's cap. Returns the call id.
    Raises BudgetExceeded, with no row written, when the cap would be passed."""
    amount = _reservation(provider, model, prompt_tokens, max_output_tokens, searches, units,
                          batch, reserved_usd)
    with _tx(conn) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("mr-budget:%s" % run_id,))
        cur.execute("SELECT cost_cap_usd, status FROM mr_runs WHERE id=%s", (run_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError("no run with id %s" % run_id)
        cap, status = Decimal(row[0]), row[1]
        if status in ("complete", "failed", "cancelled"):
            raise RuntimeError("run %s is %s; no further calls can be booked to it" % (run_id, status))
        committed = _committed(cur, run_id)
        if committed + amount > cap:
            raise BudgetExceeded(run_id, cap, committed, amount)
        cur.execute("""INSERT INTO mr_provider_calls (run_id, stage, provider, model, batch, units,
                                                      reserved_usd)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (run_id, stage, provider, model, batch, units, str(amount)))
        return cur.fetchone()[0]


def record(call_id, *, usage=None, units=None, actual_usd=None, error=None, billed=None,
           elapsed_ms=None, conn=None):
    """Settle a reserved call with what actually happened.

    usage       the model's usage block as a dict (model calls)
    units       how many units were used (searches, place lookups, credits)
    actual_usd  a charge the provider reported itself (Apify returns one)
    error       what went wrong, if anything
    billed      False when the provider is known not to have charged

    Returns the status written: done, failed (known unbilled) or unmeasured
    (counted at its reservation)."""
    with _tx(conn) as cur:
        cur.execute("""SELECT provider, model, batch, status FROM mr_provider_calls
                       WHERE id=%s FOR UPDATE""", (call_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError("no provider call with id %s" % call_id)
        provider, model, batch, status = row
        if status not in ("reserved", "abandoned"):
            raise ValueError("call %s was already settled (%s)" % (call_id, status))

        notes, cost, new_status = [], None, "unmeasured"
        if actual_usd is not None:
            cost, new_status = Decimal(str(actual_usd)), "done"
            notes.append("charge reported by the provider")
        elif billed is False:
            cost, new_status = Decimal(0), "failed"
        elif provider == MODEL_PROVIDER and usage is not None:
            try:
                cost, notes = costs.model_cost(model, usage, batch)
                new_status = "done"
            except (ValueError, costs.UnknownPrice) as e:
                notes.append("usage could not be priced: %s" % e)
        elif provider != MODEL_PROVIDER and units is not None and not error:
            cost = costs.provider_cost(provider, units)   # None for credit providers
            new_status = "done"
            if cost is None:
                notes.append("billed in credits, not dollars")
        if new_status == "unmeasured" and not notes:
            notes.append("no measured cost; counted at its reservation")

        cur.execute("""UPDATE mr_provider_calls SET status=%s, actual_usd=%s, usage=%s::jsonb,
                           units=COALESCE(%s, units), error=%s, notes=%s::jsonb, elapsed_ms=%s,
                           finished_at=now()
                       WHERE id=%s""",
                    (new_status, None if cost is None else str(cost),
                     None if usage is None else json.dumps(usage), units,
                     None if error is None else str(error)[:500], json.dumps(notes),
                     elapsed_ms, call_id))
        return new_status


class _Call:
    def __init__(self, call_id, conn):
        self.id, self._conn, self.status, self._started = call_id, conn, None, time.monotonic()

    def record(self, **kwargs):
        kwargs.setdefault("elapsed_ms", int((time.monotonic() - self._started) * 1000))
        self.status = record(self.id, conn=self._conn, **kwargs)
        return self.status


@contextmanager
def track(run_id, stage, provider, *, conn=None, **reserve_kwargs):
    """Reserve, run the block, and make sure the call is settled. A block
    that raises, or finishes without calling .record(), leaves the call
    unmeasured (counted at its reservation) with the reason written down."""
    call = _Call(reserve(run_id, stage, provider, conn=conn, **reserve_kwargs), conn)
    try:
        yield call
    except BaseException as e:
        if call.status is None:
            record(call.id, error="%s: %s" % (type(e).__name__, e), conn=conn,
                   elapsed_ms=int((time.monotonic() - call._started) * 1000))
        raise
    if call.status is None:
        record(call.id, error="the call finished without recording its usage", conn=conn,
               elapsed_ms=int((time.monotonic() - call._started) * 1000))


def abandon_open(run_id, *, conn=None):
    """Mark calls still 'reserved' as abandoned (their worker is gone). They
    keep counting at their reservation. Returns how many were marked."""
    with _tx(conn) as cur:
        cur.execute("""UPDATE mr_provider_calls SET status='abandoned', finished_at=now()
                       WHERE run_id=%s AND status='reserved'""", (run_id,))
        return cur.rowcount


def summary(run_id, *, conn=None):
    """What a run has cost so far, and how sure that figure is."""
    with _tx(conn) as cur:
        cur.execute("SELECT cost_cap_usd FROM mr_runs WHERE id=%s", (run_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError("no run with id %s" % run_id)
        cap = Decimal(row[0])
        cur.execute("""SELECT stage, provider, status, reserved_usd, actual_usd, units
                       FROM mr_provider_calls WHERE run_id=%s ORDER BY id""", (run_id,))
        rows = cur.fetchall()

    measured = reserved_counted = Decimal(0)
    by_stage, by_provider, credits, statuses = {}, {}, {}, {}
    for stage, provider, status, reserved, actual, units in rows:
        statuses[status] = statuses.get(status, 0) + 1
        if status in MEASURED:
            counted = Decimal(actual or 0)
            measured += counted
            if provider in costs.CREDIT_PROVIDERS and status == "done":
                credits[provider] = credits.get(provider, 0) + (units or 0)
        else:
            counted = Decimal(reserved or 0)
            reserved_counted += counted
        by_stage[stage] = by_stage.get(stage, Decimal(0)) + counted
        by_provider[provider] = by_provider.get(provider, Decimal(0)) + counted

    total = measured + reserved_counted
    partial = any(s not in MEASURED for s in statuses)
    return {
        "run_id": run_id,
        "total_usd": costs.usd(total),
        "measured_usd": costs.usd(measured),
        "counted_at_reservation_usd": costs.usd(reserved_counted),
        "partial": partial,
        "cap_usd": costs.usd(cap),
        "remaining_usd": costs.usd(cap - total),
        "calls": len(rows),
        "calls_by_status": statuses,
        "by_stage": {k: costs.usd(v) for k, v in sorted(by_stage.items())},
        "by_provider": {k: costs.usd(v) for k, v in sorted(by_provider.items())},
        "credits": credits,
        "pricing_checked": costs.PRICING_CHECKED,
        "pricing_source": costs.PRICING_SOURCE,
        "basis": ("List prices applied to the usage each provider reported; not reconciled "
                  "to an invoice. Free allowances, discounts and tax are not netted."
                  + (" Partial: calls without a measured cost are counted at their reservation"
                     " (the most they could have cost)." if partial else "")),
    }
