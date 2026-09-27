# Changelog

All notable project changes are recorded here. Entries retain the short commit
identifier so the history remains auditable against Git.

## [Unreleased]

No changes yet.

## [0.1.3] — 2026-09-27

Released after reconciling the temporal-engine rebuild and Phase 2 hardening
onto the remote v0.1.2 mainline.

### Temporal engine rebuild

- Rebuilt temporal resolution around `GitTemporalProvider`, which indexes
  reflog, commit, stash, and branch chronology into one bounded normalized event
  stream.
- Added Git-backed revision resolution with `git rev-list --timestamp` for
  `HEAD~N` and `HEAD^N` references.
- Resolved repository ordinals such as `3 pushes ago` and `2 commits ago` from
  the indexed stream, preserving `anchor` provenance alongside timestamp,
  confidence, and `resolved_from`.
- Normalized Git-native expressions such as `1.hour.ago` into ISO-8601 bounds;
  arbitrary natural-language or Git date expressions are no longer passed
  through to `--since`/`--until`.
- Added normalized-bound handling for Git timeline, session timeline, shell
  activity, developer velocity, resources, and temporal snapshots.
- Migrated Git and session timeline chronology to the shared provider stream and
  preserved temporal provenance through timeline and snapshot payloads.
- Added provider, ordinal-anchor, normalized-bound, and regression coverage.

### Phase 2 hardening

- Routed Phase 2 resource windows through the shared temporal resolver.
- Prevented Git-controlled prompt text from breaking untrusted-data delimiters by
  encoding angle brackets in payload content.
- Stopped resource errors and returned resource URIs from echoing caller-selected
  filesystem paths; query-selected repositories still undergo repository
  validation.
- Added malformed-parameter handling and end-to-end HTTP JSON-RPC coverage for
  `resources/list`, `resources/read`, `prompts/list`, and `prompts/get`.
- Preserved bounded resource output and serializer-gated privacy behavior.

### Project maintenance

- Added this complete changelog through the current working state.

## [0.1.1] — 2026-08-07

- `010b5ac` — Added MCP Registry publishing workflow and package metadata,
  including the published server schema and registry manifest.

## Development between v0.1.1 and v0.1.3

These commits are included in the v0.1.3 release.

- `0abbc16` — Fixed MCP Registry publishing metadata and README references.
- `3a77fd5` — Added the Phase 1 temporal engine: fuzzy ranges, sessions, and
  deterministic cache support.
- `7207fef` — Added MCP Resources and Prompts for the 2026 protocol surface.
- `4c57261` — Documented the Phase 2 surface and phased development plan.
- `d901ef1` — Hardened Phase 2 protocol handling and focused surface tests.
- `93d70fc` — Renamed the Phase 2 resource/prompt test module.
- `b9ad0f1` — Hardened server boundaries, privacy scrubbing, subprocess
  isolation, repository/history validation, request limits, and security tests.
- `1a2ce0a` — Finished remediation quality gates and compatibility fixes across
  the server, tools, schemas, CI metadata, and tests.

## [0.1.0] — 2026-08-06

### Foundation

- `b36132a` — Created project scaffolding, packaging metadata, README, Python
  version pin, ignore rules, and locked dependencies.
- `badc4c0` — Implemented the initial Starlette MCP server and first tool
  registry, including Git timeline, working-tree, activity, session, context,
  safety, branch, stash, velocity, conflict, and commit tools.
- `badaded` — Improved core Git timeline, activity, working-tree analysis, and
  tool registration behavior.
- `bfb9f70` — Added workspace checkpoint comparison and temporal snapshot tools.

### Compliance, tests, and privacy

- `8fa7d8d` — Added the pytest harness, ephemeral Git fixtures, CI workflow,
  telemetry envelope/schema validation, tool output contracts, annotations, and
  the initial privacy scrubber.
- `3b71c04` — Added the developer handbook and protocol/tool documentation.
- `e671a8a` — Added the Apache-2.0 license and package license metadata.

### Distribution and security automation

- `cd67ded` — Added PyPI publishing, CodeQL configuration, security scanning,
  and release workflow automation.

## Release references

- `v0.1.0` → `cd67ded`
- `v0.1.1` → `010b5ac`
- `v0.1.2` → `04b9704`
- `v0.1.3` → release commit created from this tree

[Unreleased]: https://github.com/rootbid/git-telemetry-mcp/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/rootbid/git-telemetry-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/rootbid/git-telemetry-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/rootbid/git-telemetry-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rootbid/git-telemetry-mcp/releases/tag/v0.1.0
