# Optional RMF Workspace Shell

Version 0.250.060 introduces the disabled-by-default shell for integrating
SimpleChat group workspaces with the RMF Workload Analysis Tool.

Administrators must first enable **RMF Workspaces** in Admin Settings. A group
owner or administrator can then enable RMF for the active group from the new
RMF page. Enabling the shell does not import documents, call the RMF service, or
start an analysis.

The shell reserves dedicated views for:

- Overview
- Evidence
- Analysis
- Controls
- Attestations
- Export

Each group remains disabled independently until a group owner or administrator
opts in. Disabling the global setting removes RMF navigation and rejects both
the frontend and backend RMF routes.
