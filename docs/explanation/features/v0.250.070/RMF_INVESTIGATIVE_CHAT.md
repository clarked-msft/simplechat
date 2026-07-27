# RMF investigative chat workspace

Implemented in version: **0.250.070**

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

Message and archive writes require `expected_revision`. A paired-service HTTP
409 is preserved so the UI can lock the stale investigation and direct the user
to create a new one.

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
- `starter_prompts` are strings. The UI also accepts objects containing
  `question`, `prompt`, or `label`.
- RMF overview remains available for the optional system, impact-level, and
  baseline context shown in the compact chat header.

## Testing

`functional_tests/test_rmf_investigative_chat.py` validates paired-service path
mapping, payload forwarding, role access, request validation, stale HTTP 409
preservation, route and navigation wiring, safe Markdown/citation handling, and
the key responsive UI states.

The version update is tracked in `application/single_app/config.py`.
