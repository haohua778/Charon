"""Deterministic, one-to-one source matching shared by aggregation and metrics."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_CLAIM_ID = re.compile(r'^p([1-9]\d*)\.s([1-9]\d*)(?:-s([1-9]\d*))?$')


def _target_id(item: Any) -> str:
    target = getattr(item, 'target', None)
    return target.target_id if target is not None else item.target_id


def _claim_range(target_id: str) -> tuple[int, int, int] | None:
    matched = _CLAIM_ID.fullmatch(target_id)
    if not matched:
        return None
    paragraph, first, last = matched.groups()
    return int(paragraph), int(first), int(last or first)


def position_key(item: Any) -> tuple[int, int, str, str]:
    """Order claim ranges by paragraph and first sentence, then check ID.

    Section/document items match only identical IDs, so their relative position
    cannot alter matching. Their offsets provide a useful stable fallback.
    """
    target_id = _target_id(item)
    span = _claim_range(target_id)
    if span:
        return span[0], span[1], item.check_id, target_id
    target = getattr(item, 'target', None)
    return getattr(target, 'start', 0), 0, item.check_id, target_id


def same_issue(left: Any, right: Any) -> bool:
    if left.check_id != right.check_id:
        return False
    left_id, right_id = _target_id(left), _target_id(right)
    left_span, right_span = _claim_range(left_id), _claim_range(right_id)
    if left_span and right_span:
        return (left_span[0] == right_span[0]
                and max(left_span[1], right_span[1]) <= min(left_span[2], right_span[2]))
    return left_span is None and right_span is None and left_id == right_id


def match_items(left: Sequence[Any], right: Sequence[Any]) -> list[tuple[int, int]]:
    """Return original list indices for greedily matched, ordered objects.

    Every object participates in at most one match: a broad claim cannot count
    as two answer hits. No fuzzy text or model-dependent similarity is used.
    """
    remaining = sorted(range(len(right)), key=lambda index: position_key(right[index]))
    matches: list[tuple[int, int]] = []
    for left_index in sorted(range(len(left)), key=lambda index: position_key(left[index])):
        for right_index in remaining:
            if same_issue(left[left_index], right[right_index]):
                matches.append((left_index, right_index))
                remaining.remove(right_index)
                break
    return matches
