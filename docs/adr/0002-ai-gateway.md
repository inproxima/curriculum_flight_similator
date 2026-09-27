# ADR 0002: Model gateway, grounding, and routes

Status: accepted (2026-09-26). Scope: Phase 5.

## Decisions
1. **One gateway (`cfs.ai.gateway`) is the only path to providers.** Application code never imports a provider
   SDK. Adapters (`providers/openai_adapter.py`: Responses API, `store=False`; `providers/anthropic_adapter.py`:
   Messages API) each run their own native tool loop behind one neutral request type.
2. **Routes, not hard-coded models** (`cfs.ai.routes`). The defaults follow the spec and were verified against
   both accounts' model lists on 2026-09-26:

   | Route | Primary | Fallback |
   |---|---|---|
   | classify | gpt-6-luna | claude-sonnet-5 |
   | extract | gpt-6-sol | claude-sonnet-5 |
   | synthesize | gpt-6-astra | claude-opus-5-5 |
   | pedagogy | claude-sonnet-5 | gpt-6-sol |
   | critique | claude-opus-5-5 | gpt-6-astra |
   | embed | text-embedding-3-small (1,536) | none |

   Every run records the model identity the provider returned.
3. **Content policy before fallback.** A provider is used only if it has a key, it is in
   `CFS_AI_PROVIDER_ALLOWLIST`, **and** every document whose excerpts are in the request permits it
   (`document_versions.ai_providers`). Retrieval applies the same filter in SQL before anything is sent.
   Fallback happens only on provider failure, only under the same check, and is recorded (`fallback_from`).
   Refusals are surfaced and never re-routed.
4. **Models propose; deterministic code decides.**
   - Extraction output is validated: quotes must be found on the page, codes must appear in their own quote,
     and credits, terms and descriptions are kept only when the quoted text states them.
   - Validated output becomes review items only.
   - Assistant answers are a strict JSON envelope. The server keeps only citations to evidence returned by a
     tool in the same turn whose quote matches, drops unknown IDs, and validates proposed scenario operations
     against a copy of the projection.
   - Scenario analysis stays in the deterministic engine; the AI only explains it.
5. **Application-owned grounding.** Anthropic native citations are not combined with structured output, which
   is incompatible. Both providers cite the application's own evidence spans and chunks, so grounding is the
   same whichever provider answers.
6. **Operations.**
   - Each route has timeouts and bounded SDK retries, plus per-provider concurrency limits.
   - Spend is limited per job and per workspace per calendar month, with cost computed from published prices
     (retrieved 2026-09-26, overridable).
   - Prompt and schema versions are recorded on each run.
   - Results are cached by (document hash or snapshot hash, prompt version, schema version, pipeline version).
   - `model_runs` and `tool_calls` form the audit trail. Hidden reasoning is never stored.
7. **Assistant turns are durable jobs.** Progress is streamed over SSE from persisted job events, so reconnecting
   is safe, and the answer is stored as a message.
