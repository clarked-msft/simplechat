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

## Recurring-selection measurement

Set `RMF_ANALYSIS_METRICS_KEY` to a dedicated random secret to enable
privacy-preserving measurement of custom selections. SimpleChat records only
HMAC-derived workspace/scope digests, selected and baseline control counts,
selection duration, and submission outcome. It does not record workspace IDs,
user IDs, control IDs, or control text. Missing or failed telemetry never blocks
analysis.

Use the `rmf_custom_analysis_selection` and `rmf_analysis_baseline_size` event
names in Application Insights to measure repeated workspace scopes, median
selection duration, selected-to-baseline ratio, and submission errors. Treat a
missing metrics key or either event stream as insufficient evidence rather than
as proof that reusable profiles are unnecessary.
