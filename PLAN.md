# git-telemetry-mcp — Development Plan

**Protocol target:** MCP `2026-07-28` (stateless HTTP JSON-RPC + SSE)

This plan sequences work from the current baseline to the full "Development Time
Machine" vision. It is ordered by dependency and risk: compliance/safety debt
first (privacy, tests, output contracts), then the temporal engine, then the
2026 spec surface (Resources/Prompts), then the event-driven core, then advanced
semantics, then GA hardening.

---

## 1. Current Baseline

**Shipped and registered (16 tools):** `git_timeline`, `working_dir_delta`,
`dev_activity`, `get_session_timeline`, `explain_uncommitted_drift`,
`trace_file_evolution`, `get_active_context_pack`, `safe_git_reset`,
`safe_git_checkout`, `stash_and_isolate`, `detect_stale_branches`,
`generate_smart_commit`, `get_developer_velocity`, `conflict_prelim_check`,
`get_temporal_snapshot`, `compare_workspace_checkpoints`.

**Transport:** Starlette app; `/mcp` (POST JSON-RPC), `/sse`, `/health`.
`initialize`, `tools/list`, `tools/call`, `notifications/initialized` handled.
Destructive tools use a two-round-trip `input_required` confirmation flow.

**Confirmed gaps (drive the roadmap):**

| Area | State | Mandate / source |
|------|-------|------------------|
| Unit + contract tests | **None exist** | AGENTS.md testing requirements |
| Privacy scrubbing (PII/secrets/tokens) | **None**; shell history read raw | AGENTS.md data-privacy |
| `outputSchema` + `telemetry_payload` wrapper | **Absent** on all tools | Attachment §4/§5 |
| Tool annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`) | **Absent** | Attachment §5 |
| Fuzzy temporal parsing ("last 45m", "3 pushes ago") | Pass-through only | Attachment §3 |
| Resources (`telemetry://`) | Not implemented; `resources/*` → -32601 | Attachment §4 |
| Prompts (`prompts/list`) | Not implemented; `prompts/*` → -32601 | Attachment §4 |
| Deterministic caching / ring-buffer indexer | Every call re-scans disk | Attachment §5 |
| Checkpoint persistence (`.git-telemetry-index`) | None | Attachment §3 |
| Session boundary detection | None | Attachment §2 |
| Anomaly/risk highlighting, transaction bundling | None | Attachment §2 |
| Terminal output ingestion, working-set awareness | None | Attachment §2 |

---

## 2. Guiding Principles

- **Privacy is a gate, not a feature.** No serialized payload leaves a tool
  without passing the scrubber. This blocks Phase 1 exit.
- **Every tool has a machine-checkable contract.** `outputSchema` + a contract
  test are part of "done" for each tool.
- **Stateless request/response.** Caching and the `.git-telemetry-index` are
  *derivable acceleration*, never authoritative state. Cold start must produce
  identical results (slower).
- **Graceful degradation.** Missing shell timestamps, detached HEAD, sparse
  checkout, giant diffs → partial result + `confidence_score`, never a crash.
- **Ship vertically.** Each phase ends with something demonstrable end-to-end.

---

## 3. Phased Roadmap

### Phase 0 — Compliance & Contract Foundation [COMPLETE]

Close the AGENTS.md debt before adding surface area. Highest risk if deferred:
privacy leaks and untested parsers compound with every new tool.

- **Test harness.** `pytest` + `pytest-asyncio`; fixtures that build ephemeral
  git repos (temp dir, scripted commits/reflog/stashes). Add to `pyproject.toml`
  dev deps. Wire a `uv run pytest` task and a CI workflow (`.github/workflows/ci.yml`).
- **Parser/metric unit tests** for the shipped 16 tools' pure functions
  (reflog parse, diff-stat, churn, entropy, conventional-commit inference).
- **`telemetry_payload` wrapper.** Standard envelope: `timezone_offset`,
  `repo_checksum` (SHA of repo path), `confidence_score`, `data`. Central
  helper in `git_telemetry_mcp/schema.py`; all 16 handlers migrated to emit it.
- **`outputSchema` (JSON Schema draft-07)** per tool in the registry; contract
  tests (`tests/test_tools_contract.py`) assert every `tools/call` result
  validates against its `outputSchema`.
- **Tool annotations** in each definition: `readOnlyHint`, `destructiveHint`,
  `idempotentHint`, plus `annotations` dict for MCP spec compliance.
- **Privacy scrubber (v1).** `git_telemetry_mcp/privacy.py` with default regex
  set (API keys, PEM blocks, `Authorization:` headers, URLs with creds, common
  token shapes) + `GIT_TELEMETRY_EXCLUDE_PATTERNS` env config. Integrated in
  `serialize_telemetry_payload` so no output bypasses it. Unit tests in
  `tests/test_privacy.py`.

**Status & Exit criteria met:** `uv run pytest` (25 tests passing) green in CI;
every tool returns a schema-valid `telemetry_payload`; scrubber redacts fixture
secrets with 0 leaks; annotations present in `tools/list`.

**Key Learnings & Architectural Refinements from Phase 0:**
1. **Central Serializer Gate:** Integrating `scrub_data()` directly inside `serialize_telemetry_payload()` guarantees zero-leak serialization across all tools without handler-level boilerplate.
2. **Interactive `input_required` Flow:** Two-round-trip destructive tools (`safe_git_reset`, `safe_git_checkout`) return a structured `resultType: "input_required"` dictionary during confirmation prompts, and emit scrubbed `telemetry_payload` JSON upon confirmed execution.
3. **Composite Tool Unwrapping:** Compositional tools (e.g. `get_temporal_snapshot`) invoke sub-tools, unwrap their inner `data` payloads, and re-wrap the aggregated snapshot into a single clean envelope.
4. **Dynamic Confidence Scoring:** Confidence scores dynamically degrade (e.g. `0.8` for >30-day reflog or timestamp-less shell history) to signal precision limits, laying the foundation for Phase 1 fuzzy temporal resolution.
### Phase 1 — Temporal Engine Core [COMPLETE]

Make "time" first-class instead of pass-through strings.

- **Fuzzy temporal parser** (`git_telemetry_mcp/temporal.py`): natural-language
  ranges ("last 45m", "right before lunch"), ordinal git refs ("3 pushes ago",
  "2 checkouts ago") resolved via reflog ordering, and absolute ISO ranges →
  normalized `{since, until, resolved_from}`. Report ambiguity in
  `confidence_score`. Rework `get_temporal_snapshot` to use it.
- **Session boundary detection**: segment activity into logical sessions by
  inactivity gap (>15 min), branch switch, or reflog discontinuity. Expose
  session IDs so callers can request "Session #3" without timestamps. Back
  `get_session_timeline` with it.
- **Deterministic cache**: keyed by `SHA(repo_path + resolved window + git tip)`;
  in-memory LRU with TTL. Identical consecutive calls skip re-scan; cold cache
  yields identical output. Cache-hit metric surfaced in `_meta`.

**Exit criteria:** parser unit tests cover NL/ordinal/ISO cases;
`get_temporal_snapshot("last 45m")` returns a correct bounded window; session
segmentation validated against a scripted-repo fixture; cache hit/miss test.

**Status & Exit criteria met:** `uv run pytest` green — 50 tests (25 new for
Phase 1). New modules: `temporal.py` (fuzzy parser), `sessions.py` (boundary
detection), `cache.py` (TTL/LRU). `get_temporal_snapshot` and
`get_session_timeline` reworked and still schema-valid in the contract suite.
- Parser unit tests cover NL duration, colloquial, ordinal-reflog, ISO, git-native, and ambiguous-fallback cases (`tests/test_temporal.py`).
- `get_temporal_snapshot("last 45m")` returns a window with `until - since == 45m` and `resolved_from == "relative_duration"` (`tests/test_cache.py::test_snapshot_bounded_window`).
- Session segmentation validated against the `repo_with_history` fixture, incl. `Session #N` selection (`tests/test_session_timeline.py`, `tests/test_sessions.py`).
- Cache hit/miss + cold==warm equality + TTL expiry + LRU eviction covered (`tests/test_cache.py`).

**Key Learnings & Architectural Refinements from Phase 1:**
1. **Pure/impure parser split:** `parse_*` helpers are subprocess-free and directly unit-testable; only ordinal resolution (`_resolve_ordinal`) and `git_tip` touch git. This kept the fuzzy grammar (NL/ordinal/ISO/colloquial) fully deterministic under an injectable `now`.
2. **Cache key = expression, not resolved timestamps:** Relative windows resolve against wall-clock `now`, so keying on resolved `{since, until}` would *never* hit. Keying on `SHA(repo + normalized expr + git tip + granularity)` makes identical consecutive calls deterministic within the TTL, while a moved tip invalidates — preserving the "derivable acceleration, never authoritative state" invariant.
3. **`_meta.cache` inside the envelope `data`:** The strict `additionalProperties: false` telemetry envelope forbids top-level extras, so the cache-hit metric lives at `data._meta.cache` (`hit`/`miss`). Cold and warm payloads are byte-identical apart from this marker (asserted).
4. **Plural action grammar:** git pluralizes actions irregularly ("checkout"→"checkouts", "push"→"pushes"); the ordinal regex strips `(?:es|s)?` so both `-s` and `-es` forms normalize to the singular reflog verb.
5. **Reflog-only ordinals degrade, never crash:** Actions absent from the HEAD reflog (e.g. `push`) resolve to `reflog_ordinal_unresolved` with `confidence 0.4` and a 1-hour fallback window rather than failing — consistent with the graceful-degradation principle.

### Phase 2 — 2026 Spec Surface: Resources & Prompts

- **Resources.** Handle `resources/list` + `resources/read`; advertise the
  capability in `initialize`. Implement `telemetry://` scheme:
  `telemetry://session/current`, `telemetry://history/standup` (Markdown
  standup), `git://delta/latest` (raw unified diff of latest commit chain).
  Resources reuse Phase 0 scrubbing + Phase 1 engine.
- **Prompts.** Handle `prompts/list` + `prompts/get`; advertise capability.
  Implement `review_debug_loop`, `generate_commit_message_context`,
  `handover_notes` — each assembles a context blob from existing tools.
- **Capability negotiation.** Extend `initialize` capabilities to
  `{tools, resources, prompts}`; conformance test against the declared set.

**Status & Exit criteria met:** Resources and prompts are implemented, advertised during
`initialize`, and covered by focused protocol tests. Resource payloads pass the central
privacy serializer gate; invalid resource/prompt names return structured errors.
Phase 2 hardening additionally validates repository boundaries and bounds resource output.

### Phase 3 — Event-Driven Indexer & Checkpoint Persistence

Performance backbone for `<50ms` answers on large repos.

- **File watchers** (inotify on Linux; abstraction leaves room for
  kqueue/ReadDirectoryChangesW) on `.git` refs/logs, shell history, and tracked
  saves. Stream events into a bounded ring buffer.
- **`.git-telemetry-index`** lightweight on-disk index (gitignored) so restart
  resumes without full reflog re-scan. Treated as a cache: corrupt/missing →
  rebuild transparently. Never stores unredacted commit/telemetry blobs
  (AGENTS.md).
- **Warm-path wiring**: temporal tools read the ring buffer/index first, fall
  back to subprocess `git` on miss.

**Exit criteria:** benchmark on a synthetic large repo (≥100k reflog entries)
shows warm-query p95 `<50ms`; kill-and-restart reproduces results from the
index; deleting the index degrades gracefully to a cold scan.

### Phase 4 — Advanced Semantic Intelligence

Layer meaning onto the raw timeline. Each item ships as an annotated read-only
tool and/or enrichment of an existing one.

- **Anomaly & risk highlighting**: flag `reset --hard`, `push --force`,
  `clean -fd` as "Context Shifts" with before/after commit trees.
- **Transaction bundling**: group causally linked events (e.g. `npm install` →
  lockfile change → `git add` → commit) into one labeled transaction.
- **Reverse differential tracing**: given a line/bug, trace back through
  `log -p` + reflog to the last modification and the correlated terminal input.
- **Stateful context merging**: unify staged + unstaged + committed into one
  "Project Patch" since last successful build (extends
  `explain_uncommitted_drift`).
- **Terminal output ingestion (opt-in)**: capture stdout/stderr of long
  commands; link build/test failures to the file state at that moment. Off by
  default; scrubbed.
- **Working-set awareness (opt-in)**: track editor-open files via file
  watcher/LSP hints to add a "focus" dimension.

**Exit criteria:** each feature has a scripted-repo scenario test proving the
semantic claim (e.g. bundling groups the dependency-update sequence into one
transaction; anomaly tool surfaces a forced reset with correct before/after).

### Phase 5 — Scale, Degradation & GA 

- Edge-case hardening: detached HEAD, sparse checkout, shallow clone, huge
  diffs (stream/cap, no memory bloat), missing shell timestamps.
- Load/perf pass on large monorepos; confirm ring-buffer memory bounds.
- Full-suite green, contract tests across all tools/resources/prompts, docs
  (README + HANDBOOK) regenerated, version bump, `0.1.0 → 1.0.0` release.

**Exit criteria:** clean full-suite run; documented degradation behavior for
each edge case; tagged release.

---

## 4. Cross-Cutting Tracks (continuous)

- **Docs:** keep README/HANDBOOK tool tables in sync each phase; no drift.
- **Privacy regression tests** grow with every new data source touched.
- **Schema conformance** runs in CI on every `tools/list` and `tools/call`.

## 5. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Shell-history formats vary (no timestamps) | Weak correlation | Detect format; degrade with `confidence_score`; document `HISTTIMEFORMAT`/`EXTENDED_HISTORY` |
| Privacy leak in a new data source | Compliance breach | Scrubber is a mandatory serialization gate; regression tests per source |
| Stateless spec vs. index/cache | Nondeterminism | Index/cache strictly derivable; cold==warm equality tests |
| File-watcher portability | Non-Linux gaps | Abstract watcher; Linux (inotify) first, others behind capability probe |
| Large-repo memory/latency | Unusable at scale | Bounded ring buffer, streamed diffs, `<50ms` warm-path benchmark gate |

## 6. Milestone Summary

| Phase | Headline deliverable |
|-------|----------------------|
| 0 | Tests, privacy scrubber, output schemas + annotations |
| 1 | Fuzzy temporal parser, sessions, deterministic cache |
| 2 | `telemetry://` Resources + Prompts (2026 spec) |
| 3 | Event-driven indexer + `.git-telemetry-index` |
| 4 | Anomaly, bundling, reverse tracing, context merge |
| 5 | Degradation hardening + 1.0.0 GA |
