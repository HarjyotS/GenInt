"""Optional local sprite generation via a small on-device diffusion model.

An alternate backend to generator_openai.py -- no cloud API call, no account rate limit, no
external content-moderation gate (two real OpenAI failure modes: gpt-image-1's real
5-requests/minute rate limit, and a separate run's hero sprite rejected outright by OpenAI's
moderation system -- see notes.md for both). This is the *default* backend (see
resolver.py::_select_sprite_generator); "openai" remains available as an explicit opt-in via
INFINIENV_SPRITE_BACKEND=openai. Same contract as generator_openai.generate_sprite, so it's a
drop-in alternate implementation -- resolve_assets()'s caching/fallback logic is unchanged either
way.

Not part of the P0 loop, same as generator_openai.py: requires the `diffusion` extra
(`pip install infinienv[diffusion]`, which includes torch/diffusers/transformers/rembg[cpu]) --
never imported unless this backend is explicitly selected, so no other usage path needs any of
that installed.
"""

from __future__ import annotations

import os
import threading

from infinienv.assets.generator_openai import OBJECT_DESCRIPTIONS, TEXTURE_TILE_TYPES, _crop_to_content
from infinienv.llm.base import ProviderError

# Model weights (torch/diffusers) and the rembg/U2Net segmentation model must NOT be downloaded
# fresh into wherever `HOME` happens to resolve to -- inside a `--sandbox` run, `HOME` resolves
# *within that one run's ephemeral, per-attempt workspace filesystem*, not the real host home
# directory, so every sandboxed run using this backend was silently re-downloading multi-GB
# weights from scratch (a real run's sandbox_workspace grew to 1.2GB from exactly this before it
# was caught and this fix landed -- see notes.md). `INFINIENV_MODEL_CACHE_DIR` is a single,
# explicit, project-level cache root (default: `.infinienv_model_cache/` next to
# `.infinienv_asset_cache/`) that both the outer (non-sandboxed) process and every sandboxed
# subprocess resolve to the exact same absolute path -- `sandbox/runner.py` sets this env var in
# the environment the sandboxed process inherits *and* grants that exact host path read-write
# access, so a download that already happened once (by any run, sandboxed or not) is reused by
# every run after it, the same way `.infinienv_asset_cache/` already reuses generated sprites.
_MODEL_CACHE_DIR = os.environ.get("INFINIENV_MODEL_CACHE_DIR") or os.path.join(
    os.getcwd(), ".infinienv_model_cache"
)
_HF_CACHE_DIR = os.path.join(_MODEL_CACHE_DIR, "huggingface")
_U2NET_CACHE_DIR = os.path.join(_MODEL_CACHE_DIR, "u2net")
os.makedirs(_HF_CACHE_DIR, exist_ok=True)
os.makedirs(_U2NET_CACHE_DIR, exist_ok=True)
# setdefault, not direct assignment: an explicit HF_HOME/U2NET_HOME the user has already set
# (the standard env vars these libraries themselves respect) must still win.
os.environ.setdefault("HF_HOME", _HF_CACHE_DIR)
os.environ.setdefault("U2NET_HOME", _U2NET_CACHE_DIR)


def model_cache_dir() -> str:
    """The shared, project-level directory local diffusion/background-removal model weights are
    cached under -- the same absolute path is granted read-write into every sandboxed run (see
    `sandbox/runner.py`) so weights downloaded once are reused by every run after, sandboxed or
    not, instead of re-downloading per run."""
    return _MODEL_CACHE_DIR

# SD-Turbo: 1-4 step inference (no classifier-free guidance needed), small enough to fit "small
# quick sprites." Ships under the Stability AI Community License (free for research/personal/
# small-business use; a revenue threshold applies beyond that) -- not as permissive as this
# repo's other dependencies. Override via INFINIENV_DIFFUSION_MODEL if that doesn't fit how this
# project is used; nothing else in this module assumes SD-Turbo specifically.
DEFAULT_DIFFUSION_MODEL = "stabilityai/sd-turbo"

# Local diffusion pipelines have no request-time "transparent background" feature the way
# OpenAI's Images API does (background="transparent") -- there's no alpha channel at all.
#
# First approach tried: prompt for a solid chroma-key background color, then remove it by
# color-distance thresholding. Live-verified NOT reliable: a 2-step distilled model like
# SD-Turbo doesn't consistently follow "solid flat background" instructions -- one real generated
# sprite came back as pink corrugated stripes with a red-framed square instead of anything close
# to a flat color, so there was nothing clean to key against. A background-removal *model*
# (rembg/U2Net, see `_remove_background` below) segments foreground from background regardless of
# what the generator actually painted, so it doesn't depend on the generator's prompt adherence
# at all -- see CLAUDE.md's asset pipeline section for the full live-verification account of why
# chroma-keying was replaced rather than just re-tuned.
# The fixed style/framing instructions come BEFORE {desc}, not after. Live-verified why this
# order matters: SD-Turbo's CLIP text encoder hard-truncates any prompt past 77 tokens, and a
# scene-derived description (the player-character description in particular can run up to
# _AGENT_DESCRIPTION_MAX_CHARS=220 characters, see resolver.py::_scene_descriptions) routinely
# pushed the *old* desc-first template past that limit -- silently truncating away the trailing
# "isolated object... plain background" instructions and leaving only the raw description. The
# real, observed failure: for a long player-character description, SD-Turbo drew an entire
# multi-element scene (floating islands, water, several small figures) instead of one isolated
# character, which rembg then had no single foreground object to segment -- the resulting sprite
# came back nearly blank. Putting the load-bearing instructions first means truncation (if it
# still happens) only ever drops the tail of the description, never the formatting instructions
# the rest of this pipeline (crop-to-content, background removal) depends on.
DIFFUSION_SPRITE_PROMPT_TEMPLATE = (
    "Clean 2D pixel art game sprite. A single isolated object filling nearly the entire frame "
    "edge-to-edge with minimal empty margin, centered, on a plain simple background clearly "
    "distinct from the object, no text, no labels, no scene or background elements. "
    "Subject: {desc}"
)

# Texture tiles need no chroma-key/transparency handling at all -- they're meant to fill the
# whole opaque canvas already, same reasoning as generator_openai.py's TEXTURE_PROMPT_TEMPLATE.
DIFFUSION_TEXTURE_PROMPT_TEMPLATE = (
    "Seamless tileable 2D pixel art game texture, no text, no labels. The texture must cover "
    "the ENTIRE square canvas edge-to-edge with zero border and zero margin -- an opaque "
    "surface filling 100% of the frame, not an isolated object. Subject: {desc}"
)

_pipeline = None
_pipeline_lock = threading.Lock()


def _resolve_device_and_dtype():
    import torch

    if torch.cuda.is_available():
        return "cuda", torch.float16
    if torch.backends.mps.is_available():
        # float16 on MPS has a history of being unreliable in diffusers; float32 is slower
        # but correct.
        return "mps", torch.float32
    return "cpu", torch.float32


def _get_pipeline(model_id: str):
    """Lazily load and cache the pipeline for this process. Raises ImportError if the
    `diffusion` extra isn't installed -- caught and converted to a ProviderError by
    generate_sprite(), never handled here, so this function's contract stays a plain "give me
    a working pipeline or raise."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            from diffusers import AutoPipelineForText2Image

            device, dtype = _resolve_device_and_dtype()
            pipe = AutoPipelineForText2Image.from_pretrained(model_id, torch_dtype=dtype)
            _pipeline = pipe.to(device)
    return _pipeline


def _run_pipeline(prompt: str, *, model_id: str):
    """The one seam tests mock -- everything above/below this call in generate_sprite() is
    pure PIL/numpy post-processing with no torch/diffusers dependency, so tests never need the
    `diffusion` extra installed to exercise that logic."""
    pipe = _get_pipeline(model_id)
    # diffusers pipeline objects aren't guaranteed safe for concurrent __call__, and unlike the
    # network-bound OpenAI path there's no latency-hiding argument for true parallelism here
    # (local inference is CPU/GPU-bound) -- serialize actual generation through the same lock
    # used to guard pipeline construction.
    with _pipeline_lock:
        result = pipe(prompt=prompt, num_inference_steps=2, guidance_scale=0.0)
    return result.images[0]


def _remove_background(img):
    """Segment the foreground object out of `img` using rembg's default (U2Net-based) model,
    returning an RGBA image with a real alpha channel. Robust to whatever background the
    generator actually painted -- unlike chroma-keying, this doesn't depend on the model
    reliably producing one specific solid color (see module docstring for why that mattered)."""
    from rembg import remove

    return remove(img.convert("RGB")).convert("RGBA")


def generate_sprite(
    object_type: str,
    cache_dir: str,
    *,
    model: str | None = None,
    quality: str | None = None,
    description: str | None = None,
) -> str:
    """Generate (or reuse a cached) sprite for `object_type` via a local diffusion model.

    Same contract as generator_openai.generate_sprite -- writes {cache_dir}/{object_type}.png,
    returns the path. `quality` is accepted for interface parity with the OpenAI backend but is
    a no-op here: there's no equivalent quality-tier concept for a 1-4-step turbo model.
    """
    model_id = model or os.environ.get("INFINIENV_DIFFUSION_MODEL", DEFAULT_DIFFUSION_MODEL)
    desc = description or OBJECT_DESCRIPTIONS.get(object_type, object_type.replace("_", " "))
    is_texture = object_type in TEXTURE_TILE_TYPES
    template = DIFFUSION_TEXTURE_PROMPT_TEMPLATE if is_texture else DIFFUSION_SPRITE_PROMPT_TEMPLATE
    prompt = template.format(desc=desc)

    try:
        img = _run_pipeline(prompt, model_id=model_id)
        if not is_texture:
            img = _remove_background(img)
    except ImportError as exc:
        raise ProviderError(
            "The 'diffusion' extra is not installed. Install it with `pip install infinienv[diffusion]`."
        ) from exc
    except Exception as exc:
        raise ProviderError(f"local image generation failed for {object_type!r}: {exc}") from exc

    if not is_texture:
        img = _crop_to_content(img)
    img = img.convert("RGBA").resize((64, 64))

    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{object_type}.png")
    img.save(path)
    return path
