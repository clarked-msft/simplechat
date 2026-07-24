# RMF Overview Dashboard

Version: 0.250.061

The RMF workspace Overview tab now presents a deterministic dashboard for the
system bound to the active group workspace.

The dashboard displays system identity, evidence and policy inventory,
topology counts, impact-level baseline analysis coverage, AI-drafted control
posture, open requirement gaps, and a contextual next action. These values are
analysis summaries, not a compliance score or authorization decision.

SimpleChat retrieves the overview through its authenticated backend proxy. The
browser never receives the RMF service credential and never connects directly
to RMF persistence.
