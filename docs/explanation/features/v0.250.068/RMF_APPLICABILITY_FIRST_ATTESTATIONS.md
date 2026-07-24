# RMF applicability-first attestations

Attestation interviews now distinguish whether a control applies from whether
its implementation is sufficient.

When owner answers affirmatively establish that a control likely has no
in-scope subject, RMF records the lower-trust attestation and creates a
manager-review applicability candidate. It does not mark the implementation as
failed and does not approve Not Applicable automatically.

The Attestations transcript labels these outcomes as **Applicability review
required** and links to the existing Controls review panel. Candidate controls
are excluded from the direct Analysis handoff until a manager approves or
rejects the applicability decision.

Internet reachability alone does not establish public accessibility. For
example, an authenticated API exposed through Azure API Management is
distinguished from content available anonymously to the general public.
