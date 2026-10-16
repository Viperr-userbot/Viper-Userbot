"""
RMBG (Remove Background) Module for Kitten Assistant Userbot.
Provides free state-of-the-art AI background removal with automatic multi-provider fallback:
1. BRIA RMBG-2.0 (State of the art quality via official Hugging Face Space Gradio API)
2. BRIA RMBG-1.4 (Official Hugging Face Space Gradio API fallback)
3. Local Rembg (Open-source onnxruntime/u2net fallback if installed)
"""
import io
import json
import base64
import logging
import asyncio
from typing import Tuple, Optional
from PIL import Image
import httpx

log = logging.getLogger("hwh.rmbg")

# Official BRIA AI Hugging Face Spaces for free inference
BRIA_RMBG_2_0_URL = "https://briaai-bria-rmbg-2-0.hf.space"
BRIA_RMBG_1_4_URL = "https://briaai-bria-rmbg-1-4.hf.space"

# Optional local rembg support
try:
    import rembg
    _HAS_LOCAL_REMBG = True
except ImportError:
    rembg = None
    _HAS_LOCAL_REMBG = False


def prepare_input_image(image_bytes: bytes, max_dim: int = 768) -> bytes:
    """
    Validates and normalizes input image to RGB format.
    Optimized for Telegram sticker speed by keeping max dimension <= 768px.
    """
    img = Image.open(io.BytesIO(image_bytes))
    
    # Downscale if larger than max_dim for fast upload and inference
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Convert to RGB if needed (handling RGBA, CMYK, P, L)
    if img.mode != "RGB":
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        else:
            img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_telegram_sticker(rgba_img: Image.Image) -> bytes:
    """
    Formats transparent RGBA image to Telegram sticker standard:
    - Exactly 512px on the longest dimension, other dimension <= 512px.
    - Lossless WEBP format with transparency preserved.
    """
    w, h = rgba_img.size
    if w >= h:
        new_w = 512
        new_h = max(1, int(h * (512 / w)))
    else:
        new_h = 512
        new_w = max(1, int(w * (512 / h)))

    resized = rgba_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="WEBP")
    return buf.getvalue()


async def _call_gradio_space(
    space_url: str,
    endpoint_name: str,
    data_url: str,
    timeout: float = 30.0
) -> Optional[bytes]:
    """
    Calls a Gradio 5 space via its server-sent events call API.
    """
    call_url = f"{space_url}/gradio_api/call/{endpoint_name}"
    payload = {"data": [{"url": data_url}]}
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Step 1: Submit job
        r = await client.post(call_url, json=payload)
        if r.status_code != 200:
            log.warning(f"Gradio space {space_url} POST returned {r.status_code}: {r.text[:150]}")
            return None
            
        data = r.json()
        event_id = data.get("event_id")
        if not event_id:
            log.warning(f"Gradio space {space_url} did not return event_id: {data}")
            return None

        # Step 2: Poll/stream result event
        res_url = f"{space_url}/gradio_api/call/{endpoint_name}/{event_id}"
        res = await client.get(res_url)
        if res.status_code != 200:
            log.warning(f"Gradio space {space_url} GET returned {res.status_code}: {res.text[:150]}")
            return None

        # Step 3: Parse SSE response lines
        file_download_url = None
        for line in res.text.splitlines():
            if line.startswith("data:"):
                raw_json = line[5:].strip()
                try:
                    parsed = json.loads(raw_json)
                    # Expected return is [Imageslider, FileData] or [FileData]
                    if isinstance(parsed, list):
                        if len(parsed) >= 2 and isinstance(parsed[1], dict) and "url" in parsed[1]:
                            file_download_url = parsed[1]["url"]
                        elif len(parsed) >= 1 and isinstance(parsed[0], dict) and "url" in parsed[0]:
                            file_download_url = parsed[0]["url"]
                except Exception as e:
                    log.debug(f"Failed to parse SSE line from {space_url}: {e}")

        if not file_download_url:
            log.warning(f"No valid file URL extracted from {space_url} SSE response")
            return None

        # Step 4: Download processed image
        down_res = await client.get(file_download_url)
        if down_res.status_code == 200 and len(down_res.content) > 0:
            return down_res.content
        else:
            log.warning(f"Failed to download result image from {file_download_url} (HTTP {down_res.status_code})")
            return None


async def call_bria_rmbg_2(prepared_png: bytes, timeout: float = 30.0) -> Optional[bytes]:
    """Inference via BRIA RMBG-2.0 official Hugging Face Space."""
    try:
        b64 = base64.b64encode(prepared_png).decode("utf-8")
        data_url = f"data:image/png;base64,{b64}"
        return await _call_gradio_space(BRIA_RMBG_2_0_URL, "image", data_url, timeout=timeout)
    except Exception as e:
        log.warning(f"BRIA RMBG-2.0 provider error: {e}")
        return None


async def call_bria_rmbg_1_4(prepared_png: bytes, timeout: float = 30.0) -> Optional[bytes]:
    """Inference via BRIA RMBG-1.4 official Hugging Face Space."""
    try:
        b64 = base64.b64encode(prepared_png).decode("utf-8")
        data_url = f"data:image/png;base64,{b64}"
        return await _call_gradio_space(BRIA_RMBG_1_4_URL, "run", data_url, timeout=timeout)
    except Exception as e:
        log.warning(f"BRIA RMBG-1.4 provider error: {e}")
        return None


def call_local_rembg(prepared_png: bytes) -> Optional[bytes]:
    """Inference via local rembg python package if installed."""
    if not _HAS_LOCAL_REMBG:
        return None
    try:
        return rembg.remove(prepared_png)
    except Exception as e:
        log.warning(f"Local rembg provider error: {e}")
        return None


async def remove_background(
    image_bytes: bytes,
    as_sticker: bool = True,
    timeout: float = 30.0
) -> Tuple[bytes, str]:
    """
    Removes background from image_bytes with optimized multi-provider fallback.
    Returns: (output_bytes, provider_name)
    Raises RuntimeError if all providers fail.
    """
    if not image_bytes:
        raise ValueError("Empty image bytes provided.")

    # Validate and normalize image
    try:
        norm_png = prepare_input_image(image_bytes)
    except Exception as e:
        raise ValueError(f"Invalid or corrupted image: {e}")

    result_bytes = None
    provider_name = ""

    # Priority 1: Local rembg (Instant ~300ms, zero network)
    if _HAS_LOCAL_REMBG:
        try:
            loop = asyncio.get_running_loop()
            result_bytes = await loop.run_in_executor(None, call_local_rembg, norm_png)
            if result_bytes:
                provider_name = "Local Rembg (ONNX)"
        except Exception:
            pass

    # Priority 2: BRIA RMBG-2.0 (State of the Art Quality)
    if not result_bytes:
        log.info("Attempting background removal with BRIA RMBG-2.0...")
        result_bytes = await call_bria_rmbg_2(norm_png, timeout=timeout)
        if result_bytes:
            provider_name = "BRIA RMBG-2.0"

    # Priority 3: BRIA RMBG-1.4 (Fast & Reliable Fallback)
    if not result_bytes:
        log.info("Attempting fallback background removal with BRIA RMBG-1.4...")
        result_bytes = await call_bria_rmbg_1_4(norm_png, timeout=timeout)
        if result_bytes:
            provider_name = "BRIA RMBG-1.4"

    if not result_bytes:
        raise RuntimeError("Background removal failed across all available providers.")

    # Validate resulting image
    out_img = Image.open(io.BytesIO(result_bytes))
    if out_img.mode != "RGBA":
        out_img = out_img.convert("RGBA")

    if as_sticker:
        final_bytes = to_telegram_sticker(out_img)
    else:
        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        final_bytes = buf.getvalue()

    return final_bytes, provider_name
