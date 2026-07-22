"""Tile sharding for multi-node arrays must partition tiles exactly.

A SLURM job array runs one shard per task; together the shards must cover every
tile once and only once, or the CONUS output silently loses or double-counts
tiles.
"""

import pytest

from csb.polygonize import _shard_tiles, _tile_windows


def test_shards_partition_exactly() -> None:
    tiles = _tile_windows(30000, 20000, 5000)  # 6 x 4 = 24 tiles
    names = {n for n, _ in tiles}

    for num_shards in (1, 2, 3, 5, 7, 24):
        seen: list[str] = []
        for idx in range(num_shards):
            shard = _shard_tiles(tiles, num_shards, idx)
            seen.extend(n for n, _ in shard)
        # Exact cover: every tile exactly once across all shards.
        assert sorted(seen) == sorted(names)
        assert len(seen) == len(set(seen))


def test_shards_balanced() -> None:
    tiles = _tile_windows(50000, 50000, 5000)  # 100 tiles
    sizes = [len(_shard_tiles(tiles, 8, i)) for i in range(8)]
    assert max(sizes) - min(sizes) <= 1  # round-robin => near-equal


def test_shard_index_out_of_range() -> None:
    tiles = _tile_windows(10000, 10000, 5000)
    with pytest.raises(ValueError, match="out of range"):
        _shard_tiles(tiles, 4, 4)
