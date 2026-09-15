# Eliminate the Confirm Tax on Every Resume

A run created purely via MCP tools is self certified, so `continuum_resume` returns `request_human` until a `REVIEW_CONFIRMED` event exists. Every resume therefore costs an extra `continuum_confirm` call plus a model turn.

## Options

- **Same client auto confirm.** If the resuming client name matches the creating client name and the run is within a short window (for example 24 hours), treat the resumption as operator approved and return `RESUME` directly. The ledger still records a synthetic `REVIEW_CONFIRMED` with `actor=client_name` and `reason=auto_confirm_same_client`, so the audit trail shows the decision.

- **Scoped confirm.** Add a `confirm --scope self` that only clears `REQUIRES_REVIEW` that is due to `Origin.EXTERNAL_AGENT` self certification, not due to other validators like environment drift. This keeps the human gate for real staleness while removing the tax for the self certification case.

- **New mode.** Return `RESUME_WITH_REVIEW` that is safe to continue but carries a warning that the initial progress was self certified. The agent may proceed but the UI surfaces the review status. This avoids overloading `request_human`.

## Recommendation

Start with scoped confirm. It is the minimal safe change: one new flag, no new mode, and it preserves the human gate for every other signal. Same client auto confirm can be layered on top once scoped confirm is proven.

No implementation is done here. This is a design note for issue 84, with no external claims.

Reproduce the tax by creating a run via `continuum_record_progress` over MCP, then calling `continuum_resume` and observing `request_human` until `continuum_confirm` is called.

## Risks

Auto confirming the wrong client would let a compromised agent launder its own progress. The same client check must compare the full `client_info.name` that the server saw at handshake, not a value supplied in tool arguments.
