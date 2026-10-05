"""Bilibili-specific video tools borrowing the selected adapter's credentials."""

import asyncio
import html
import json
import math
import re
import uuid
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bilibili_api import video as sdk_video

from core.adapter.src.bilibili.client import get_bilibili_client
from core.chat.message_elements import File
from core.utils.path_utils import get_data_path

from .content import integer, invalid
from .i18n import PluginError

BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
VIDEO_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
SUBTITLE_DOMAINS = ("hdslb.com", "bilibili.com", "bilivideo.com")


def checked_url(url, *, subtitle=False):
    if not isinstance(url, str) or len(url) > 8192:
        raise PluginError("invalid_download" if subtitle else "invalid_video")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise PluginError("invalid_download" if subtitle else "invalid_video") from None
    host = (parsed.hostname or "").lower()
    allowed = (any(host == domain or host.endswith("." + domain) for domain in SUBTITLE_DOMAINS)
               if subtitle else host in VIDEO_HOSTS | {"b23.tv"})
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443) or not allowed:
        raise PluginError("invalid_download" if subtitle else "invalid_video")
    return parsed


async def resolve_video(value, timeout):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise PluginError("invalid_video")
    value = value.strip()
    if BV_PATTERN.fullmatch(value):
        return value, 1
    parsed = checked_url(value)
    if parsed.hostname == "b23.tv":
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            visited = set()
            for _ in range(6):
                parsed = checked_url(value)
                if parsed.hostname != "b23.tv":
                    break
                if value in visited:
                    raise PluginError("invalid_video")
                visited.add(value)
                response = await client.head(value)
                if response.status_code not in (301, 302, 303, 307, 308) or not response.headers.get("location"):
                    raise PluginError("invalid_video")
                value = urljoin(value, response.headers["location"])
            else:
                raise PluginError("invalid_video")
    parsed = checked_url(value)
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
    if parsed.hostname not in VIDEO_HOSTS or match is None:
        raise PluginError("invalid_video")
    page_text = parse_qs(parsed.query).get("p", ["1"])[0]
    if not page_text.isascii() or not page_text.isdigit():
        invalid("page")
    return match.group(1), integer(int(page_text), "page", maximum=10000)


def normalize_language(value):
    value = value.lower().replace("_", "-").removeprefix("ai-")
    parts = value.split("-")
    script = ""
    for part in parts[1:]:
        if part in ("hans", "hant"):
            script = part
        elif part in ("cn", "sg"):
            script = "hans"
        elif part in ("tw", "hk", "mo"):
            script = "hant"
    return parts[0], script


def pick_track(tracks, requested):
    tracks = [track for track in tracks if isinstance(track, dict) and isinstance(track.get("lan"), str)]
    if not tracks:
        raise PluginError("no_subtitle")
    if not requested:
        return tracks[0], tracks
    if not isinstance(requested, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", requested):
        invalid("language")
    base, script = normalize_language(requested)
    ranked = []
    for index, track in enumerate(tracks):
        track_base, track_script = normalize_language(track["lan"])
        if track_base != base or (script and track_script and script != track_script):
            continue
        exact = track["lan"].lower().replace("_", "-") == requested.lower().replace("_", "-")
        ranked.append((4 if exact else 3 if script and script == track_script else 1, -index, track))
    if not ranked:
        invalid("language")
    return max(ranked, key=lambda item: item[:2])[2], tracks


def subtitle_entries(data):
    if not isinstance(data, dict) or not isinstance(data.get("body"), list):
        raise PluginError("invalid_download")
    entries = []
    for item in data["body"]:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise PluginError("invalid_download")
        try:
            start = float(item.get("from", 0))
            end = float(item.get("to", start + 2))
        except (ValueError, TypeError):
            raise PluginError("invalid_download") from None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < 0:
            raise PluginError("invalid_download")
        text = html.unescape(re.sub(r"<[^>]*>", "", item["content"])).strip()
        if text:
            entries.append((start, end if end > start else start + 2, text))
    if not entries:
        raise PluginError("empty_subtitle")
    return entries


def srt_timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, milliseconds = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build_srt(entries):
    return "\n".join(
        f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(entries, 1)
    )


def write_srt(directory, filename, text):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


class VideoService:
    def __init__(self, timeout=60, max_bytes=2097152, max_chars=12000):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_chars = max_chars

    async def handle(self, adapter, value):
        bvid, url_page = await resolve_video(value, self.timeout)
        get_bilibili_client()
        return sdk_video.Video(bvid=bvid, credential=adapter.credential), bvid, url_page

    @staticmethod
    async def pages(handle, info):
        pages = info.get("pages") or await handle.get_pages()
        if not isinstance(pages, list) or not pages:
            invalid("page")
        return pages

    async def info(self, adapter, *, video, page=None):
        handle, bvid, url_page = await self.handle(adapter, video)
        info = await handle.get_info()
        pages = await self.pages(handle, info)
        page = integer(url_page if page is None else page, "page", maximum=len(pages))
        owner = info.get("owner") or {}
        stat = info.get("stat") or {}
        selected = pages[page - 1]
        return {
            "bvid": bvid, "aid": str(info.get("aid", "")), "title": str(info.get("title") or "")[:500],
            "description": str(info.get("desc") or "")[:3000],
            "author": {"id": str(owner.get("mid", "")), "name": str(owner.get("name") or "")[:128]},
            "url": f"https://www.bilibili.com/video/{bvid}?p={page}",
            "published_at": info.get("pubdate"), "duration": info.get("duration"),
            "stats": {key: stat[key] for key in ("view", "danmaku", "reply", "favorite", "coin", "share", "like") if key in stat},
            "page": page, "page_count": len(pages), "part": str(selected.get("part") or "")[:500],
            "parts": [{"page": index, "title": str(item.get("part") or "")[:128], "duration": item.get("duration")}
                      for index, item in enumerate(pages[:100], 1)],
        }

    async def like(self, adapter, *, video, liked):
        if type(liked) is not bool:
            invalid("liked")
        adapter.credential.raise_for_no_sessdata()
        adapter.credential.raise_for_no_bili_jct()
        handle, bvid, _ = await self.handle(adapter, video)
        await handle.like(status=liked)
        return {"bvid": bvid, "liked": liked}

    async def download_subtitle(self, url):
        if isinstance(url, str) and url.startswith("//"):
            url = "https:" + url
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            for _ in range(6):
                checked_url(url, subtitle=True)
                async with client.stream("GET", url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise PluginError("invalid_download")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > self.max_bytes:
                        raise PluginError("invalid_download")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        content.extend(chunk)
                        if len(content) > self.max_bytes:
                            raise PluginError("invalid_download")
                    return await asyncio.to_thread(json.loads, bytes(content))
        raise PluginError("invalid_download")

    async def subtitle(self, adapter, *, video, page=None, language="", entry_offset=0, max_entries=20):
        integer(entry_offset, "entry_offset", minimum=0)
        integer(max_entries, "max_entries", maximum=100)
        if not isinstance(language, str):
            invalid("language")
        handle, bvid, url_page = await self.handle(adapter, video)
        info = await handle.get_info()
        pages = await self.pages(handle, info)
        page = integer(url_page if page is None else page, "page", maximum=len(pages))
        cid = pages[page - 1].get("cid")
        if not cid:
            invalid("page")
        subtitle_info = await handle.get_subtitle(cid=cid)
        tracks = (subtitle_info or {}).get("subtitles") or []
        track, tracks = pick_track(tracks, language)
        data = await self.download_subtitle(track.get("subtitle_url"))
        entries = await asyncio.to_thread(subtitle_entries, data)
        if entry_offset >= len(entries) and entry_offset != 0:
            invalid("entry_offset")
        preview = []
        budget = self.max_chars
        text_truncated = False
        for start, end, text in entries[entry_offset:entry_offset + max_entries]:
            if budget <= 0:
                break
            preview.append({"from": start, "to": end, "text": text[:budget]})
            text_truncated = text_truncated or len(text) > budget
            budget -= min(len(text), budget)
        next_offset = entry_offset + len(preview)
        safe_language = re.sub(r"[^0-9A-Za-z-]", "", track["lan"])[:32] or "sub"
        filename = f"{bvid}_p{page}_{safe_language}_{uuid.uuid4().hex}.srt"
        text = await asyncio.to_thread(build_srt, entries)
        if len(text.encode("utf-8")) > self.max_bytes * 2:
            raise PluginError("invalid_download")
        writer = asyncio.create_task(asyncio.to_thread(
            write_srt, get_data_path() / "temp" / "bili_enhance_subtitles", filename, text,
        ))
        try:
            path = await asyncio.shield(writer)
        except asyncio.CancelledError:
            await writer
            raise
        return {
            "bvid": bvid, "page": page, "language": track["lan"],
            "available_languages": [{"code": item["lan"], "label": str(item.get("lan_doc") or item["lan"])[:128]}
                                    for item in tracks[:40]],
            "entry_count": len(entries), "entry_offset": entry_offset, "entries": preview,
            "text_truncated": text_truncated,
            "next_entry_offset": next_offset if next_offset < len(entries) else None,
        }, [await asyncio.to_thread(File, file=str(path), name=filename)]