import os
import threading
import time

import infinienv.assets.generator_openai as generator_openai
from infinienv.assets.resolver import resolve_assets, scene_asset_types
from infinienv.generation.templates import kitchen_delivery
from infinienv.llm.base import ProviderError


def test_scene_asset_types_includes_agent_and_wall():
    scene = kitchen_delivery("kitchen task", seed=1)
    types = scene_asset_types(scene)
    assert "agent" in types
    assert "wall" in types
    assert "table" in types
    assert "can" in types
    assert "sink" in types


def test_resolve_assets_none_mode_returns_no_paths():
    scene = kitchen_delivery("kitchen task", seed=1)
    entries, notes = resolve_assets(scene, "none", "/tmp/unused")
    assert notes == []
    assert all(e.source == "none" and e.path is None for e in entries.values())


def test_resolve_assets_local_mode_uses_checked_in_placeholders(tmp_path):
    scene = kitchen_delivery("kitchen task", seed=1)
    entries, notes = resolve_assets(scene, "local", str(tmp_path))
    for t, entry in entries.items():
        assert entry.source == "local", (t, entry)
        assert os.path.exists(entry.path)


def _write_fake_sprite(cache_dir: str, object_type: str) -> str:
    from PIL import Image

    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{object_type}.png")
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(path)
    return path


def test_resolve_assets_generated_mode_calls_generate_sprite_for_each_missing_type(tmp_path, monkeypatch):
    scene = kitchen_delivery("kitchen task", seed=1)
    calls = []

    def fake_generate(object_type, cache_dir, **kwargs):
        calls.append(object_type)
        return _write_fake_sprite(cache_dir, object_type)

    monkeypatch.setattr(generator_openai, "generate_sprite", fake_generate)
    entries, notes = resolve_assets(scene, "generated", str(tmp_path))

    assert notes == []
    expected_types = set(scene_asset_types(scene))
    assert set(calls) == expected_types
    for t, entry in entries.items():
        assert entry.source == "generated", (t, entry)
        assert os.path.exists(entry.path)


def test_resolve_assets_skips_generation_for_a_cache_hit(tmp_path, monkeypatch):
    scene = kitchen_delivery("kitchen task", seed=1)
    cache_dir = str(tmp_path)
    _write_fake_sprite(cache_dir, "agent")  # pre-warm the cache for one type

    calls = []

    def fake_generate(object_type, cache_dir, **kwargs):
        calls.append(object_type)
        return _write_fake_sprite(cache_dir, object_type)

    monkeypatch.setattr(generator_openai, "generate_sprite", fake_generate)
    entries, notes = resolve_assets(scene, "generated", cache_dir)

    assert "agent" not in calls  # cache hit -- never regenerated
    assert entries["agent"].note == "cache hit"
    assert entries["agent"].source == "generated"


def test_resolve_assets_generates_missing_types_concurrently(tmp_path, monkeypatch):
    # Regression coverage for the sequential-generation performance bug: each call sleeps
    # briefly and records how many other calls were in flight at the same time. If generation
    # were still serialized, peak concurrency would be 1 no matter how many types are pending.
    scene = kitchen_delivery("kitchen task", seed=1)  # 5 distinct types: agent/wall/table/can/sink
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def fake_generate(object_type, cache_dir, **kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return _write_fake_sprite(cache_dir, object_type)

    monkeypatch.setattr(generator_openai, "generate_sprite", fake_generate)
    start = time.perf_counter()
    entries, notes = resolve_assets(scene, "generated", str(tmp_path))
    elapsed = time.perf_counter() - start

    assert peak > 1, "sprite generation ran fully sequentially -- expected overlap"
    # 5 calls x 0.05s each: sequential would take ~0.25s; concurrent (bounded pool) well under.
    assert elapsed < 0.2, f"took {elapsed:.3f}s -- looks sequential, not concurrent"
    assert all(e.source == "generated" for e in entries.values())


def test_resolve_assets_concurrency_is_bounded_by_env_override(tmp_path, monkeypatch):
    scene = kitchen_delivery("kitchen task", seed=1)
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def fake_generate(object_type, cache_dir, **kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return _write_fake_sprite(cache_dir, object_type)

    monkeypatch.setattr(generator_openai, "generate_sprite", fake_generate)
    monkeypatch.setenv("INFINIENV_ASSET_CONCURRENCY", "1")
    resolve_assets(scene, "generated", str(tmp_path))

    assert peak == 1, f"expected the concurrency cap of 1 to be respected, saw peak={peak}"


def test_resolve_assets_generated_mode_records_a_note_and_no_asset_on_failure(tmp_path, monkeypatch):
    scene = kitchen_delivery("kitchen task", seed=1)

    def fake_generate(object_type, cache_dir, **kwargs):
        if object_type == "table":
            raise ProviderError("image generation failed for 'table': boom")
        return _write_fake_sprite(cache_dir, object_type)

    monkeypatch.setattr(generator_openai, "generate_sprite", fake_generate)
    entries, notes = resolve_assets(scene, "generated", str(tmp_path))

    assert entries["table"].source == "none"
    assert entries["table"].note == "generation failed and fallback not requested"
    assert any("table" in n for n in notes)
    # other types still resolved -- one failure doesn't take down the rest
    assert entries["agent"].source == "generated"


def test_resolve_assets_auto_mode_falls_back_to_local_on_generation_failure(tmp_path, monkeypatch):
    scene = kitchen_delivery("kitchen task", seed=1)

    def fake_generate(object_type, cache_dir, **kwargs):
        raise ProviderError(f"image generation failed for {object_type!r}: boom")

    monkeypatch.setattr(generator_openai, "generate_sprite", fake_generate)
    entries, notes = resolve_assets(scene, "auto", str(tmp_path))

    for t, entry in entries.items():
        assert entry.source == "local", (t, entry)
        assert entry.note == "fallback: generated unavailable"
