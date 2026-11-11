# Subscriptions and AI usage

For the people operating and extending Ryze Infinity plans. Status: built and tested on a temporary database, **not commercially ready**: no real payment gateway exists.

## Principles

- **A plan never grants data access.** Plans attach to the username. Domain grants and roles are not read or changed by any of this code.
- **Three separate records.** Provider usage (`llm_usage_events`) is what each model call reported. Plan credits (`usage_periods`, `usage_transactions`) are this application's own allowance. Provider quota is known only from what a provider says, such as a 429. Credits are never presented as quota.
- **Nothing unbuilt is sold.** `FEATURES` in `services/subscriptions.py` marks what exists. Anything else shows as "Planned, not available yet", whatever a plan's settings say.
- **No plan is unlimited.** Allowances, rate limits and provider limits always apply.

## How a question is charged

1. `open_gate` reads the plan once when the request starts.
2. Before any model call, `llm.py` asks the gate. A refused stage makes no provider call.
3. Every provider attempt is recorded, including failures. Missing token counts stay `NULL`.
4. A finished request is charged for one feature: forecasting, then SQL generation, then standard chat.

| Outcome | Credits |
|---|---|
| Answered by rules, with no model or forecast | 0, and still works at 100% |
| Refusal, clarification, awaiting review, error | 0, even if a model ran |
| Complete answer that used a model or made a forecast | The feature's configured cost |

A request the plan stopped returns HTTP 402. Other status codes keep their meanings, and 402 replaces a 422 or 503 only when the plan caused it. Administrators are recorded but not limited.

Deductions use a unique key and a database-side increment, so a retry cannot charge twice and concurrent requests cannot lose an update. If accounting itself fails, the question still runs and a warning is logged.

## Configuration

| Setting | Default | Effect |
|---|---|---|
| `SUBSCRIPTIONS_ENABLED` | `true` | `false` removes plans, limits, records and the UI |
| `SUBSCRIPTION_ENFORCEMENT` | `true` | `false` records usage but limits nobody |
| `SUBSCRIPTION_NOTICE_THRESHOLDS` | `30,50,80,90,100` | Notice levels |
| `SUBSCRIPTION_CURRENCY` | `INR` | Currency of the starting plans |
| `PAYMENT_GATEWAY` | empty | Empty means no online payment. `mock` works only with `DEMO_MODE=true` and moves no money |
| `LLM_COST_PER_MILLION_TOKENS` | empty | `provider:model=input,output;...`. Without a rate, cost shows "No rate set" |

Plans are edited under **Admin console > Plans & AI usage**. Starting allowances (100, 1,000 and 5,000 credits) are placeholders, shown as provisional until an administrator saves the plan. Paid prices start empty, and checkout is refused until they are set.

## API

| Endpoint | Who | Purpose |
|---|---|---|
| `GET /v1/subscription/plans`, `GET /v1/subscription/me` | Signed-in user | Plans; own plan, usage and tokens |
| `POST /v1/subscription/notices/{id}/seen` | Owner | Dismiss a notice |
| `POST /v1/subscription/cancel`, `/resume`, `/downgrade` | Signed-in user | Takes effect when the period ends |
| `POST /v1/billing/checkout`, `/verify`, `GET /v1/billing/history` | Signed-in user | Order, server-side verification, own records |
| `POST /v1/billing/mock/pay` | Signed-in user, mock gateway only | Stands in for a hosted checkout page |
| `GET /v1/admin/plans`, `PUT /v1/admin/plans/{code}` | Administrator | Edit or create plans |
| `POST /v1/admin/users/{username}/subscription`, `/credits` | Administrator | Assign a plan, adjust credits; a reason is required |
| `GET /v1/admin/subscriptions`, `GET /v1/admin/usage` | Administrator | Users, history, payments; usage by model, domain, user, day and month |

`QueryResponse` gains an optional `usage` field. Migration: `0003`.

## Payment gateway

A plan starts only after `/v1/billing/verify` passes: valid signature, the caller's own order, matching amount and currency, one-time use. To add a real gateway, implement `PaymentGateway` in `services/payments.py` and add a webhook for renewals and failed payments, which do not exist yet.

**Recommendation, to confirm with finance:** Razorpay first if customers mainly pay in INR, because comparisons favour it for UPI Autopay. Stripe if most billing is international. These comparisons are mostly vendor blogs and disagree on Stripe's UPI support, so check both providers' current terms and pricing.

## Known limitations

- **No real payments, renewals, webhooks, refunds or tax invoices.** Paid plans expire to Free at period end unless paid again. Receipts are numbers only.
- **No proration.** An upgrade charges the full price and unused credits are not carried over; the confirmation says so. A scheduled move to a lower paid plan ends on Free until it is paid for.
- **Slight overshoot under load.** Requests in flight read the same balance, so usage can pass 100% by the cost of those requests. A model call that ran is always recorded.
- **Provider percentage is not shown.** No Gemini endpoint for remaining quota was found, and no configured provider limit is built.
- **MCP is not metered.** `mcp_server.py` calls the banking workflow directly, so its model calls are neither recorded nor charged.
- **Routing is restriction only.** A plan can limit providers, but with two models there is no cost-based routing.
- **Banking forecasts** are checked against the plan after the history query has run, so a refused forecast still costs a warehouse read but no credits.
- `/v1/admin/usage` reads at most the latest 50,000 attempts in its window. No retention period is set for usage records.
