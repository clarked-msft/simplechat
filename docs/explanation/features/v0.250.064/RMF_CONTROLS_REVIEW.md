# RMF Controls Review

The Controls tab provides a read-only review of every control in the RMF
system's selected impact-level baseline.

Reviewers can search and filter controls by family, evidence freshness,
implementation status, and whether gaps remain. Each control shows its current
or latest AI-drafted implementation statement, confidence, rationale,
responsible role, inheritance, unmet requirements, citations, and immutable
generation history.

Controls are labeled:

- **Current** when the draft matches the current evidence and policy corpus.
- **Stale** when evidence or policy changed after the draft was generated.
- **Not analyzed** when no draft exists.

All workspace members may review Controls. Owners and Admins can use **Analyze
this control** to preselect the control in the existing Analysis tab, where
Fresh and Deep options remain explicit. This release does not add manual edits
or approvals; those require a separate audited assessor-disposition model.
