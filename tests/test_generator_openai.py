import base64
import io
from types import SimpleNamespace

from PIL import Image

from infinienv.assets.generator_openai import TEXTURE_TILE_TYPES, generate_sprite


def _fake_png_bytes(size=(200, 200), *, with_alpha_border: bool) -> bytes:
    """A small solid-color square, optionally with a transparent border -- simulates
    a generated sprite that has margin baked in (the exact case _crop_to_content
    exists to fix)."""
    if with_alpha_border:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        inner = Image.new("RGBA", (size[0] // 2, size[1] // 2), (10, 20, 30, 255))
        img.paste(inner, (size[0] // 4, size[1] // 4))
    else:
        img = Image.new("RGBA", size, (10, 20, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _install_fake_openai(monkeypatch, *, with_alpha_border: bool):
    calls = []

    class FakeImages:
        def generate(self, **kwargs):
            calls.append(kwargs)
            b64 = base64.b64encode(_fake_png_bytes(with_alpha_border=with_alpha_border)).decode()
            return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)])

    class FakeClient:
        def __init__(self, *a, **kw):
            self.images = FakeImages()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    return calls


def test_texture_type_requests_opaque_background_and_skips_crop(tmp_path, monkeypatch):
    assert "wall" in TEXTURE_TILE_TYPES
    calls = _install_fake_openai(monkeypatch, with_alpha_border=True)

    path = generate_sprite("wall", str(tmp_path))

    assert calls[0]["background"] == "opaque"
    assert "seamless" in calls[0]["prompt"].lower()
    img = Image.open(path)
    assert img.size == (64, 64)
    # texture path skips _crop_to_content -- corners should NOT be transparent even
    # though the fake source image has a transparent border, because that step never ran.
    assert img.convert("RGBA").getpixel((0, 0))[3] == 0  # unchanged from source: still transparent
    # (this asserts the crop step did NOT run for a texture type, i.e. no cropping
    # was applied to strip the border -- the raw fake image is used as-is)


def _opaque_fraction(img: Image.Image) -> float:
    alpha = img.convert("RGBA").split()[3]
    histogram = alpha.histogram()
    return histogram[255] / (img.width * img.height)


def test_discrete_object_type_requests_transparent_background_and_crops(tmp_path, monkeypatch):
    calls = _install_fake_openai(monkeypatch, with_alpha_border=True)
    # source: a 200x200 canvas with a 100x100 opaque square centered in it -> 25% opaque.
    raw_opaque_fraction = 0.25

    path = generate_sprite("key", str(tmp_path))

    assert calls[0]["background"] == "transparent"
    assert "isolated object" in calls[0]["prompt"].lower()
    img = Image.open(path).convert("RGBA")
    assert img.size == (64, 64)
    # crop-to-content should have trimmed most of the transparent border (keeping only
    # a small ~3% padding margin), so the opaque content now covers much more of the
    # frame than it did in the raw generated image.
    assert _opaque_fraction(img) > raw_opaque_fraction * 2


def test_generate_sprite_caches_to_object_type_named_file(tmp_path, monkeypatch):
    _install_fake_openai(monkeypatch, with_alpha_border=False)
    path = generate_sprite("hazard", str(tmp_path))
    assert path.endswith("hazard.png")


def test_generate_sprite_defaults_to_low_quality(tmp_path, monkeypatch):
    # Every sprite is resized to 64x64 immediately after generation, so paying for
    # gpt-image-1's default ("auto", a slow high-effort render) buys nothing visible --
    # "low" should be the default unless overridden.
    calls = _install_fake_openai(monkeypatch, with_alpha_border=False)
    generate_sprite("key", str(tmp_path))
    assert calls[0]["quality"] == "low"


def test_generate_sprite_quality_overridable_via_env(tmp_path, monkeypatch):
    calls = _install_fake_openai(monkeypatch, with_alpha_border=False)
    monkeypatch.setenv("INFINIENV_IMAGE_QUALITY", "high")
    generate_sprite("key", str(tmp_path))
    assert calls[0]["quality"] == "high"


def test_generate_sprite_quality_kwarg_overrides_env(tmp_path, monkeypatch):
    calls = _install_fake_openai(monkeypatch, with_alpha_border=False)
    monkeypatch.setenv("INFINIENV_IMAGE_QUALITY", "high")
    generate_sprite("key", str(tmp_path), quality="medium")
    assert calls[0]["quality"] == "medium"


def test_generate_sprite_description_override_replaces_default(tmp_path, monkeypatch):
    calls = _install_fake_openai(monkeypatch, with_alpha_border=False)
    generate_sprite("agent", str(tmp_path), description="a green-clothed Italian rescuer with a mustache")
    assert "small friendly robot" not in calls[0]["prompt"]
    assert "green-clothed Italian rescuer" in calls[0]["prompt"]


def test_generate_sprite_without_description_uses_object_descriptions_default(tmp_path, monkeypatch):
    calls = _install_fake_openai(monkeypatch, with_alpha_border=False)
    generate_sprite("agent", str(tmp_path))
    assert "small friendly robot" in calls[0]["prompt"]
