"""devbox-linear MCP server — exposes Linear's API to Claude with first-class
issue-dependency semantics so multiple ao workers can run in parallel.

Tools (write-leaning, since planning sessions and workers both create/mutate):
  create_issue        new issue, optionally with parent (epic) + depends_on
  link_blocks         add a "X blocks Y" relation between two issues
  unlink_blocks       remove the inverse
  list_ready_issues   issues matching a filter AND with no open blockers
  list_issues         same filter, no blocker check
  get_issue           full detail incl. parent, state, blockers
  update_issue        state / assignee / labels / priority / description
  claim_issue         atomic-ish: flip to a "started" state + assign (for workers)
  add_comment         post a comment
  list_workflow_states  enumerate a team's workflow states (so callers know names)

Refs everywhere accept either a Linear identifier ("MAT-123") or a UUID; the
server resolves to UUIDs as needed for mutations. Team can be a key ("MAT") or
UUID. Labels are resolved by name per team.

Concurrency note: list_ready_issues + claim_issue is the intended flow for
parallel workers. claim_issue flips state to "started" (and optionally assigns),
which is the de-facto lock — Linear's mutation is strongly consistent, so two
workers racing to claim the same issue will see one succeed and the other see
the state already changed if they re-read. Workers should re-read after claim.

Configuration via env vars (set by the wrapper from SSM):
  LINEAR_API_KEY            personal API key (required)
  LINEAR_DEFAULT_TEAM_KEY   optional team key (e.g. "MAT") used when callers
                            omit `team`. If unset, callers must pass `team`.

Missing API key doesn't crash the server — each tool returns a structured
{"error": "..."} so Claude can surface the remediation.
"""

from __future__ import annotations

import functools
import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LinearClient, LinearError

mcp = FastMCP("devbox-linear")

DEFAULT_TEAM_KEY = os.environ.get("LINEAR_DEFAULT_TEAM_KEY", "").strip()

# Per-process caches. The MCP runs as a stdio child per Claude session, so a
# fresh process means a fresh cache — no stale-team-uuid risks across deploys.
_team_uuid_cache: dict[str, str] = {}
_state_cache: dict[str, list[dict]] = {}  # team_uuid -> list of {id,name,type,position}
_label_cache: dict[str, dict[str, str]] = {}  # team_uuid -> {label_name_lower: label_id}
_viewer_id: str | None = None

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_IDENT_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


# ---------- resolvers --------------------------------------------------------

def _client() -> LinearClient:
    return LinearClient()


def _resolve_team_uuid(team: str | None) -> str:
    """Accept team UUID or key (e.g. 'MAT'); fall back to LINEAR_DEFAULT_TEAM_KEY."""
    ref = (team or DEFAULT_TEAM_KEY or "").strip()
    if not ref:
        raise LinearError(
            "no team specified and LINEAR_DEFAULT_TEAM_KEY is unset. Pass team='MAT' "
            "or set /devbox/linear-default-team-key in SSM."
        )
    if _UUID_RE.match(ref):
        return ref
    if ref in _team_uuid_cache:
        return _team_uuid_cache[ref]
    data = _client().query(
        "query($k:String!){teams(filter:{key:{eq:$k}}){nodes{id key}}}",
        {"k": ref},
    )
    nodes = ((data.get("teams") or {}).get("nodes") or [])
    if not nodes:
        raise LinearError(f"no Linear team with key '{ref}' (API token may lack access)")
    _team_uuid_cache[ref] = nodes[0]["id"]
    return nodes[0]["id"]


def _resolve_issue_uuid(ref: str) -> str:
    """Accept an issue UUID or identifier ('MAT-123') and return the UUID.

    Linear's `issue(id: ...)` query accepts both forms, but mutations that
    take `issueId` or `relatedIssueId` want the UUID specifically.
    """
    ref = (ref or "").strip()
    if not ref:
        raise LinearError("issue ref is empty")
    if _UUID_RE.match(ref):
        return ref
    if not _IDENT_RE.match(ref):
        raise LinearError(f"issue ref '{ref}' is neither a UUID nor a TEAM-NUMBER identifier")
    data = _client().query("query($r:String!){issue(id:$r){id}}", {"r": ref})
    issue = data.get("issue")
    if not issue:
        raise LinearError(f"no issue with identifier '{ref}'")
    return issue["id"]


def _viewer_uuid() -> str:
    global _viewer_id
    if _viewer_id is None:
        data = _client().query("query{viewer{id}}", {})
        _viewer_id = (data.get("viewer") or {}).get("id") or ""
        if not _viewer_id:
            raise LinearError("could not resolve viewer (token may be invalid)")
    return _viewer_id


def _team_states(team_uuid: str) -> list[dict]:
    if team_uuid in _state_cache:
        return _state_cache[team_uuid]
    data = _client().query(
        """query($t:String!){
          team(id:$t){
            states{nodes{id name type position}}
          }
        }""",
        {"t": team_uuid},
    )
    nodes = (((data.get("team") or {}).get("states") or {}).get("nodes") or [])
    _state_cache[team_uuid] = nodes
    return nodes


def _resolve_state_id(team_uuid: str, state: str) -> str:
    """`state` may be a workflow state name ('In Progress'), a type
    ('started','unstarted','backlog','completed','canceled','triage'), or a UUID."""
    state = state.strip()
    if _UUID_RE.match(state):
        return state
    states = _team_states(team_uuid)
    # exact name (case-insensitive)
    by_name = next((s for s in states if (s.get("name") or "").lower() == state.lower()), None)
    if by_name:
        return by_name["id"]
    # by type — pick lowest position so 'started' lands on the team's "In Progress"
    # rather than "In Review" or similar later states
    by_type = sorted(
        (s for s in states if (s.get("type") or "").lower() == state.lower()),
        key=lambda s: s.get("position") or 0,
    )
    if by_type:
        return by_type[0]["id"]
    available = ", ".join(f"{s['name']} ({s['type']})" for s in states)
    raise LinearError(f"no workflow state matching '{state}' for team. Available: {available}")


def _team_labels(team_uuid: str) -> dict[str, str]:
    if team_uuid in _label_cache:
        return _label_cache[team_uuid]
    data = _client().query(
        """query($t:String!){
          team(id:$t){labels{nodes{id name}}}
        }""",
        {"t": team_uuid},
    )
    nodes = (((data.get("team") or {}).get("labels") or {}).get("nodes") or [])
    _label_cache[team_uuid] = {(n.get("name") or "").lower(): n["id"] for n in nodes}
    return _label_cache[team_uuid]


def _resolve_label_ids(team_uuid: str, names: list[str]) -> list[str]:
    by_name = _team_labels(team_uuid)
    out: list[str] = []
    missing: list[str] = []
    for n in names:
        lid = by_name.get((n or "").lower())
        if lid:
            out.append(lid)
        else:
            missing.append(n)
    if missing:
        raise LinearError(
            f"unknown label(s) {missing} for team. Existing labels: {sorted(by_name.keys())}"
        )
    return out


def _resolve_user_uuid(user: str) -> str:
    """Accept 'me', a UUID, or an email/displayName/name."""
    user = (user or "").strip()
    if not user:
        raise LinearError("user ref is empty")
    if user.lower() == "me":
        return _viewer_uuid()
    if _UUID_RE.match(user):
        return user
    # Linear users query supports filter by email or name
    data = _client().query(
        """query($q:String!){
          users(filter:{or:[{email:{eq:$q}},{name:{eq:$q}},{displayName:{eq:$q}}]}){
            nodes{id email displayName}
          }
        }""",
        {"q": user},
    )
    nodes = ((data.get("users") or {}).get("nodes") or [])
    if not nodes:
        raise LinearError(f"no Linear user matching '{user}'")
    if len(nodes) > 1:
        raise LinearError(
            f"ambiguous user '{user}' — matches {[n.get('email') or n.get('displayName') for n in nodes]}"
        )
    return nodes[0]["id"]


# ---------- shaping ----------------------------------------------------------

def _issue_summary(node: dict) -> dict:
    state = node.get("state") or {}
    parent = node.get("parent")
    project = node.get("project")
    assignee = node.get("assignee")
    return {
        "id": node.get("id"),
        "identifier": node.get("identifier"),
        "title": node.get("title"),
        "url": node.get("url"),
        "state": {"name": state.get("name"), "type": state.get("type")},
        "priority": node.get("priority"),
        "parent": {
            "id": (parent or {}).get("id"),
            "identifier": (parent or {}).get("identifier"),
            "title": (parent or {}).get("title"),
        } if parent else None,
        "project": {
            "id": (project or {}).get("id"),
            "name": (project or {}).get("name"),
        } if project else None,
        "assignee": {
            "id": (assignee or {}).get("id"),
            "displayName": (assignee or {}).get("displayName"),
        } if assignee else None,
        "labels": [l.get("name") for l in ((node.get("labels") or {}).get("nodes") or [])],
    }


_ISSUE_CORE_FIELDS = """
  id identifier title url priority
  state{name type}
  parent{id identifier title}
  project{id name}
  assignee{id displayName}
  labels{nodes{id name}}
"""

_ISSUE_WITH_BLOCKERS = _ISSUE_CORE_FIELDS + """
  inverseRelations(filter:{type:{eq:\"blocks\"}}){
    nodes{
      id type
      issue{id identifier title state{name type}}
    }
  }
"""


# ---------- catch wrapper ----------------------------------------------------

def _wrap(fn):
    """Decorator: convert LinearError into a structured tool response.

    Uses functools.wraps so FastMCP's inspect.signature() follows __wrapped__
    back to the real function — without it, the wrapper's (*args, **kwargs)
    signature leaks into the generated tool schema, causing callers to send
    `args`/`kwargs` parameters that the real function doesn't accept.
    """
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LinearError as e:
            return {"error": str(e)}
    return wrapped


# ---------- tools ------------------------------------------------------------

@mcp.tool()
@_wrap
def create_issue(
    title: str,
    description: str | None = None,
    team: str | None = None,
    parent: str | None = None,
    project: str | None = None,
    labels: list[str] | None = None,
    depends_on: list[str] | None = None,
    priority: int | None = None,
    assignee: str | None = None,
) -> dict[str, Any]:
    """Create a Linear issue.

    Args:
      title: issue title (required).
      description: markdown body.
      team: team key like 'MAT' or UUID; defaults to LINEAR_DEFAULT_TEAM_KEY.
      parent: parent issue ref ('MAT-100' or UUID) — use for epic child issues.
      project: project UUID (Linear projects don't have short keys).
      labels: list of label names (resolved per team).
      depends_on: list of issue refs that must complete before this one starts.
                  Each becomes a 'blocks' relation (the listed issue blocks
                  this new one). Use this in plans so list_ready_issues knows
                  what's actually pickup-able.
      priority: 0 (none), 1 (urgent), 2 (high), 3 (medium), 4 (low).
      assignee: 'me', a Linear UUID, email, or displayName.

    Returns the new issue summary including id/identifier/url.
    """
    team_uuid = _resolve_team_uuid(team)
    inp: dict[str, Any] = {"teamId": team_uuid, "title": title}
    if description is not None:
        inp["description"] = description
    if parent is not None:
        inp["parentId"] = _resolve_issue_uuid(parent)
    if project is not None:
        inp["projectId"] = project
    if labels:
        inp["labelIds"] = _resolve_label_ids(team_uuid, labels)
    if priority is not None:
        inp["priority"] = priority
    if assignee is not None:
        inp["assigneeId"] = _resolve_user_uuid(assignee)

    data = _client().query(
        f"""mutation($i:IssueCreateInput!){{
          issueCreate(input:$i){{
            success
            issue{{{_ISSUE_CORE_FIELDS}}}
          }}
        }}""",
        {"i": inp},
    )
    result = data.get("issueCreate") or {}
    if not result.get("success"):
        raise LinearError("issueCreate returned success=false")
    issue = result.get("issue") or {}
    summary = _issue_summary(issue)

    # Layer on dependencies after creation (Linear has no single-shot
    # create-with-relations form). Each blocker.id -> issue.id with type=blocks.
    relations: list[dict] = []
    for blocker_ref in depends_on or []:
        blocker_uuid = _resolve_issue_uuid(blocker_ref)
        rel = _client().query(
            """mutation($a:String!,$b:String!){
              issueRelationCreate(input:{issueId:$a, relatedIssueId:$b, type:blocks}){
                success
                issueRelation{id}
              }
            }""",
            {"a": blocker_uuid, "b": issue["id"]},
        )
        rc = rel.get("issueRelationCreate") or {}
        relations.append({
            "blocker": blocker_ref,
            "relation_id": (rc.get("issueRelation") or {}).get("id"),
            "success": bool(rc.get("success")),
        })
    if relations:
        summary["blockers_added"] = relations
    return summary


@mcp.tool()
@_wrap
def link_blocks(blocker: str, blocked: str) -> dict[str, Any]:
    """Add a 'blocker blocks blocked' relation between two existing issues.
    Both args accept issue identifiers ('MAT-12') or UUIDs.
    """
    a = _resolve_issue_uuid(blocker)
    b = _resolve_issue_uuid(blocked)
    data = _client().query(
        """mutation($a:String!,$b:String!){
          issueRelationCreate(input:{issueId:$a, relatedIssueId:$b, type:blocks}){
            success
            issueRelation{id type}
          }
        }""",
        {"a": a, "b": b},
    )
    rc = data.get("issueRelationCreate") or {}
    return {
        "success": bool(rc.get("success")),
        "relation_id": (rc.get("issueRelation") or {}).get("id"),
        "blocker": blocker,
        "blocked": blocked,
    }


@mcp.tool()
@_wrap
def unlink_blocks(blocker: str, blocked: str) -> dict[str, Any]:
    """Remove an existing 'blocker blocks blocked' relation between two issues."""
    a = _resolve_issue_uuid(blocker)
    b = _resolve_issue_uuid(blocked)
    # Find the relation id by walking blocker's relations.
    data = _client().query(
        """query($a:String!){
          issue(id:$a){
            relations(filter:{type:{eq:"blocks"}}){
              nodes{id relatedIssue{id}}
            }
          }
        }""",
        {"a": a},
    )
    rels = (((data.get("issue") or {}).get("relations") or {}).get("nodes") or [])
    target = next((r for r in rels if (r.get("relatedIssue") or {}).get("id") == b), None)
    if not target:
        raise LinearError(f"no blocks relation found from {blocker} to {blocked}")
    deleted = _client().query(
        """mutation($id:String!){
          issueRelationDelete(id:$id){success}
        }""",
        {"id": target["id"]},
    )
    return {
        "success": bool((deleted.get("issueRelationDelete") or {}).get("success")),
        "relation_id": target["id"],
    }


@mcp.tool()
@_wrap
def get_issue(ref: str) -> dict[str, Any]:
    """Return full detail for an issue including parent, state, and current blockers."""
    data = _client().query(
        f"""query($r:String!){{
          issue(id:$r){{
            {_ISSUE_WITH_BLOCKERS}
            description
          }}
        }}""",
        {"r": ref},
    )
    issue = data.get("issue")
    if not issue:
        raise LinearError(f"no issue with ref '{ref}'")
    summary = _issue_summary(issue)
    summary["description"] = issue.get("description")
    blockers = []
    for rel in ((issue.get("inverseRelations") or {}).get("nodes") or []):
        b = rel.get("issue") or {}
        b_state = b.get("state") or {}
        blockers.append({
            "id": b.get("id"),
            "identifier": b.get("identifier"),
            "title": b.get("title"),
            "state": {"name": b_state.get("name"), "type": b_state.get("type")},
            "resolved": (b_state.get("type") or "") in {"completed", "canceled"},
        })
    summary["blockers"] = blockers
    summary["blocked"] = any(not b["resolved"] for b in blockers)
    return summary


def _build_issue_filter(
    parent: str | None,
    project: str | None,
    team_uuid: str | None,
    state_types: list[str] | None,
    labels: list[str] | None,
) -> dict[str, Any]:
    f: dict[str, Any] = {}
    if parent is not None:
        f["parent"] = {"id": {"eq": _resolve_issue_uuid(parent)}}
    if project is not None:
        f["project"] = {"id": {"eq": project}}
    if team_uuid is not None:
        f["team"] = {"id": {"eq": team_uuid}}
    if state_types:
        f["state"] = {"type": {"in": list(state_types)}}
    if labels:
        # Filter by label name(s) — Linear supports {labels: {name: {in: [...]}}}
        # for "any of these labels".
        f["labels"] = {"name": {"in": list(labels)}}
    return f


@mcp.tool()
@_wrap
def list_issues(
    parent: str | None = None,
    project: str | None = None,
    team: str | None = None,
    state_types: list[str] | None = None,
    labels: list[str] | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """Return issues matching the filter. No blocker check — includes blocked
    issues. Use list_ready_issues if you want only pickup-able work.

    state_types: any of 'triage', 'backlog', 'unstarted', 'started',
                 'completed', 'canceled'. Default: all states.
    """
    team_uuid = _resolve_team_uuid(team) if (team or DEFAULT_TEAM_KEY) else None
    f = _build_issue_filter(parent, project, team_uuid, state_types, labels)
    data = _client().query(
        f"""query($f:IssueFilter, $n:Int!){{
          issues(filter:$f, first:$n){{
            nodes{{{_ISSUE_CORE_FIELDS}}}
          }}
        }}""",
        {"f": f, "n": max(1, min(max_results, 100))},
    )
    nodes = ((data.get("issues") or {}).get("nodes") or [])
    return {"count": len(nodes), "issues": [_issue_summary(n) for n in nodes]}


@mcp.tool()
@_wrap
def list_ready_issues(
    parent: str | None = None,
    project: str | None = None,
    team: str | None = None,
    state_types: list[str] | None = None,
    labels: list[str] | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """Return issues matching the filter AND with no open blockers.

    'Open blocker' = an inverseRelation of type=blocks whose blocker's state
    type is not in {completed, canceled}. Default state_types filter is
    ['backlog', 'unstarted'] — the natural 'pickup-able' set. Pass an explicit
    state_types list to widen (e.g. include 'started' to re-pick stalled work).

    This is the tool ao workers should call to claim work in parallel without
    stepping on each other — combine with claim_issue to flip state atomically.
    """
    team_uuid = _resolve_team_uuid(team) if (team or DEFAULT_TEAM_KEY) else None
    types = state_types if state_types is not None else ["backlog", "unstarted"]
    f = _build_issue_filter(parent, project, team_uuid, types, labels)
    data = _client().query(
        f"""query($f:IssueFilter, $n:Int!){{
          issues(filter:$f, first:$n){{
            nodes{{{_ISSUE_WITH_BLOCKERS}}}
          }}
        }}""",
        {"f": f, "n": max(1, min(max_results, 100))},
    )
    nodes = ((data.get("issues") or {}).get("nodes") or [])
    ready: list[dict] = []
    blocked: list[dict] = []
    for n in nodes:
        rels = ((n.get("inverseRelations") or {}).get("nodes") or [])
        open_blockers = [
            r for r in rels
            if ((r.get("issue") or {}).get("state") or {}).get("type") not in {"completed", "canceled"}
        ]
        summary = _issue_summary(n)
        if open_blockers:
            summary["open_blockers"] = [
                {
                    "id": (r.get("issue") or {}).get("id"),
                    "identifier": (r.get("issue") or {}).get("identifier"),
                    "title": (r.get("issue") or {}).get("title"),
                    "state": ((r.get("issue") or {}).get("state") or {}).get("type"),
                }
                for r in open_blockers
            ]
            blocked.append(summary)
        else:
            ready.append(summary)
    return {
        "ready_count": len(ready),
        "blocked_count": len(blocked),
        "ready": ready[:max_results],
        # Surface a hint about blocked work so the caller can reason about
        # what's coming next without a second query.
        "blocked_preview": blocked[:5],
    }


@mcp.tool()
@_wrap
def update_issue(
    ref: str,
    state: str | None = None,
    assignee: str | None = None,
    labels_add: list[str] | None = None,
    labels_remove: list[str] | None = None,
    priority: int | None = None,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Update an issue's state / assignee / labels / priority / title / description.

    `state` accepts a workflow state name ('In Progress') or type ('started').
    `assignee` accepts 'me', a UUID, email, or displayName. Pass None to
    leave a field untouched. For label adds/removes the new label set is
    computed server-side from the current set.
    """
    issue_uuid = _resolve_issue_uuid(ref)
    inp: dict[str, Any] = {}
    if title is not None:
        inp["title"] = title
    if description is not None:
        inp["description"] = description
    if priority is not None:
        inp["priority"] = priority
    if state is not None or labels_add or labels_remove:
        # Need the issue's team + current labels to do these resolutions.
        cur = _client().query(
            """query($r:String!){issue(id:$r){
              team{id}
              labels{nodes{id name}}
            }}""",
            {"r": ref},
        )
        cur_issue = cur.get("issue") or {}
        team_uuid = (cur_issue.get("team") or {}).get("id")
        if state is not None:
            inp["stateId"] = _resolve_state_id(team_uuid, state)
        if labels_add or labels_remove:
            current_ids = {l["id"] for l in ((cur_issue.get("labels") or {}).get("nodes") or [])}
            if labels_add:
                current_ids.update(_resolve_label_ids(team_uuid, labels_add))
            if labels_remove:
                for lid in _resolve_label_ids(team_uuid, labels_remove):
                    current_ids.discard(lid)
            inp["labelIds"] = sorted(current_ids)
    if assignee is not None:
        inp["assigneeId"] = _resolve_user_uuid(assignee)
    if not inp:
        raise LinearError("no fields to update")

    data = _client().query(
        f"""mutation($id:String!,$i:IssueUpdateInput!){{
          issueUpdate(id:$id, input:$i){{
            success
            issue{{{_ISSUE_CORE_FIELDS}}}
          }}
        }}""",
        {"id": issue_uuid, "i": inp},
    )
    result = data.get("issueUpdate") or {}
    if not result.get("success"):
        raise LinearError("issueUpdate returned success=false")
    return _issue_summary(result.get("issue") or {})


@mcp.tool()
@_wrap
def claim_issue(ref: str, assignee: str | None = "me") -> dict[str, Any]:
    """Claim an issue for a worker: flip its state to a 'started' type state
    and (by default) assign to 'me'. Idempotent for the same worker; if
    another worker has already claimed, the second caller still succeeds
    silently — re-read with get_issue to detect contention.

    Pass assignee=None to skip assignment (state-only flip).
    """
    return update_issue(ref=ref, state="started", assignee=assignee)


@mcp.tool()
@_wrap
def add_comment(ref: str, body: str) -> dict[str, Any]:
    """Post a markdown comment on an issue."""
    issue_uuid = _resolve_issue_uuid(ref)
    data = _client().query(
        """mutation($id:String!,$b:String!){
          commentCreate(input:{issueId:$id, body:$b}){
            success
            comment{id url body}
          }
        }""",
        {"id": issue_uuid, "b": body},
    )
    result = data.get("commentCreate") or {}
    if not result.get("success"):
        raise LinearError("commentCreate returned success=false")
    c = result.get("comment") or {}
    return {"id": c.get("id"), "url": c.get("url")}


@mcp.tool()
@_wrap
def list_workflow_states(team: str | None = None) -> dict[str, Any]:
    """Enumerate a team's workflow states so callers know what names/types exist.
    Useful before update_issue(state=...) or to pick a custom 'started' state.
    """
    team_uuid = _resolve_team_uuid(team)
    states = _team_states(team_uuid)
    return {
        "team": team or DEFAULT_TEAM_KEY,
        "states": [
            {"id": s.get("id"), "name": s.get("name"), "type": s.get("type"),
             "position": s.get("position")}
            for s in sorted(states, key=lambda s: s.get("position") or 0)
        ],
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
