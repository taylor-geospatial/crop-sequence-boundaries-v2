"""Shared object-instance metric helpers (mirrors ftw-planet's metrics.py)."""


def object_metrics_from_counts(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Return object precision, recall, and F1 for matched-instance counts."""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1
