# RMF investigative chat workspace

Implemented in version: **0.250.070**
Updated in version: **0.250.071**
Retry policy updated in version: **0.250.072**
Citation lookup fixed in version: **0.250.073**
Sources key added in version: **0.250.074**
Dedicated generation timeout added in version: **0.250.075**

## Overview

SimpleChat now provides a dedicated `/rmf/chat` workspace for read-only,
grounded investigation of the active group's current RMF assessment snapshot.
All authenticated RMF workspace roles can create and review investigations.
Chat does not expose RMF workflow mutations or live cloud tools.

The workspace includes:

- Collapsible active and archived investigation history.
- A compact header with the current RMF system, impact level, baseline coverage,
  active group, and workspace role when those values are available.
- Backend-provided starter prompts and source inventory counts.
- Full-width, sustained conversation with non-streaming grounded-retrieval
  feedback.
- Sanitized Markdown responses and inline citation chips that load source
  details on demand in an accessible drawer.
- An always-visible Sources key beneath every cited assistant response. It lists
  every citation with its contract-provided title or label and available
  page, section, or location metadata, even when all citations appear inline.
- Explicit insufficient-evidence, unavailable-service, archived, and stale
  assessment states.
- A prominent stale-snapshot banner that disables sending and starts a new
  investigation against the current assessment revision.

## Architecture

The browser only calls authenticated SimpleChat endpoints under
`/api/rmf/workspace/chat`. `route_backend_rmf.py` resolves the active group,
validates membership and request shape, and forwards the request through the
existing server-to-server RMF context in `functions_rmf.py`.

SimpleChat supplies the trusted workspace, user, and mapped role headers to the
paired RMF service. Service credentials and backend storage details are never
returned to the browser.

| SimpleChat endpoint | Paired RMF endpoint |
| --- | --- |
| `GET /api/rmf/workspace/chat/capabilities` | `GET /api/v1/workspaces/current/chat/capabilities` |
| `GET/POST /api/rmf/workspace/chat/sessions` | `GET/POST /api/v1/workspaces/current/chat/sessions` |
| `GET /api/rmf/workspace/chat/sessions/{id}` | `GET /api/v1/workspaces/current/chat/sessions/{id}` |
| `POST /api/rmf/workspace/chat/sessions/{id}/messages` | `POST /api/v1/workspaces/current/chat/sessions/{id}/messages` |
| `POST /api/rmf/workspace/chat/sessions/{id}/archive` | `POST /api/v1/workspaces/current/chat/sessions/{id}/archive` |
| `GET /api/rmf/workspace/chat/sessions/{id}/sources/{source_id}` | `GET /api/v1/workspaces/current/chat/sessions/{id}/sources/{source_id}` |

The `{source_id}` route segment receives the persisted opaque
`ChatCitation.id`. `ChatCitation.source_id` identifies the underlying
evidence, graph, or assessment source and is not used for source-detail lookup.

Message and archive writes require `expected_revision`. Message writes also send
an optional `idempotency_key`. The browser generates a new UUID for each send
and reuses it for one bounded automatic replay after a transport failure,
workspace-busy HTTP 429, or temporary-model HTTP 503. HTTP 409 and all other
responses are not retried. A stale or revision-conflict HTTP 409 locks the
investigation and directs the user to create a new one; a permanent-limit HTTP
409 preserves the draft without incorrectly marking the snapshot stale.

Chat message generation uses `RMF_API_CHAT_TIMEOUT_SECONDS`, defaulting to 180
seconds so grounded generation remains below the App Service request ceiling.
Other RMF calls retain the 15-second `RMF_API_TIMEOUT_SECONDS` default. An
upstream timeout returns HTTP 504 with chat-specific guidance, and the UI
restores the drafted question instead of reporting the RMF service as down.

## Security and accessibility

- The feature remains gated by `enable_group_workspaces`, `enable_rmf`, active
  group membership, and the group's RMF enabled state.
- Conversation and source IDs are validated before proxying and URL-encoded for
  the paired service.
- Assistant Markdown is rendered with the repository's `marked` renderer and
  sanitized with DOMPurify before insertion. User text, source excerpts, and
  metadata use text-only DOM APIs.
- History controls, stale and retrieval status, citation buttons, the source
  drawer, and the composer provide labels and live-region semantics.
- Responsive layouts collapse history on smaller viewports and honor reduced
  motion preferences.

## Backend contract assumptions

- Session list responses are the documented `ChatConversation[]`; the UI also
  tolerates a `{sessions: [...]}` wrapper for compatibility.
- Conversation and source identifiers are URL-segment-safe identifiers up to
  128 characters using letters, numbers, `.`, `_`, `:`, or `-`.
- Successful message, archive, session-detail, and source-detail calls return
  HTTP 200; session creation returns HTTP 201.
- Whitespace-only questions return HTTP 422. Stale/revision conflicts and
  permanent limits return non-retryable HTTP 409, workspace-busy responses
  return retryable HTTP 429, and temporary model failures return retryable HTTP
  503. The UI preserves the question after non-stale failures and presents
  status-specific messages.
- Message `idempotency_key` values are optional opaque strings up to 100
  characters. Browser sends use UUIDs stable across the bounded transport retry.
- `ChatMessage.request_id` may be returned for request tracing. The UI safely
  ignores fields it does not render.
- `starter_prompts` are strings. The UI also accepts objects containing
  `question`, `prompt`, or `label`.
- RMF overview remains available for the optional system, impact-level, and
  baseline context shown in the compact chat header.

## Testing

`functional_tests/test_rmf_investigative_chat.py` validates paired-service path
mapping, payload forwarding, role access, request validation, HTTP
409/422/429/503 semantics, route and navigation wiring, safe Markdown/citation
handling, and the key responsive UI states.
`functional_tests/test_rmf_chat_idempotency.js`
executes the retry helper and verifies stable UUID reuse for transport,
workspace-busy, and temporary-model retries; non-retryable HTTP 409 behavior;
and UUID rotation for a new send.
`functional_tests/test_rmf_chat_citation_source.js` verifies that source-detail
requests use `ChatCitation.id` when it differs from `ChatCitation.source_id`
and that fully inline-referenced citations still construct a complete Sources
key with preserved numbering and available location metadata.

The version update is tracked in `application/single_app/config.py`.
