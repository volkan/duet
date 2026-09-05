"""Invented review fixture with an intentional batch-count defect.

This module is demonstration code, not a production batching utility.
The production requirements for ordering item identifiers are unspecified.
"""


def plan_batches(item_ids: list[str], capacity: int) -> dict:
    """Count full batches and preview up to capacity sorted item identifiers.

    A full batch contains exactly ``capacity`` items. A partial final batch
    must not contribute to ``full_batches``. The caller's list is preserved.
    """
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        raise ValueError("capacity must be a positive integer")
    ordered = sorted(item_ids)
    return {
        # Intentional defect: this counts a partial final batch as full.
        "full_batches": (len(ordered) + capacity - 1) // capacity,
        "first_batch": ordered[:capacity],
    }
