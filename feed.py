"""Expose existing feed capabilities without duplicating platform operations."""

from bilibili_api import comment as sdk_comment
from bilibili_api.utils.aid_bvid_transformer import bvid2aid

from core.adapter import FeedPost, FeedQuery, FeedSearchQuery
from core.adapter.src.bilibili.feed_content import COMMENT_RESOURCES, comment_target

from .accounts import capability
from .content import chain, comment_data, integer, invalid, optional_text, page_data, positive_id, post_options, ref_data, target_ref, write_data
from .i18n import PluginError


async def browse(adapter, *, source="recommended", count=5, kinds=None, author_id=None, cursor=None, max_count=20):
    integer(count, "count", maximum=max_count)
    if not isinstance(source, str) or source not in {"recommended", "following", "user"}:
        invalid("source")
    if kinds is not None and (not isinstance(kinds, list) or any(
        not isinstance(kind, str) or kind not in {"post", "video", "article", "audio", "unknown"} for kind in kinds
    )):
        invalid("kinds")
    if author_id is not None:
        author_id = positive_id(author_id, "author_id")
    if source == "user" and author_id is None:
        invalid("author_id")
    if source == "recommended" and any(kind != "video" for kind in kinds or []):
        invalid("kinds")
    optional_text(cursor, "cursor", 256)
    page = await capability(adapter).get_feed(FeedQuery(
        source=source, count=count, kinds=tuple(kinds or []), author_id=author_id, cursor=cursor,
    ))
    return page_data(page)


async def search(adapter, *, keyword, kind="video", count=5, author_id=None, cursor=None, max_count=20):
    if optional_text(keyword, "keyword", 256) is None:
        invalid("keyword")
    integer(count, "count", maximum=max_count)
    if not isinstance(kind, str) or kind not in {"video", "article"}:
        invalid("kind")
    if author_id is not None:
        author_id = positive_id(author_id, "author_id")
    optional_text(cursor, "cursor", 256)
    page = await capability(adapter).search_feed(FeedSearchQuery(
        keyword=keyword.strip(), kind=kind, count=count, author_id=author_id, cursor=cursor,
    ))
    return page_data(page)


async def comment(adapter, *, target, content, root=None, parent=None):
    ref = target_ref(target)
    if root is not None:
        root = positive_id(root, "root")
    if parent is not None:
        parent = positive_id(parent, "parent")
        if root is None:
            invalid("root")
    feed = capability(adapter)
    message = await chain(content, await feed.get_comment_metadata())
    return write_data(await feed.send_comment(message, ref, root=root, parent=parent))


async def post(adapter, *, content, options=None):
    extra = post_options(options)
    feed = capability(adapter)
    message = await chain(content, await feed.get_post_metadata(), mentions=True)
    return write_data(await feed.send_post(FeedPost(content=message, extra=extra)))


async def comments(adapter, *, target, count=5, include_replies=True, max_count=20):
    """Read the client's first comment batch for the selected resource."""
    ref = target_ref(target)
    integer(count, "count", maximum=max_count)
    if type(include_replies) is not bool:
        invalid("include_replies")
    client = adapter.get_client()
    if ref.resource_type == "dynamic":
        detail = await client.get_dynamic_info(int(ref.id))
        ref = comment_target(detail.get("item") or {})
        if ref is None:
            raise PluginError("no_comment_target")
    if ref.resource_type == "video" and isinstance(ref.id, str) and ref.id.startswith("BV"):
        try:
            oid = bvid2aid(ref.id)
        except Exception:
            invalid("target.id")
    else:
        oid = int(positive_id(ref.id, "target.id"))
    types = {name: sdk_comment.CommentResourceType(value) for value, name in COMMENT_RESOURCES.items()}
    response = await client.get_comments_lazy(oid=oid, type_=types[ref.resource_type])
    collected = []
    seen = set()
    for pinned, batch in ((True, response.get("top_replies") or []), (False, response.get("replies") or [])):
        for reply in batch:
            if not isinstance(reply, dict):
                continue
            comment_id = str(reply.get("rpid_str") or reply.get("rpid") or "")
            if comment_id and comment_id in seen:
                continue
            if comment_id:
                seen.add(comment_id)
            collected.append((pinned, reply))
    cursor = response.get("cursor") or {}
    has_more = not cursor["is_end"] if type(cursor.get("is_end")) is bool else None
    if len(collected) > count:
        has_more = True
    return {
        "target": ref_data(ref), "first_batch_only": True,
        "comments": [{**comment_data(reply, include_replies=include_replies), "pinned": pinned}
                     for pinned, reply in collected[:count]],
        "has_more": has_more, "truncated": len(collected) > count,
    }