"""Shared mechanics for concise Resolver-owned Block labels."""

LABEL_IDENTIFIER_LIMIT = 96


def normalize_label_identifier(
  value: str | None,
  *,
  first_line: bool = False,
) -> str | None:
  """Normalize whitespace and bound one optional human-readable identifier."""
  if value is None:
    return None
  candidates = value.splitlines() if first_line else (value,)
  normalized = next((" ".join(item.split()) for item in candidates if item.strip()), "")
  if not normalized:
    return None
  if len(normalized) > LABEL_IDENTIFIER_LIMIT:
    return f"{normalized[:LABEL_IDENTIFIER_LIMIT]}…"
  return normalized


def format_label(
  kind: str,
  identifier: str | None = None,
  *,
  first_line: bool = False,
) -> str:
  """Format one stable resolver-qualified label without exposing exact IDs."""
  normalized = normalize_label_identifier(identifier, first_line=first_line)
  return kind if normalized is None else f"{kind} <{normalized}>"
