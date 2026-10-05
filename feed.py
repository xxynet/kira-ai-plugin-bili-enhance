"""Expose existing feed capabilities without duplicating platform operations."""

from core.adapter import FeedPost, FeedQuery, FeedSearchQuery

from .accounts import capability
from .content import chain, integer, invalid, optional_text, page_data, positive_id, post_options, target_ref, write_data


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