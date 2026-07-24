# RMF Evidence Workflow

Version: 0.250.062

The RMF workspace Evidence tab synchronizes current group document revisions as
Observed Evidence, Organization Policy, or Azure Resource JSON. Supported file
formats come from the RMF service capabilities response, including Visio
`.vsdx`, using its `supported_suffixes` field; SimpleChat does not apply a
separate RMF format allowlist.

Owners, administrators, and document managers can add, resync, retry, and
withdraw imports. Other group members have a read-only view. The table reports
queued, processing, current, stale, failed, superseded, and withdrawn states,
plus the durable `withdrawing` cleanup state, and marks an import stale when its
source document differs from the current revision in the same revision family.
Azure Resource JSON can only be selected for `.json` documents.

Only a SimpleChat document ID and classification are sent by the browser.
SimpleChat reauthorizes the active group, verifies the exact current revision,
copies the mutable current alias to its immutable revision path with
document-ID and ETag checks, streams that snapshot to a temporary file while
computing SHA-256, and sends streaming multipart data to RMF through the
server-side authenticated proxy. RMF requests do not follow redirects, so the
service credential cannot be forwarded to another origin. RMF service
credentials and storage details are never exposed to the browser.
