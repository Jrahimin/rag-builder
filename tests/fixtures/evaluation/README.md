# Captured evaluation fixtures

The message-runner and Phase 2 regression tests load the three October 1 captures
through `tests/unit/modules/conversations/captured_fixture_helpers.py`. The loader
pins SHA256 of canonical UTF-8 JSON, including provenance and all test inputs;
LF/CRLF checkout differences do not change that integrity check.

Original `raw_path`, `source_path` and `source_file` values and their capture hashes
remain provenance. CI never opens those ignored local paths. Q1-Q4 replay obtains
the exact repair requirements and queries from the tracked failure fixture, bound
to each timing case by original path, SHA256 and question. Budget source rows keep
their individual content-hash assertions.

Before deliberately changing a capture or pinned hash, verify the relevant curated
fields against the original hashed source. Store only the required nonsecret
projection; keep raw responses, credentials and private proof outside the repository.
The fixture-integrity tests reject altered data and accept both checkout line endings.
