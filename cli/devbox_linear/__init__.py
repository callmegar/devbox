"""devbox-linear — MCP wrapper over Linear's GraphQL API.

Builds on an in-SSM Linear API key (same key ao's tracker uses). Designed to
let multiple ao workers query/update issues in parallel safely, by exposing
explicit issue-dependency semantics (Linear `blocks` relations) plus a
`list_ready_issues` filter that only returns work with no open blockers.
"""
