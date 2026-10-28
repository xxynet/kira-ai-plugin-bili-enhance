"""Read one bounded six-minute danmaku segment from the selected video part."""

import math

from .content import integer

SEGMENT_SECONDS = 360
MAX_TEXT_CHARS = 500


async def read(videos, adapter, *, video, page=None, segment=1, count=5, max_count=20):
    integer(segment, "segment", maximum=10000)
    integer(count, "count", maximum=max_count)
    handle, bvid, page, selected = await videos.part(adapter, video, page)
    duration = selected.get("duration")
    total = math.ceil(duration / SEGMENT_SECONDS) if type(duration) in (int, float) and math.isfinite(duration) and duration > 0 else None
    if total is not None:
        integer(segment, "segment", maximum=total)
    batch = await handle.get_danmakus(cid=selected["cid"], from_seg=segment - 1, to_seg=segment - 1)
    items = []
    for item in batch:
        seconds = item.dm_time
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            continue
        text = str(item.text or "")
        items.append({"id": str(item.id_str or item.id_), "time_sec": seconds,
                      "text": text[:MAX_TEXT_CHARS], "text_truncated": len(text) > MAX_TEXT_CHARS})
    items.sort(key=lambda item: item["time_sec"])
    return {
        "bvid": bvid, "page": page, "segment": segment, "segment_start_sec": (segment - 1) * SEGMENT_SECONDS,
        "segment_end_sec": min(segment * SEGMENT_SECONDS, duration) if total else segment * SEGMENT_SECONDS,
        "segment_count": total, "items": items[:count], "truncated": len(items) > count,
        "next_segment": segment + 1 if total is not None and segment < total else None,
    }
