"""Purged K-fold cross-validation (P15).

López de Prado — adjacent time-series samples share information because
overlapping labels (e.g., 5-day forward returns on consecutive days
overlap 4 days). Standard k-fold leaks the future into the "past"
training fold; you get gorgeous CV scores that don't hold live.

Purged K-fold:
  1. Split observations into K contiguous folds.
  2. For each test fold: PURGE the training samples whose label window
     overlaps the test fold's time range.
  3. Optionally add an EMBARGO (small buffer) around the test fold to
     handle serial correlation of features.

This module produces the INDEX SPLITS. The user runs their own
evaluator (accuracy, Sharpe, etc.) on each fold and averages.
"""

from __future__ import annotations

from typing import Iterator


def purged_kfold_indices(n_samples: int, k: int = 5,
                         label_window: int = 5, embargo: int = 2,
                         ) -> Iterator[tuple[list[int], list[int]]]:
    """Yield (train_indices, test_indices) tuples for K folds.

    Args:
        n_samples: total observations (chronologically ordered)
        k: number of folds
        label_window: how many bars forward the label uses (5 for 5-day return)
        embargo: additional buffer bars purged around the test set

    Purges training samples whose label horizon overlaps the test range.
    """
    if n_samples < k * (label_window + embargo + 2):
        raise ValueError(f"n_samples ({n_samples}) too small for k={k} folds")
    fold_size = n_samples // k
    for fold in range(k):
        test_start = fold * fold_size
        test_end = test_start + fold_size if fold < k - 1 else n_samples
        # Purge zone: label_window bars BEFORE test_start also see the future
        # of test set through their forward return; embargo widens the buffer
        purge_start = max(0, test_start - label_window - embargo)
        purge_end = min(n_samples, test_end + embargo)
        train = [i for i in range(n_samples)
                 if i < purge_start or i >= purge_end]
        test = list(range(test_start, test_end))
        yield (train, test)


def cross_validate(n_samples: int, evaluator,
                   k: int = 5, label_window: int = 5, embargo: int = 2) -> dict:
    """Run K-fold CV given an evaluator(train_idx, test_idx) → score.

    Returns per-fold + mean + std.
    """
    scores = []
    for i, (train, test) in enumerate(
            purged_kfold_indices(n_samples, k, label_window, embargo)):
        score = evaluator(train, test)
        scores.append({"fold": i, "n_train": len(train), "n_test": len(test),
                        "score": score})
    values = [s["score"] for s in scores if s["score"] is not None]
    mean = sum(values) / len(values) if values else 0.0
    var = sum((v - mean) ** 2 for v in values) / max(len(values), 1)
    return {
        "folds": scores,
        "mean_score": mean,
        "std_score": var ** 0.5,
    }
