# OpenAI SDK: model choice and structured trade-order output

Resolves GitHub issue #4. Researched 2026-09-14 against OpenAI's own docs and the
`openai-python` / `openai-agents-python` repos only. Prices and model IDs were
read from the live pages on that date; re-check before relying on them, OpenAI
moves these often. Note: `platform.openai.com/docs/*` now 301-redirects to
`developers.openai.com/api/docs/*`.

## TL;DR

- Use the **Responses API** via `pip install openai` with `client.responses.parse(..., text_format=YourPydanticModel)`; read `response.output_parsed`. Skip the Agents SDK for a single-shot decision.
- Start with **`gpt-5.6-terra`** (~$0.08/cycle, ~$8/month at 3 cycles/day). Drop to **`gpt-5.6-luna`** (~$0.01/cycle, ~$0.80/month) if quality holds; step up to `gpt-5.6-sol` only if terra's decisions are measurably worse. `gpt-6-astra` is ~5x terra and cannot turn reasoning off.
- Do not build on `gpt-5-mini`/`gpt-5-nano`/`o3`/`o4-mini`: all have announced shutdown dates in Oct-Dec 2026.

## 1. Current models and what fits a 20-40k-token decision cycle

The models overview lists four current flagship text models, all with a 1,050,000-token context window, 128,000 max output tokens, and support for the Responses API, structured outputs and function calling
([models overview](https://developers.openai.com/api/docs/models)).

| Model ID | Positioning (OpenAI's words) | Knowledge cutoff | `reasoning.effort` values |
|---|---|---|---|
| `gpt-6-astra` | "most capable model", recommended default for new projects | 2026-04-30 | low, medium, high, xhigh, max ("does not support `none`") |
| `gpt-5.6-sol` | "complex professional work" | 2026-02-16 | none, low, medium (default), high, xhigh, max |
| `gpt-5.6-terra` | balances "intelligence and cost" | 2026-02-16 | none, low, medium (default), high, xhigh, max |
| `gpt-5.6-luna` | "cost-sensitive workloads" | 2026-02-16 | none, low, medium (default), high, xhigh, max |

Sources: [gpt-6-astra](https://developers.openai.com/api/docs/models/gpt-6-astra),
[gpt-5.6-sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
[gpt-5.6-terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
[gpt-5.6-luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
[reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

Fit: a 20-40k-token prompt is ~4% of any of these context windows, so context is
not a differentiator. The decision is cost vs. decision quality. Reasoning
tokens "are billed as output tokens" ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)),
so `reasoning.effort` is the main cost knob after model choice; `low` is the
sensible starting point for a trading cycle, `none` if latency/cost matter more
than deliberation.

Older models still priced but on the way out ([deprecations](https://developers.openai.com/api/docs/deprecations)):

| Model | Shutdown | OpenAI's replacement |
|---|---|---|
| `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `o3` | 2026-12-11 | sol / terra / luna / sol |
| `o4-mini`, `gpt-4.1-nano` | 2026-10-23 | terra / luna |

`gpt-5.5`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-4.1`, `gpt-4o` are still on the
pricing page with no deprecation entry as of today, but they are not on the
current-models overview and the 5.6 family is cheaper or equal at each tier, so
there is no reason to pick them for a new build.

## 2. Cost per cycle at 3 cycles/day

Standard-tier prices per 1M tokens, read from the [pricing page](https://developers.openai.com/api/docs/pricing) on 2026-09-14:

| Model | Input | Cached input | Output |
|---|---|---|---|
| `gpt-6-astra` | $10.00 | $1.00 | $50.00 |
| `gpt-5.6-sol` | $4.00 | $0.40 | $20.00 |
| `gpt-5.6-terra` | $2.00 | $0.20 | $12.00 |
| `gpt-5.6-luna` | $0.20 | $0.02 | $1.20 |
| `gpt-5.4-mini` (legacy) | $0.75 | $0.075 | $4.50 |
| `gpt-5.4-nano` (legacy) | $0.20 | $0.02 | $1.25 |

Assumptions: 30k input tokens (midpoint of 20-40k), 2k output tokens (a few
hundred tokens of JSON orders plus reasoning at `low` effort), no cache hits,
3 cycles/day, 30-day month = 90 cycles. Arithmetic: `in/1M * price_in + out/1M * price_out`.

| Model | $/cycle | $/day | $/month (90 cycles) | Worst case 40k in / 4k out, $/cycle |
|---|---|---|---|---|
| `gpt-6-astra` | 0.400 | 1.20 | 36.00 | 0.600 |
| `gpt-5.6-sol` | 0.160 | 0.48 | 14.40 | 0.240 |
| `gpt-5.6-terra` | 0.084 | 0.25 | 7.56 | 0.128 |
| `gpt-5.6-luna` | 0.0084 | 0.025 | 0.76 | 0.0128 |
| `gpt-5.4-mini` | 0.0315 | 0.09 | 2.84 | 0.048 |
| `gpt-5.4-nano` | 0.0085 | 0.026 | 0.77 | 0.0129 |

Two cost notes from the [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching):

- Cache reads on GPT-5.6+ are 0.1x the input rate, but the only TTL is 30 minutes
  (`prompt_cache_options.ttl`, "the only supported value, `30m`, is also the
  default"). Cycles hours apart will not hit the cache. If you ever run cycles
  closer together, put the static part (instructions, journal rules, schema) at
  the front of the prompt; the minimum cacheable prefix is 1,024 tokens.
- Batch API is 50% off standard, but it is asynchronous with no latency
  guarantee, which does not suit a live decision cycle.

Even at the top end (`gpt-6-astra`, worst case) this is under $2/day, so the
model decision should be driven by decision quality on your own backtests, not
by cost. Start on terra, compare against luna on the same journal.

## 3. Strictly-typed JSON orders (structured outputs with pydantic)

Structured outputs is a Responses API feature: `text.format = {"type": "json_schema", "strict": true, "name": ..., "schema": ...}`.
The Python SDK wraps it as `client.responses.parse(..., text_format=PydanticModel)`
and returns `ParsedResponse[T]` with the validated instance on `response.output_parsed`
([structured outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs);
[openai-python `responses.py`](https://github.com/openai/openai-python/blob/main/src/openai/resources/responses/responses.py)
defines `def parse(self, *, text_format: type[TextFormatT] | Omit = omit, ...) -> ParsedResponse[TextFormatT]`).

Schema rules that bite in practice (same guide):

- "All fields are required by default." Optional means `field: T | None`, not a default value. "Pydantic models cannot use default values" in this context.
- `additionalProperties` must be `false` (the SDK sets this for you from the pydantic model).
- Supported types: string, number, boolean, array, object, null; `enum` and `Literal` are supported (use them for `side`, `order_type`, `time_in_force`).
- Limits are generous: 100,000 properties, 100 nesting levels, 10,000 enum values.
- The model can still refuse; a refusal arrives as a content item with `type == "refusal"` instead of parsed output, so check `output_parsed is None`.

Structured outputs "is available in our latest large language models, starting with GPT-4o. For new projects, start with `gpt-6-astra`." All four 5.6/6 models above list `structured_outputs` as a supported feature.

## 4. Responses API vs Agents SDK for a single-shot decision

| | `openai` (Responses API) | `openai-agents` (Agents SDK) |
|---|---|---|
| pip name | `openai` | `openai-agents` |
| Version seen | 3.13.0 | 0.22.2 |
| Python | >= 3.10 | >= 3.10 (tested through 3.14) |
| Direct deps | httpx2, pydantic, typing-extensions, anyio, sniffio, jiter (6) | `openai` + griffelib, requests, websockets, mcp, pyjwt, python-multipart, starlette, urllib3 (12 direct, plus everything `mcp` and `starlette` pull in) |
| Structured output | `responses.parse(text_format=Model)` | `Agent(output_type=Model)` (uses the same structured-outputs feature underneath) |
| What it adds | nothing beyond the API | agent loop, tool dispatch, handoffs, guardrails, sessions, tracing |

Sources: [openai-python pyproject.toml](https://github.com/openai/openai-python/blob/main/pyproject.toml),
[openai-agents-python pyproject.toml](https://github.com/openai/openai-agents-python/blob/main/pyproject.toml),
[Agents SDK README](https://github.com/openai/openai-agents-python/blob/main/README.md),
[Agents SDK `output_type` docs](https://openai.github.io/openai-agents-python/agents/).

Verdict: the plain `openai` package is the smaller dependency by a wide margin
and does everything a one-request-in, one-order-list-out cycle needs. The Agents
SDK earns its weight only when the model has to call tools in a loop (e.g. "fetch
more bars for AAPL, then decide"). Chat Completions is not deprecated, but OpenAI
says "Responses is recommended for all new projects" and reports 40-80% better
cache utilization ([migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)).

## 5. Minimal code sketch

```bash
uv add openai            # pulls pydantic v2 transitively
export OPENAI_API_KEY=...  # from your secret store, never committed
```

```python
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel


class Order(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: float | None  # optional = union with None, no default
    time_in_force: Literal["day", "gtc"]
    rationale: str


class Decision(BaseModel):
    orders: list[Order]  # empty list means "hold"
    journal_note: str


client = OpenAI()  # reads OPENAI_API_KEY


def decide(context_markdown: str) -> Decision | None:
    resp = client.responses.parse(
        model="gpt-5.6-terra",
        reasoning={"effort": "low"},
        instructions=(
            "You are a portfolio decision engine. Read the bars, headlines, "
            "positions and journal, then return orders. Return no orders to hold."
        ),
        input=context_markdown,
        text_format=Decision,
    )
    # None when the model refused; resp.output[0].content[0].refusal has the reason
    return resp.output_parsed


if __name__ == "__main__":
    # self-check: schema is strict-compatible without hitting the API
    schema = Decision.model_json_schema()
    assert set(schema["required"]) == {"orders", "journal_note"}
```

Cost/usage per call is on `resp.usage.input_tokens`, `resp.usage.output_tokens`,
and `resp.usage.input_tokens_details.cached_tokens`; log them per cycle so the
table in section 2 can be replaced with measured numbers.
