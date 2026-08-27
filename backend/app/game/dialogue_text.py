"""Player-facing dialogue formatting for internal structured references."""
from __future__ import annotations

import re
from collections.abc import Mapping


def render_entity_references(text: str, labels: Mapping[str, str]) -> str:
    """Remove redundant ID annotations and name any remaining entity references."""
    rendered = text
    for identifier in sorted(labels, key=len, reverse=True):
        if not identifier:
            continue
        token = re.escape(identifier).replace("_", r"\\?_")
        code_token = rf"`?\s*{token}\s*`?"
        annotation = rf"\s*(?:(?:id|npc_id|evidence_id)\s*[:=]\s*)?{code_token}\s*"
        annotated = rf"[ \t]*(?:\({annotation}\)|\[{annotation}\]|（{annotation}）)"
        rendered = re.sub(annotated, "", rendered, flags=re.IGNORECASE)
        rendered = re.sub(
            rf"(?<![A-Za-z0-9_])`?{token}`?(?![A-Za-z0-9_])",
            lambda match: labels[identifier],
            rendered,
            flags=re.IGNORECASE,
        )
    return rendered.strip()
