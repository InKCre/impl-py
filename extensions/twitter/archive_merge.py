"""Twitter-owned sparse fact merge, including per-counter observation times."""

from __future__ import annotations

import copy
import datetime

from pydantic import JsonValue


def _merge_content(target: dict[str, JsonValue], incoming: dict[str, JsonValue]) -> None:
  for key, value in incoming.items():
    current = target.get(key)
    if isinstance(value, dict) and isinstance(current, dict):
      _merge_content(current, value)
    else:
      target[key] = copy.deepcopy(value)


def _observation_time(value: str) -> datetime.datetime:
  parsed = datetime.datetime.fromisoformat(value)
  # Twitter compares observations at millisecond resolution; retain finer source precision.
  return parsed.replace(microsecond=parsed.microsecond // 1000 * 1000)


def merge_archive_record(
  existing: dict[str, JsonValue], incoming: dict[str, JsonValue]
) -> dict[str, JsonValue]:
  """Return a detached merge; delayed counter observations cannot replace newer ones."""
  result = copy.deepcopy(existing)
  _merge_content(
    result,
    {
      key: value
      for key, value in incoming.items()
      if key not in {"counts", "countsObservedAt"}
    },
  )
  counts = incoming.get("counts")
  if not isinstance(counts, dict):
    return result

  current_counts = result.get("counts")
  if not isinstance(current_counts, dict):
    current_counts = {}
  current_times = result.get("countsObservedAt")
  if not isinstance(current_times, dict):
    current_times = {}
  incoming_times = incoming.get("countsObservedAt")
  if not isinstance(incoming_times, dict):
    incoming_times = {}

  for key, value in counts.items():
    observed_at = incoming_times.get(key)
    current_time = current_times.get(key)
    if (
      isinstance(observed_at, str)
      and isinstance(current_time, str)
      and _observation_time(observed_at) < _observation_time(current_time)
    ):
      continue
    count_changed = key not in current_counts or current_counts[key] != value
    if count_changed:
      current_counts[key] = copy.deepcopy(value)
    if isinstance(observed_at, str):
      current_times[key] = observed_at
    elif count_changed:
      current_times.pop(key, None)

  if current_counts:
    result["counts"] = current_counts
  if current_times:
    result["countsObservedAt"] = current_times
  else:
    result.pop("countsObservedAt", None)
  return result


__all__ = ["merge_archive_record"]
