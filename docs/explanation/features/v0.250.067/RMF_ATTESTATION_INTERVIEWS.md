# RMF collaborative attestation interviews

The RMF workspace Attestations tab now hosts gap-driven system-owner interviews.
Owners and administrators start or cancel sessions; any workspace member may
answer the current grounded question. Every verbatim answer is attributed to
the authenticated participant and retained in the session transcript.

Attestations are visibly labeled **self-reported and unverified**. They remain
separate from observed evidence and cannot independently establish that a
control is implemented. Completed answers make related analysis stale; an
Owner or Admin can explicitly send the attested controls back to Analysis.

The collaborative workflow uses session revisions and turn IDs to reject stale
or duplicate answers. If another participant advances the interview first, the
page reloads the current session rather than overwriting their work.

Live Azure MCP corroboration and ad-hoc `/look` queries remain available in the
RMF CLI but are intentionally deferred from this first hosted release.
