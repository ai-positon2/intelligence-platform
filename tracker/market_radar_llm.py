"""Market Radar: one structured-output model call, booked in the cost ledger.

    parsed, meta = call_json(SYSTEM, user, SCHEMA, model="claude-haiku-5-5",
                             max_tokens=4000, run_id=run, stage="verify")

Every step that asks a model for JSON (the profile, the search plan, the
candidate checks, the ranking) goes through here, so they share one set of
rules:

  * no tools: the model reads only what it is given;
  * structured output against a JSON schema, effort "low";
  * server-side fallbacks, so a refusal can be served by another model inside
    the same call, and the call is priced at the model that actually served it;
  * a request the API rejects outright (bad request, bad key) costs nothing;
    a dropped connection counts at its reservation; a refusal or a truncated
    reply is billed and raised as an error, never returned as a result.
"""
from __future__ import annotations

import json
import os
import time

from . import market_radar_costs as costs

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelError(RuntimeError):
    """kind: not_configured, refused, truncated, bad_json, api_error, budget."""

    def __init__(self, kind, detail):
        self.kind, self.detail = kind, detail
        super().__init__("%s: %s" % (kind, detail))


def default_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ModelError("not_configured", "ANTHROPIC_API_KEY is not set")
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=240, max_retries=2)


def _request(client, model, max_tokens, system, user, schema, use_fallbacks, effort="low"):
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": effort,
                                 "format": {"type": "json_schema", "schema": schema}})
    if use_fallbacks:
        # If the model declines, the API re-runs the request on a fallback
        # model inside the same call, routed by the refusal's category.
        return client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
    return client.messages.create(**kwargs)


def call_json(system, user, schema, *, model, max_tokens, run_id=None, stage, client=None,
              effort="low"):
    """Returns (parsed, meta). Raises ModelError. `effort` is "low" for
    every reading step; the report writer asks for more."""
    client = client or default_client()
    meta = {"model_requested": model, "prompt_chars": len(system) + len(user)}

    def call():
        started = time.monotonic()
        try:
            resp = _request(client, model, max_tokens, system, user, schema, True, effort)
        except Exception as e:
            # A fallback configuration this API version rejects must not cost
            # the step: ask again without it, and say so.
            if "fallback" in str(e).lower() and getattr(e, "status_code", None) == 400:
                meta["fallbacks"] = "rejected by the API; asked without them"
                resp = _request(client, model, max_tokens, system, user, schema, False, effort)
            else:
                raise
        meta["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return resp

    if run_id is None:
        return _parse(call(), meta, model, max_tokens)

    import anthropic
    from . import market_radar_ledger as ledger
    unbilled = (anthropic.BadRequestError, anthropic.AuthenticationError,
                anthropic.PermissionDeniedError, anthropic.NotFoundError)
    try:
        with ledger.track(run_id, stage, "anthropic", model=model,
                          prompt_tokens=costs.estimate_tokens(system + user) + 400,
                          max_output_tokens=max_tokens) as call_ctx:
            try:
                resp = call()
            except unbilled as e:
                # The API refused the request itself: nothing was generated or billed.
                call_ctx.record(error="%s: %s" % (type(e).__name__, str(e)[:300]), billed=False)
                raise
            usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
            served = getattr(resp, "model", None) or model
            meta.update({"model_served": served, "usage": usage, "stop_reason": resp.stop_reason})
            try:
                usd, _ = costs.model_cost(served, usage)
            except (ValueError, costs.UnknownPrice) as e:
                # Counted at its reservation, with the reason written down.
                call_ctx.record(usage=usage, error="could not price %s: %s" % (served, e))
            else:
                call_ctx.record(actual_usd=usd, usage=usage)
                meta["cost_usd"] = costs.usd(usd)
    except ledger.BudgetExceeded as e:
        raise ModelError("budget", str(e))
    except ModelError:
        raise
    except Exception as e:
        raise ModelError("api_error", "%s: %s" % (type(e).__name__, str(e)[:300]))
    return _parse(resp, meta, model, max_tokens)


def _parse(resp, meta, model, max_tokens):
    meta.setdefault("stop_reason", resp.stop_reason)
    meta.setdefault("model_served", getattr(resp, "model", model))
    if resp.stop_reason == "refusal":
        raise ModelError("refused", "the model declined (%s)" % getattr(resp, "stop_details", None))
    if resp.stop_reason == "max_tokens":
        raise ModelError("truncated", "the reply hit the %d-token limit" % max_tokens)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text), meta
    except ValueError as e:
        raise ModelError("bad_json", "%s; reply began %r" % (e, text[:120]))
