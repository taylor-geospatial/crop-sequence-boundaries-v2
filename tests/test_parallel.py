"""Tests for csb.utils — parallelism helpers."""

from csb.utils import parallel_map, parallel_starmap, worker_count


def _square(x: int) -> int:
    return x * x


def _add(a: int, b: int) -> int:
    return a + b


def test_worker_count() -> None:
    n = worker_count(0.5)
    assert n >= 1


def test_worker_count_full() -> None:
    n = worker_count(1.0)
    import multiprocessing

    assert n == multiprocessing.cpu_count()


def test_worker_count_zero() -> None:
    n = worker_count(0.0)
    assert n >= 1


def test_parallel_map() -> None:
    results = parallel_map(_square, [1, 2, 3, 4], max_workers=2)
    assert results == [1, 4, 9, 16]


def test_parallel_map_empty() -> None:
    results = parallel_map(_square, [], max_workers=1)
    assert results == []


def test_parallel_starmap() -> None:
    results = parallel_starmap(_add, [(1, 2), (3, 4), (5, 6)], max_workers=2)
    assert results == [3, 7, 11]
