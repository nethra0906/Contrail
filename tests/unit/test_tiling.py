from __future__ import annotations

from services.ingest.sources.tiling import DEFAULT_HUB_ICAOS, core_hub_tiles, grid_tiles, hub_tiles


def test_hub_tiles_returns_one_per_known_hub():
    tiles = hub_tiles()
    assert len(tiles) == len(DEFAULT_HUB_ICAOS)


def test_core_hub_tiles_is_a_strict_subset_of_hub_tiles():
    core = core_hub_tiles()
    full = hub_tiles()
    assert 0 < len(core) < len(full)
    core_coords = {(t.lat, t.lon) for t in core}
    full_coords = {(t.lat, t.lon) for t in full}
    assert core_coords <= full_coords


def test_core_hub_tiles_default_radius_is_positive():
    for t in core_hub_tiles():
        assert t.radius_nm > 0


def test_grid_tiles_covers_a_bbox_with_positive_count():
    bbox = (24.5, 49.5, -125.0, -66.5)
    tiles = grid_tiles(bbox, radius_nm=200.0)
    assert len(tiles) > 0
    for t in tiles:
        assert bbox[0] <= t.lat <= bbox[1] + 5  # allow the last row's overshoot
        assert bbox[2] <= t.lon <= bbox[3] + 5
