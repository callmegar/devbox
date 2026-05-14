"""Discovery extractor: Terraform/OpenTofu stacks + inter-stack dependencies.

Each direct subdir of the terraform root containing .tf files is a stack.
Inter-stack edges come from `terraform_remote_state` data sources that
reference another stack by name. HCL is parsed with regex — good enough for
the common block shapes; complex cases are best-effort.
"""

from __future__ import annotations

import re

from ..model import KIND_TERRAFORM_STACK

_REMOTE_STATE_RE = re.compile(
    r'data\s+"terraform_remote_state"\s+"[^"]+"\s*\{(.*?)\}', re.S
)
_MODULE_RE = re.compile(r'^\s*module\s+"', re.M)
_RESOURCE_RE = re.compile(r'^\s*resource\s+"', re.M)


def extract(ctx) -> dict[str, dict]:
    layout = ctx.layout
    if not layout.terraform_root:
        return {}

    tf_root = layout.terraform_root
    stacks = [
        d for d in sorted(tf_root.iterdir())
        if d.is_dir() and any(d.glob("*.tf"))
    ]
    stack_names = {d.name for d in stacks}
    out: dict[str, dict] = {}

    for d in stacks:
        tf_text = ""
        for tf in sorted(d.glob("*.tf")):
            try:
                tf_text += tf.read_text(errors="ignore") + "\n"
            except Exception:
                continue

        deps: set[str] = set()
        for block in _REMOTE_STATE_RE.findall(tf_text):
            for sn in stack_names:
                if sn != d.name and re.search(rf'\b{re.escape(sn)}\b', block):
                    deps.add(f"tf:{sn}")

        out[f"tf:{d.name}"] = {
            "name": f"tf:{d.name}",
            "kind": KIND_TERRAFORM_STACK,
            "path": str(d.relative_to(ctx.repo_path)),
            "depends_on": sorted(deps),
            "loc": tf_text.count("\n"),
            "extra": {
                "modules_used": len(_MODULE_RE.findall(tf_text)),
                "resources_declared": len(_RESOURCE_RE.findall(tf_text)),
                "tf_files": len(list(d.glob("*.tf"))),
            },
        }
    return out
