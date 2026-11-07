# Shop chatbot implementation plan

Prepared 4 November 2026. Planning deliverable only; the chatbot has not been changed.

Business source: [Shop bánh kẹo — requirements for 200 products](https://docs.google.com/document/d/1qKcVBl0cYq4pXux53WQSS5gRtAozjQHHJqjTr-pDj2A/edit?tab=t.0).
Code source: `rag_chatbot_sample/app.py`, `README.md`, `requirements.txt`, and `tests/test_app.py` in this workspace. This is the available sample corresponding to your “RAG_chatbot_co_mau” reference.

## 1. What to build first

Extend the existing Python terminal chatbot into a Vietnamese shop assistant that can:

- Find a specific product or suggest up to three suitable products.
- Respect budget, quantity, packaging, and ingredient restrictions across follow-up questions.
- Answer approved product questions and shop FAQs with traceable sources.
- Ask one or two short clarification questions when needed.
- Record unresolved requests in a staff queue, including cases where no public reply is posted.

Start with terminal input and a reusable `handle_message(...)` function. Add the actual messaging/API adapter after the shop selects its channel. No UI, order confirmation, payment, invented discounts, delivery promises, or automatic FAQ publication in this MVP.

**Main design:** Python checks the product facts and mandatory conditions. RAG finds relevant descriptions and policies. The LLM interprets language and proposes wording; it does not decide authoritative price, stock, or ingredient suitability.

## 2. Reuse and changes in the sample

| Existing code | Keep / adapt | Reason |
| --- | --- | --- |
| `create_ollama_client()`, `friendly_error()` | Reuse connection setup and sanitized errors | Already handles credentials, timeouts, and common Cloud errors. |
| `embed_texts()`, `distance_to_similarity()` | Reuse | Local multilingual embeddings and cosine search already exist. |
| `read_text_files()`, `split_into_chunks()` | Reuse for longer approved policy text | Preserve document/section identity and repeat necessary conditions in split sections. |
| `get_collection()` | Adapt to a separate `shop_knowledge` collection | Keep the student demo index separate; preserve model/metric compatibility checks. |
| `index_knowledge_base()` | Adapt to product and approved FAQ records | Current code skips all updates once an index exists. Detect changed sources and require a rebuild before using stale descriptions/policies. |
| `retrieve_relevant_chunks()` | Add record-type and eligible-product filters; preserve record IDs | Product descriptions and FAQs must not be mixed indiscriminately. |
| `add_to_memory()`, `format_history()` | Keep short display history; add structured criteria separately | Three turns of text are insufficient to preserve mandatory restrictions reliably. |
| `build_rag_prompt()` | Replace student instructions with shop rules and verified evidence | Include source IDs, current criteria, and allowed product IDs. |
| `stream_answer()` | Reuse SDK/error handling; buffer shop output for validation | Current streaming prints words before checking them. Shop claims must be checked before publication. |
| `run_chatbot()`, `main()` | Route terminal input through `handle_message()` | Retrieval currently occurs before intent detection; retrieval errors also need per-turn handling. |
| `tests/test_app.py` | Preserve relevant connection, indexing, and failure tests; adjust changed contracts | Do not lose existing protections while changing the conversation flow. |

`TOP_K = 3` currently means three text chunks, not three valid products. Keep retrieval candidate count separate from the three-product display limit. The current `MIN_SIMILARITY = 0.70` is a demo value, not proof that a policy answers the question.

## 3. Smallest practical file layout

Proposed additions during implementation, not files already created:

```text
rag_chatbot_sample/
  app.py                       # existing: orchestration, Ollama, embeddings, CLI
  shop.py                      # new: data validation, criteria, filtering, rendering
  support.py                   # new: SQLite queue, audit events, staff CLI commands
  data/
    products.json              # approved structured catalog, one record per SKU/variant
    faq.json                   # approved Q&A, applicability, version and source
  knowledge_base/
    shop_policies.txt          # only if longer approved policies are supplied
  shop_support.sqlite3         # generated runtime file; excluded from Git
  tests/
    test_app.py                # existing tests, adapted where necessary
    test_shop.py               # fixtures and business acceptance tests
    test_support.py            # queue persistence and failure tests
```

Use the existing dependencies. Python `json`, `sqlite3`, `datetime`, and `unittest` cover the additions. No agent framework, extra vector database, model training, or separate recommendation service is needed for this first version.

## 4. Data the shop must prepare

Begin with 10–20 real, checked products and approved FAQs; import all 200 after the flow passes tests. Synthetic fixtures must be clearly marked and excluded from the real catalog.

| Data | Required fields / rules |
| --- | --- |
| Product identity | Unique SKU/variant ID, name, approved aliases, category, brand, flavor, description, packaging/weight, and sales unit. Treat variants separately provisionally; confirm with the owner. |
| Price and availability | Integer VND price per sales unit, active/discontinued status, stock quantity or explicit availability, authoritative source, and separate price/stock update timestamps. Unknown is different from zero or out of stock. |
| Ingredient evidence | Label text/reference and verification date; per requested allergen use verified absent, present, may contain, or unknown. Missing text or an empty allergen list cannot establish absence. Preserve cross-contact warnings. |
| Other product facts | Verified product URL, storage guidance, and any expiry information with product/batch scope. Do not infer a current batch expiry from a generic description. |
| FAQ/policy | Stable ID, approved answer, question/aliases, applicability conditions, effective dates/version, approval status, owner, and source reference. Do not index drafts or expired policies as active answers. |

Validate duplicate IDs, money and quantity types/ranges, timestamps, allowed status values, and URL schemes at import. Missing optional facts remain unknown. Invalid imports must leave the previous valid snapshot usable and visibly report the failure. If freshness cannot be established, do not silently continue presenting an older snapshot as current.

For the terminal pilot, the approved JSON catalog can be the official snapshot. It is not live stock. The owner must define acceptable price/stock age; stale or conflicting required facts trigger verification. If live availability is required, connect the actual inventory source before launch.

Index stable product descriptions with `record_type=product` and `product_id`; index FAQs separately by record identity with `record_type=faq` and `faq_id`. Keep each short FAQ answer and its conditions together. Resolve current price, stock, and policy validity from the authoritative records at answer time, even when an older vector record is retrieved.

## 5. Message flow

```text
Message + conversation ID + channel + request ID
  → validate input and load this conversation's criteria
  → identify intent and extract explicit changes
  → clarify if a material condition is ambiguous
  → product lookup / product filtering and ranking / approved FAQ retrieval
  → verify evidence and build a controlled response
  → create support request if needed and check persistence succeeded
  → apply channel publication policy
  → store outcome and return response
```

### A. Interpret and preserve the customer's criteria

- Intents: specific product, recommendation, product fact, FAQ/policy, out of scope, or staff request. Split mixed questions into parts; do not lose an unanswered policy question just because a product can be suggested.
- Match explicit SKU and approved name aliases first. Use the LLM for semantic intent/criteria extraction, requesting a small JSON object as text, then parse and validate it in Python. Invalid or ambiguous extraction produces clarification rather than a guessed restriction.
- Keep structured state per conversation: mandatory conditions, soft preferences, requested product, budget scope, quantity, and last suggested IDs/prices. Apply only explicit changes; an omitted field never clears an existing constraint. `/reset` clears both criteria and history.
- Preserve restrictions beyond `MEMORY_TURNS`. “Có loại rẻ hơn không?” retains ingredient and packaging restrictions and uses the previous relevant price as a ceiling; clarify which item if the reference is ambiguous.
- Ask whether a budget is per item or for all requested units when it changes eligibility. Ask about contradictory constraints rather than silently choosing one. Ingredient restrictions require explicit confirmation before removal.

The current [Ollama structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs) states that Cloud does not support structured outputs. Do not depend on schema enforcement through `format`. Check the configured Cloud model with extraction fixtures before relying on it; JSON text is untrusted until validated, and schema validity alone does not prove the interpretation is correct.

### B. Find and recommend products

1. Resolve a named SKU/variant first. Distinguish nonexistent, discontinued, confirmed out of stock, and availability unknown.
2. Check all structured products against mandatory conditions before semantic ranking: active status, verified availability, required quantity, packaging, budget scope, and verified ingredient restrictions. Unknown required facts are excluded from confirmed matches and recorded as reasons for verification.
3. Use exact/alias matches and explicit category/flavor/brand/purpose matches, then semantic similarity for descriptions. An explicitly mandatory brand or flavor remains a hard condition. Query only eligible product IDs in Chroma using metadata filtering; do not take the global top three and then filter them. [Chroma supports metadata filters on queries](https://docs.trychroma.com/docs/querying-collections/metadata-filtering).
4. Deduplicate by SKU, choose up to three, and recheck authoritative facts immediately before rendering. No candidates means no forced recommendation. If only uncertainty prevents a match, say verification is needed rather than claiming no product exists.
5. Render name/SKU, price, sales unit/packaging, and approved URL directly from the catalog. Explain suitability using verified attributes. For substitutes, explicitly identify the requested item that is unavailable and the differences. Ask before relaxing any mandatory condition.

For a total budget and a requested quantity of one SKU, compare `price_vnd × quantity` with the budget. Do not build mixed baskets or invent gift combos; clarify or hand off requests that require that separate feature.

Avoid blanket allergy-safety claims. State only what the verified label supports; unknown or conflicting ingredient/cross-contact information goes to staff.

### C. Answer FAQs and product facts

- Check approved FAQ aliases and applicability first. Semantic search may retrieve candidates, but the conditions and answer must cover the actual question. Conflicts or unresolved applicability require clarification/verification.
- Use RAG for supported descriptions and longer approved policy passages. Attach stable source IDs/version to the result. No evidence means no invented policy and no unrelated product pitch.
- Prefer the exact approved answer for policy facts. Permit LLM phrasing only around approved facts; render commercial facts from records. For free text, validate selected source/product IDs and reject unsupported claims; use an approved template or handoff whenever support cannot be established.
- Keep a small set of Vietnamese examples in the prompt: suitable recommendation, ambiguous budget, substitution, missing policy, unknown ingredient information, and an unchanged restriction in a follow-up.
- Treat customer text and retrieved text as data, never as permission to override shop rules. Do not expose secrets, internal notes, or another conversation's history.

Keep the existing E5 `query:` / `passage:` prefixes. Its [model card](https://huggingface.co/intfloat/multilingual-e5-small) explains the prefixes and notes that scores often cluster at high values. Calibrate retrieval on shop questions; use source coverage, validity, and constraints alongside similarity.

### D. Return an explicit result and handle unresolved cases

Suggested internal result contract, not an implemented API:

```python
{
    "request_id": "...",
    "status": "answered",  # or clarify / unresolved / out_of_scope / error
    "publish": True,
    "reply": "...",         # null when a public reply is intentionally held
    "product_ids": [],
    "source_ids": [],
    "ticket_id": None,
    "reason": None,
}
```

Publication is independent of the outcome: an unresolved public request can have `publish=False` and a successfully stored ticket. A normal clarification does not automatically require a ticket.

| Situation | Customer response | Internal action |
| --- | --- | --- |
| Verified products / FAQ | Grounded answer or up to three products | Record used IDs, data versions, outcome, and duration. |
| Missing customer detail | One or two short questions | Preserve criteria and await reply. |
| No suitable option, unknown facts, missing/conflicting FAQ | Honest fallback; offer criteria adjustment or verified contact | Create a review ticket with the actual reason. |
| Unsupported public comment | Hold reply only under owner-approved policy | Always retain the request and create a review ticket. |
| Provider, index, catalog, or queue failure | Temporary error; never label it “out of stock” | Record a technical failure and alert the configured operator where available. |
| Out of scope / explicit staff request | Brief scope explanation or actual handoff status | Explicit staff requests enter the support queue. |

Use SQLite for a durable pilot queue: ticket ID, request/conversation IDs, original question, channel, reason, checked products/sources, timestamp, assignee/queue, and `new / in_progress / resolved` status. Provide staff terminal commands to list tickets, assign them, and change status; a write-only log is insufficient. Deduplicate repeated delivery of the same request ID.

A local ticket is not an external notification. During the pilot, say “recorded for review” only after a successful commit, and only if staff actually monitor that queue. Say “sent to staff” only after the configured handoff succeeds. Otherwise give the shop's approved contact, if supplied. Queue failures must not produce a success claim; write a sanitized technical error to stderr as a last-resort diagnostic.

Store only necessary customer content, use conversation IDs rather than unnecessary personal details, restrict staff access, and define retention with the owner. Questions/history/evidence sent to Ollama Cloud must follow the shop's approved data handling. Never log credentials. Public reply automation stays disabled until the owner approves channel behavior.

## 6. Implementation order and completion checks

Rough planning estimate: **7–10 developer working days after usable data is supplied** for a terminal pilot, including tests. Data cleanup, owner decisions, external channel integration, and production hosting are separate dependencies, not promised within that estimate.

| Phase | Work | Done when |
| --- | --- | --- |
| 1 — Data contract, 1 day | Confirm decisions below; prepare small real catalog/FAQ and synthetic edge fixtures | Owner approves fields and authoritative sources; bad data is rejected without overwriting good data. |
| 2 — Structured search, 1–2 days | Implement catalog loading, exact/alias lookup, freshness checks, budget/quantity/ingredient filters | Tests prove returned products meet every mandatory condition and unknown facts are handled honestly. |
| 3 — Intent and state, 1–2 days | Add validated extraction, clarification, criteria updates, conversation isolation | Follow-ups preserve restrictions beyond three turns; reset and explicit changes behave correctly. |
| 4 — Shop RAG and responses, 1–2 days | Adapt indexing/retrieval/prompt and controlled rendering | Approved FAQ conditions are preserved; changed/removed records do not leak stale policy; no unvalidated partial output is published. |
| 5 — Staff queue, 1 day | Add durable tickets, staff commands, publication decisions, failure handling | Held comments remain reviewable; queue failure never claims a handoff; tickets survive restart. |
| 6 — Full catalog pilot, 1–2 days | Import all products; run shop-approved scenarios; measure results and review failures | Mandatory acceptance cases pass and owner approves observed answer quality and operational process. |

Touch points for implementation: existing function callers in `app.py`, mocks/contracts in `tests/test_app.py`, new business/queue tests and fixtures, `README.md` instructions, `.env.example` only for genuinely needed settings, and `.gitignore` for runtime databases/logs. No package/export layer exists to update. Keep credentials and student data out of shop evidence; isolate the shop collection rather than deleting the existing demo collection.

Validate source records before any index deletion. In the terminal pilot, rebuild while chat is stopped. Before a live service needs uninterrupted updates, use a validated replacement index and switch only after it is ready; keep the last working index for rollback. Price/stock refreshes alone should not require re-embedding descriptions.

## 7. Acceptance tests before launch

Use `unittest`, a small synthetic catalog, temporary SQLite/Chroma stores, mocked embeddings, and the existing `httpx.MockTransport` approach for automated checks. These checks must not call paid services or change the real catalog/index. Separately run a small real-model evaluation on owner-approved questions and measure response time/call usage.

| Scenario | Required result |
| --- | --- |
| Exact SKU and specific variant | Correct catalog name, price, packaging, and verified availability; no neighboring variant substituted silently. |
| Missing requested item / confirmed out of stock | State which condition is known; explain any substitute and preserve hard conditions. |
| Budget boundary and multiple units | Correct integer calculation; “under” versus “at most” respected; clarify unit/total ambiguity. |
| Requested ingredient avoidance | Exclude present, may-contain, unknown, stale, or contradictory evidence; never infer absence from missing text. |
| “Cheaper” after several turns | Retain ingredient/packaging constraints and honor the relevant previous price; clarify ambiguous references. |
| Zero or fewer than three eligible products | Return only actual matches; do not pad the answer or call uncertain stock “sold out.” |
| Approved FAQ with conditional answer | Preserve geographic, order, date, and other applicable conditions. |
| Missing, expired, conflicting policy or mixed question | No invented policy; identify unresolved portion and create the appropriate ticket. |
| Malformed LLM extraction / prompt injection | No guessed criteria, leaked data, unsupported products, or unauthorized actions. |
| Catalog update, removal, stale timestamps | Current facts used; obsolete evidence blocked until refresh; invalid import preserves valid data. |
| Public suppression / private fallback | Correct publication decision, retained request, and durable review ticket where required. |
| Handoff failure / duplicate request / restart | No false success; one ticket per delivery ID; staff can retrieve and resolve persisted tickets. |
| API timeout / retrieval failure / interrupted generation | Error outcome; no “no products” diagnosis and no incomplete answer stored as completed. |
| Two conversations / reset | No cross-customer criteria leakage; reset clears history and restrictions. |

Mandatory gate: all deterministic constraint, provenance, publication, and handoff checks pass. Review generated language as well; passing unit tests does not establish real-model factual reliability. Record answer correctness, recommendation relevance, handoff rate, latency, and model-call usage on a fixed shop-approved evaluation set. The owner must agree quantitative thresholds before launch; do not invent them now.

## 8. Owner decisions and launch dependencies

These are unresolved in the business document. The terminal implementation can proceed with explicit assumptions; customer-facing automation depends on the relevant answers.

1. **Channel:** private messages, public comments, or both? Which exact public cases should be held, and how does staff respond afterward?
2. **Support:** who monitors the queue, where should notifications go, what response time applies, and what contact can be offered if handoff fails?
3. **Catalog:** are 200 entries SKUs or parent products? What is the official price/stock source, update cadence, and allowed age for each fact?
4. **Recommendations:** approve the three-result limit and identify mandatory versus negotiable conditions. Additional-item suggestions remain disabled unless approved; no unapproved budget increase.
5. **Approval and data handling:** who approves policy/ingredient changes, what customer text may go to Cloud, and how long should support/audit data be retained?

After the terminal pilot is accepted, add only the chosen channel adapter. Before exposing an API/webhook, implement its authentication/signature checks, trusted conversation mapping, request limits, delivery deduplication, and channel-specific publication rules. Configure monitored support delivery, protect and back up runtime data, and document restart/rollback procedures. Hosting and ongoing Cloud cost should be sized from measured traffic and usage, not assumed from a 200-product catalog.

**First implementation task:** create the small approved data set and deterministic product filter with budget, quantity, freshness, and ingredient tests. Then connect it to the existing chatbot loop. This establishes the core recommendation rules before adding language generation.

Not included in this planning pass: implementation, runtime tests, live inventory/model verification, or external channel setup. Main risk: recommendations cannot be more reliable than the approved catalog, ingredient evidence, and price/stock update process.
