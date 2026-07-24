# Reusable RMF Export workspace

RMF workspaces now support reusable XLSX template families with immutable
versions. Owners and administrators can inspect a workbook, map its control ID
and canonical RMF fields, save versions, and activate or retire them. Every
workspace member can review readiness, generate an export without an AI call,
and download the authenticated workbook and manifest from export history.

SimpleChat rebuilds template multipart requests server-side, supplies the
trusted workspace identity headers, validates IDs, fields, file type, and a
20 MB size limit, preflights XLSX ZIP expansion, entry, sheet, row, and column
bounds, rejects macros and external links, and streams downloads without
exposing service credentials or storage URLs. Downloads are refused unless the
export reports successful completion.

Export generation uses an idempotency key bound by the paired service to the
request fingerprint. Network retries retain the key; terminal failed exports
clear it so an explicit retry creates a new request. A key reused with different
inputs must return HTTP 409.

## Paired RMF service invariants

The paired RMF service remains responsible for database and workbook invariants:

- Escape every written string beginning with `=`, `+`, `-`, `@`, tab, or
  carriage return so generated cells retain safe text semantics.
- Allocate immutable version numbers transactionally. A durable upload becomes
  `inactive`; an inactive version may activate with its current revision,
  atomically retiring the prior active version. Active versions may retire.
  Retired versions can never reactivate, and SimpleChat exposes no restore
  action.
- Capture one export fingerprint, re-check it after snapshot creation, and bind
  it to the idempotency key. A mismatched reuse returns 409.
- Mark completion only after both workbook and manifest are durably stored.
  Failed or partial artifacts are never downloadable.
- Persist template, workbook, and manifest SHA-256 digests with export history.

The paired API returns a flat list of immutable template versions. Each
version's `id` is the `template_id` used for detail, activation, retirement,
readiness, and export generation. Mutations send the current `revision`;
the UI offers Activate only for inactive versions, Retire only for active
versions, and no mutation for retired versions. Ordinary members receive active
versions only; managers may review inactive, active, and retired history.
Inspection header candidates are selected from the returned sheet and
header-row map, and creation submits the inspected digest with the nested
profile.
