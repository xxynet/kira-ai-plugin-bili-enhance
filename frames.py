"""Read bounded storyboard images and expose them through KiraAI image capabilities."""

import asyncio
import base64
import io
import json
import math
import struct
import uuid
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image as PillowImage
from PIL import ImageDraw, ImageFont

from core.agent.tool import ToolResult
from core.chat.message_elements import Image
from core.utils.common_utils import desc_img
from core.utils.media_refs import store_session_media

from .content import integer, invalid
from .i18n import PluginError, tr

MAX_FRAMES = 6
MAX_SHEET_BYTES = 12 * 1024 * 1024
MAX_SHEET_PIXELS = 16_000_000
MAX_INDEX_ENTRIES = 20000


def image_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise PluginError("invalid_download")
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("http://"):
        value = "https://" + value[7:]
    try:
        parsed = urlsplit(value)
        allowed = parsed.hostname == "hdslb.com" or (parsed.hostname or "").endswith(".hdslb.com")
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError
    except ValueError:
        raise PluginError("invalid_download") from None
    return value


async def download(url, timeout, limit=MAX_SHEET_BYTES):
    headers = {"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(6):
            url = image_url(url)
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise PluginError("invalid_download")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and int(declared) > limit:
                    raise PluginError("invalid_download")
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw) > limit:
                        raise PluginError("invalid_download")
                return bytes(raw)
    raise PluginError("invalid_download")


def snapshot_entries(snapshot, duration):
    raw = snapshot.get("index") or []
    if not isinstance(raw, list) or len(raw) > MAX_INDEX_ENTRIES:
        raise PluginError("invalid_download")
    entries = []
    for index, item in enumerate(raw):
        value = item.get("timestamp") if isinstance(item, dict) else item
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise PluginError("invalid_download")
        entries.append((float(value), index))
    scale = 1000 if entries and duration > 0 and max(t for t, _ in entries) > duration * 1.5 else 1
    return [(seconds / scale, tile) for seconds, tile in entries]


def sheet_capacity(raw, width, height):
    with PillowImage.open(io.BytesIO(raw)) as image:
        if image.width * image.height > MAX_SHEET_PIXELS or image.width < width or image.height < height:
            raise PluginError("invalid_download")
        return (image.width // width) * (image.height // height)


def crop_tile(raw, index, width, height):
    with PillowImage.open(io.BytesIO(raw)) as image:
        if image.width * image.height > MAX_SHEET_PIXELS:
            raise PluginError("invalid_download")
        columns, rows = image.width // width, image.height // height
        if columns < 1 or rows < 1 or index >= columns * rows:
            raise PluginError("invalid_download")
        x, y = (index % columns) * width, (index // columns) * height
        with image.crop((x, y, x + width, y + height)).convert("RGB") as tile:
            output = io.BytesIO()
            tile.save(output, format="JPEG", quality=85)
            return output.getvalue()


def compose_frames(frames, tiles, width, height):
    """Build one bounded contact sheet in selection order, labeled with actual sample times."""
    columns = min(3, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    scale = min(1, 640 / width, 640 / height)
    tile_width, tile_height = max(1, round(width * scale)), max(1, round(height * scale))
    cell_width, label_height, gap = max(160, tile_width), 26, 8
    image_width = columns * cell_width + (columns + 1) * gap
    image_height = rows * (tile_height + label_height) + (rows + 1) * gap
    with PillowImage.new("RGB", (image_width, image_height), "#202020") as canvas:
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default(size=14)
        for position, (frame, raw) in enumerate(zip(frames, tiles)):
            x = gap + (position % columns) * (cell_width + gap)
            y = gap + (position // columns) * (tile_height + label_height + gap)
            label = f"#{frame['index'] + 1} {frame['got_sec']:.3f}s"
            draw.text((x + 4, y + 4), label, font=font, fill="white")
            with PillowImage.open(io.BytesIO(raw)) as source:
                with source.convert("RGB") as tile:
                    tile.thumbnail((tile_width, tile_height), PillowImage.Resampling.LANCZOS)
                    canvas.paste(tile, (x + (cell_width - tile.width) // 2, y + label_height))
        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=85)
    return output.getvalue(), image_width, image_height, columns, rows


async def read(videos, adapter, *, video, page=None, num=3, at=None):
    integer(num, "num", maximum=MAX_FRAMES)
    if at is not None and (not isinstance(at, list) or not 1 <= len(at) <= MAX_FRAMES or any(
        type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in at
    )):
        invalid("at")
    handle, bvid, page, selected = await videos.part(adapter, video, page)
    duration = selected.get("duration")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise PluginError("no_frames")
    snapshot = await handle.get_video_snapshot(cid=selected["cid"], json_index=True, pvideo=False)
    entries = snapshot_entries(snapshot, duration)
    # Some responses expose the same second-based index only as a binary pvdata resource.
    if not entries and snapshot.get("pvdata"):
        raw = await download(snapshot["pvdata"], videos.timeout, MAX_INDEX_ENTRIES * 2)
        if len(raw) % 2:
            raise PluginError("invalid_download")
        entries = [(float(t), i) for i, t in enumerate(struct.unpack(f">{len(raw) // 2}H", raw))]
    sheets = snapshot.get("image") or []
    if not entries or not isinstance(sheets, list) or not sheets:
        raise PluginError("no_frames")
    covered = min(duration, max(t for t, _ in entries))
    if at is not None and any(t > covered for t in at):
        invalid("at")
    targets = at if at is not None else [covered * (i + 1) / (num + 1) for i in range(num)]
    width, height = snapshot.get("img_x_size", 480), snapshot.get("img_y_size", 270)
    if any(type(size) is not int or not 1 <= size <= 2048 for size in (width, height)):
        raise PluginError("invalid_download")
    cache = {0: await download(sheets[0], videos.timeout)}
    capacity = await asyncio.to_thread(sheet_capacity, cache[0], width, height)
    frames, tiles, failed = [], [], []
    for index, target in enumerate(targets):
        actual, tile = min(entries, key=lambda entry: abs(entry[0] - target))
        try:
            sheet, position = divmod(tile, capacity)
            if sheet >= len(sheets):
                raise PluginError("invalid_download")
            if sheet not in cache:
                cache[sheet] = await download(sheets[sheet], videos.timeout)
            raw = await asyncio.to_thread(crop_tile, cache[sheet], position, width, height)
            tiles.append(raw)
            frames.append({"index": index, "want_sec": round(target, 3), "got_sec": round(actual, 3)})
        except (asyncio.TimeoutError, TimeoutError):
            raise
        except Exception:
            failed.append({"index": index, "code": "invalid_download"})
    if not tiles:
        raise PluginError("no_frames")
    raw, image_width, image_height, columns, rows = await asyncio.to_thread(compose_frames, frames, tiles, width, height)
    image = Image(image=base64.b64encode(raw).decode("ascii"), mime="image/jpeg", name=f"{bvid}_p{page}_frames.jpg")
    return {
        "bvid": bvid, "page": page, "duration": duration, "covered_sec": covered,
        "width": image_width, "height": image_height, "columns": columns, "rows": rows,
        "frames": frames, "failed": failed,
    }, [image]


async def present(ctx, event, payload, images):
    sid = getattr(event, "sid", None)
    capabilities = await ctx.get_session_capabilities(sid) if sid else {}
    recognition = capabilities.get("image_recognition") or {}
    refs = []
    payload["image_attached"] = False
    payload["note"] = tr(ctx.get_lang(), "frames_note")
    if not recognition.get("enabled", True):
        payload["image_skip_reason"] = "image_recognition_disabled"
    elif recognition.get("mode") == "native" and sid:
        message_id = "bili_frames_" + uuid.uuid4().hex
        refs.append(await store_session_media(images[0], sid, message_id))
        payload["image_attached"] = True
    else:
        try:
            description = await desc_img(client=ctx.provider_mgr.get_default_vlm(), image=images[0],
                                         prompt=recognition.get("desc_prompt") or None, lang=ctx.get_lang())
        except (asyncio.TimeoutError, TimeoutError):
            raise
        except Exception:
            description = ""
        payload["description"] = str(description or "")[:6000]
        payload["image_described"] = bool(payload["description"])
        if not payload["image_described"]:
            payload["image_skip_reason"] = "image_description_unavailable"
    if not payload["image_attached"]:
        payload["note"] += " " + tr(ctx.get_lang(), "frames_not_attached")
    return ToolResult(text=json.dumps(payload, ensure_ascii=False), attachments=images, media_refs=refs)
