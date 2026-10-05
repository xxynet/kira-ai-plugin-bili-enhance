"""Read space uploads using the selected adapter's existing credentials."""

from bilibili_api import user as sdk_user

from core.adapter.src.bilibili.client import get_bilibili_client
from core.adapter.src.bilibili.feed_content import video_item

from .content import integer, invalid, item_data, positive_id

MAX_PAGE_SIZE = 30


async def browse(adapter, *, author_id, page=1, count=5, keyword="", order="pubdate", max_count=20):
    uid = positive_id(author_id, "author_id")
    integer(page, "page", maximum=10000)
    integer(count, "count", maximum=min(max_count, MAX_PAGE_SIZE))
    if not isinstance(keyword, str) or len(keyword) > 256:
        invalid("keyword")
    orders = {"pubdate": sdk_user.VideoOrder.PUBDATE,
              "view": sdk_user.VideoOrder.VIEW, "favorite": sdk_user.VideoOrder.FAVORITE}
    if not isinstance(order, str) or order not in orders:
        invalid("order")
    get_bilibili_client()
    handle = sdk_user.User(uid=int(uid), credential=adapter.credential)
    response = await handle.get_videos(pn=page, ps=count, keyword=keyword.strip(), order=orders[order])
    entries = (response.get("list") or {}).get("vlist") or []
    items = []
    for entry in entries[:count]:
        if not isinstance(entry, dict):
            continue
        item = video_item({**entry, "mid": uid,
                           "pubdate": entry.get("created", entry.get("pubdate")),
                           "duration": entry.get("length", entry.get("duration"))})
        items.append({**item_data(item), "cover_url": item.cover_url})
    total = (response.get("page") or {}).get("count")
    if type(total) is not int or total < 0:
        total = None
    has_more = page * count < total if total is not None else None
    return {
        "author_id": uid, "page": page, "count": count, "keyword": keyword.strip(), "order": order,
        "items": items, "total": total, "has_more": has_more,
        "next_page": page + 1 if has_more else None,
    }