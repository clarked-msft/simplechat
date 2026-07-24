# RMF Analysis Workflow

Version: 0.250.063

The RMF workspace Analysis tab runs durable baseline or selected-control
analysis jobs against the evidence already synchronized with RMF. The
selected-control picker is populated from the current impact-level baseline
reported by RMF and can be filtered by control ID, title, or family.

Owners and administrators can start and cancel jobs. Document managers and
users have a read-only view of capabilities, active progress, control failures,
and job history. Deep analysis performs a wider second pass for weak drafts;
Fresh analysis bypasses reusable cached responses. The interface warns that
both options can increase duration or model usage.

SimpleChat validates the request shape, proxies it with trusted workspace and
role headers, and leaves catalog control-ID validation to RMF. The browser
polls durable jobs with bounded retries and refreshes the Overview dashboard
when a job reaches succeeded, partial, failed, stale, or cancelled status.
All service values are rendered with safe DOM APIs; no RMF credential is
exposed to the browser.
