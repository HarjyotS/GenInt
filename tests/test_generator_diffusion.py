from PIL import Image

import infinienv.assets.generator_diffusion as generator_diffusion
from infinienv.assets.generator_diffusion import (
    DEFAULT_DIFFUSION_MODEL,
    DIFFUSION_SPRITE_PROMPT_TEMPLATE,
    DIFFUSION_TEXTURE_PROMPT_TEMPLATE,
    generate_sprite,
)
from infinienv.llm.base import ProviderError

import pytest


def _fake_diffusion_image(size=(200, 200)):
    """A plain image with a solid-color square in the middle on a distinct background --
    simulates what a local diffusion pipeline (no native alpha channel) actually returns. No
    mocked torch/diffusers import happens anywhere here -- _run_pipeline is replaced outright."""
    img = Image.new("RGB", size, (240, 240, 240))
    inner = Image.new("RGB", (size[0] // 2, size[1] // 2), (10, 20, 30))
    img.paste(inner, (size[0] // 4, size[1] // 4))
    return img


def _fake_remove_background(img):
    """Simulates rembg's segmentation without needing the real model installed: treats the
    center half of the image as foreground (opaque) and everything else as background
    (transparent) -- mirrors the known layout of _fake_diffusion_image above."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    mask = Image.new("L", (w, h), 0)
    mask.paste(255, (w // 4, h // 4, w // 4 + w // 2, h // 4 + h // 2))
    rgba.putalpha(mask)
    return rgba


def _install_fake_pipeline(monkeypatch, *, image_factory=_fake_diffusion_image):
    calls = []

    def fake_run_pipeline(prompt, *, model_id):
        calls.append({"prompt": prompt, "model_id": model_id})
        return image_factory()

    monkeypatch.setattr(generator_diffusion, "_run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(generator_diffusion, "_remove_background", _fake_remove_background)
    return calls


def _opaque_fraction(img: Image.Image) -> float:
    alpha = img.convert("RGBA").split()[3]
    histogram = alpha.histogram()
    return histogram[255] / (img.width * img.height)


def test_texture_type_skips_background_removal_and_crop(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    path = generate_sprite("wall", str(tmp_path))
    assert "seamless" in calls[0]["prompt"].lower()
    img = Image.open(path).convert("RGBA")
    assert img.size == (64, 64)
    # texture path never runs background removal -- stays fully opaque everywhere.
    assert img.getpixel((0, 0))[3] == 255


def test_discrete_object_type_removes_background_and_crops(tmp_path, monkeypatch):
    _install_fake_pipeline(monkeypatch)
    # source: a 200x200 canvas with a 100x100 "foreground" square centered -> 25% opaque
    # after segmentation (before cropping).
    raw_opaque_fraction = 0.25

    path = generate_sprite("key", str(tmp_path))

    img = Image.open(path).convert("RGBA")
    assert img.size == (64, 64)
    # background removal + crop-to-content should leave the frame mostly opaque now, not the
    # ~25% the raw (uncropped) segmentation mask would show.
    assert _opaque_fraction(img) > raw_opaque_fraction * 2


def test_generate_sprite_caches_to_object_type_named_file(tmp_path, monkeypatch):
    _install_fake_pipeline(monkeypatch)
    path = generate_sprite("hazard", str(tmp_path))
    assert path.endswith("hazard.png")


def test_generate_sprite_defaults_to_sd_turbo_model(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    generate_sprite("key", str(tmp_path))
    assert calls[0]["model_id"] == DEFAULT_DIFFUSION_MODEL


def test_generate_sprite_model_overridable_via_env(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    monkeypatch.setenv("INFINIENV_DIFFUSION_MODEL", "stabilityai/sdxl-turbo")
    generate_sprite("key", str(tmp_path))
    assert calls[0]["model_id"] == "stabilityai/sdxl-turbo"


def test_generate_sprite_model_kwarg_overrides_env(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    monkeypatch.setenv("INFINIENV_DIFFUSION_MODEL", "stabilityai/sdxl-turbo")
    generate_sprite("key", str(tmp_path), model="some/other-model")
    assert calls[0]["model_id"] == "some/other-model"


def test_generate_sprite_quality_kwarg_accepted_but_has_no_effect(tmp_path, monkeypatch):
    # No quality-tier concept exists for a 1-4-step local turbo model -- quality is accepted
    # only for interface parity with the OpenAI backend and must not raise or change output.
    _install_fake_pipeline(monkeypatch)
    path = generate_sprite("key", str(tmp_path), quality="high")
    assert Image.open(path).size == (64, 64)


def test_generate_sprite_description_override_replaces_default(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    generate_sprite("agent", str(tmp_path), description="a green-clothed Italian rescuer with a mustache")
    assert "small friendly robot" not in calls[0]["prompt"]
    assert "green-clothed Italian rescuer" in calls[0]["prompt"]


def test_generate_sprite_without_description_uses_object_descriptions_default(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    generate_sprite("agent", str(tmp_path))
    assert "small friendly robot" in calls[0]["prompt"]


def test_generate_sprite_missing_extra_raises_clear_provider_error(tmp_path, monkeypatch):
    def fake_run_pipeline(prompt, *, model_id):
        raise ImportError("No module named 'diffusers'")

    monkeypatch.setattr(generator_diffusion, "_run_pipeline", fake_run_pipeline)
    with pytest.raises(ProviderError, match=r"pip install infinienv\[diffusion\]"):
        generate_sprite("key", str(tmp_path))


def test_generate_sprite_missing_rembg_raises_clear_provider_error(tmp_path, monkeypatch):
    # ImportError from the background-removal step (rembg missing) must be caught the same way
    # as a missing torch/diffusers -- both are covered by the single `diffusion` extra.
    monkeypatch.setattr(generator_diffusion, "_run_pipeline", lambda prompt, *, model_id: _fake_diffusion_image())

    def fake_remove_background(img):
        raise ImportError("No module named 'rembg'")

    monkeypatch.setattr(generator_diffusion, "_remove_background", fake_remove_background)
    with pytest.raises(ProviderError, match=r"pip install infinienv\[diffusion\]"):
        generate_sprite("key", str(tmp_path))


def test_generate_sprite_wraps_pipeline_failure_in_provider_error(tmp_path, monkeypatch):
    def fake_run_pipeline(prompt, *, model_id):
        raise RuntimeError("out of memory")

    monkeypatch.setattr(generator_diffusion, "_run_pipeline", fake_run_pipeline)
    with pytest.raises(ProviderError, match="out of memory"):
        generate_sprite("key", str(tmp_path))


def test_sprite_template_puts_load_bearing_instructions_before_the_description():
    # Regression test for a real, live-caught bug: SD-Turbo's CLIP text encoder hard-truncates
    # any prompt past 77 tokens, and a long scene-derived description silently truncated away
    # the trailing "isolated object... plain background" instructions -- the model then drew an
    # entire scene instead of one object, which background removal had nothing to segment. The
    # fixed instructions must come before "{desc}" so truncation only ever drops description
    # text, never the formatting instructions the rest of the pipeline depends on.
    for template in (DIFFUSION_SPRITE_PROMPT_TEMPLATE, DIFFUSION_TEXTURE_PROMPT_TEMPLATE):
        desc_index = template.index("{desc}")
        assert desc_index > len(template) - len("{desc}") - 20, (
            "the description placeholder must be at (or very near) the end of the template, "
            "so CLIP's truncation only ever drops description text"
        )


def test_generate_sprite_with_a_long_description_keeps_style_instructions_intact(tmp_path, monkeypatch):
    calls = _install_fake_pipeline(monkeypatch)
    long_description = "A " + "very " * 60 + "long player character description."
    generate_sprite("agent", str(tmp_path), description=long_description)
    prompt = calls[0]["prompt"]
    assert prompt.startswith("Retro 16-bit blocky pixel art game sprite")
    assert "isolated object" in prompt.split("Subject:")[0]
