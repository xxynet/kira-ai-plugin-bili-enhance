"""Validate tool input and serialize bounded feed results."""

import asyncio
from datetime import datetime
from urllib.parse import urlsplit

from core.adapter import FeedRef
from core.adapter.src.bilibili.feed_content import COMMENT_RESOURCES
from core.chat import MessageChain
from core.chat.message_elements import At, Emoji, Image, Text

from .i18n import PluginError


def invalid(parameter):
    raise PluginError("invalid_input", parameter=parameter)


def integer(value, parameter, minimum=1, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        invalid(parameter)
    return value


def positive_id(value, parameter):
    if type(value) not in (str, int) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        invalid(parameter)
    return str(value)


def optional_text(value, parameter, maximum=4096):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > maximum):
        invalid(parameter)
    return value


async def chain(elements, metadata, *, mentions=False):
    if not isinstance(elements, list) or not 1 <= len(elements) <= 100:
        invalid("content")
    result = []
    text_size = 0
    for item in elements:
        if not isinstance(item, dict):
            invalid("content")
        kind = item.get('type')
        if not isinstance(kind, str):
            invalid('content.type')
        fields = {
            "text": {"type", "text"}, "image": {"type", "file"},
            "emoji": {"type", "id"}, "at": {"type", "id", "nickname"},
        }.get(kind)
        if fields is None or set(item) - fields:
            invalid("content")
        metadata_kind = "img" if kind == "image" else kind
        if metadata_kind not in metadata.supported_elements or (kind == "at" and not mentions):
            invalid("content.type")
        if kind == "text":
            value = item.get("text")
            if not isinstance(value, str):
                invalid("content.text")
            text_size += len(value)
            if text_size > 20000:
                invalid("content.text")
            result.append(Text(value))
        elif kind == "image":
            value = optional_text(item.get("file"), "content.file")
            if value is None:
                invalid("content.file")
            if value.startswith(("http://", "https://")):
                parsed = urlsplit(value)
                if not parsed.hostname or parsed.username or parsed.password:
                    invalid("content.file")
            # Media constructors may inspect local files; move that work off-loop.
            result.append(await asyncio.to_thread(Image, image=value))
        elif kind == "emoji":
            value = item.get("id")
            if type(value) not in (str, int) or str(value) not in (metadata.emojis or {}):
                invalid("content.id")
            result.append(Emoji(str(value)))
        else:
            nickname = item.get("nickname")
            optional_text(nickname, "content.nickname", 128)
            result.append(At(positive_id(item.get("id"), "content.id"), nickname=nickname))
    if not any(not isinstance(element, Text) or element.text.strip() for element in result):
        invalid("content")
    return MessageChain(result)


def target_ref(target):
    if not isinstance(target, dict) or set(target) != {"resource_type", "id"}:
        invalid("target")
    kind = target["resource_type"]
    if not isinstance(kind, str) or kind not in set(COMMENT_RESOURCES.values()):
        invalid("target.resource_type")
    value = target["id"]
    if kind == "video" and isinstance(value, str) and value.startswith("BV"):
        import re
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
            invalid("target.id")
    else:
        value = positive_id(value, "target.id")
    return FeedRef(kind, value)


def post_options(options):
    if options is None:
        return {}
    if not isinstance(options, dict) or set(options) - {
        "topic_id", "vote_id", "live_reserve_id", "send_time", "up_choose_comment", "close_comment",
    }:
        invalid("options")
    result = dict(options)
    for key in ("topic_id", "vote_id", "live_reserve_id"):
        if key in result:
            result[key] = int(positive_id(result[key], "options." + key))
    for key in ("up_choose_comment", "close_comment"):
        if key in result and type(result[key]) is not bool:
            invalid("options." + key)
    if "send_time" in result:
        value = result["send_time"]
        if not isinstance(value, str):
            invalid("options.send_time")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            invalid("options.send_time")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            invalid("options.send_time")
        result["send_time"] = parsed
    return result


def ref_data(ref):
    return {"resource_type": ref.resource_type, "id": str(ref.id)} if ref is not None else None


def item_data(item):
    return {
        "ref": ref_data(item.ref), "comment_target": ref_data(item.comment_target),
        "kind": str(item.kind), "title": (item.title or "")[:500],
        "text": "".join(element.repr for element in item.content)[:3000],
        "author": {"id": item.author.id, "name": (item.author.name or "")[:128]},
        "url": item.url, "published_at": item.published_at.isoformat() if item.published_at else None,
        "duration": item.duration, "stats": item.stats,
        "attachments": [
            {"kind": attachment.kind, "url": attachment.url, "ref": ref_data(attachment.ref),
             "title": (attachment.title or "")[:500], "cover_url": attachment.cover_url}
            for attachment in item.attachments[:9]
        ],
        "linked_content": ref_data(item.linked_content),
        "original": {"ref": ref_data(item.original.ref), "title": (item.original.title or "")[:500],
                     "text": "".join(element.repr for element in item.original.content)[:1000]}
        if item.original else None,
    }


def page_data(page):
    return {"items": [item_data(item) for item in page.items],
            "next_cursor": page.next_cursor, "has_more": page.has_more}


def write_data(result):
    # Expose only public receipt fields, never the raw SDK response.
    if not isinstance(result, dict):
        return {}
    receipt = {key: str(result[key]) for key in ("rpid", "dynamic_id", "dynamic_id_str", "id_str") if result.get(key)}
    reply = result.get("reply")
    if isinstance(reply, dict) and reply.get("rpid"):
        receipt["rpid"] = str(reply["rpid"])
    return receipt


def comment_data(reply, *, root=None, parent=None, include_replies=True):
    """Expose public comment fields and a bounded preview of attached replies."""
    def identifier(value):
        if type(value) not in (str, int) or str(value) in ("", "0"):
            return None
        return str(value)

    comment_id = identifier(reply.get("rpid_str") or reply.get("rpid"))
    root_id = identifier(reply.get("root_str") or reply.get("root")) or root or comment_id
    parent_id = identifier(reply.get("parent_str") or reply.get("parent")) or parent
    member = reply.get("member") or {}
    content = reply.get("content") or {}
    text = str(content.get("message") or "")
    attached = reply.get("replies") or []
    reply_count = reply.get("rcount", reply.get("count"))
    if type(reply_count) is not int or reply_count < 0:
        reply_count = None
    images = [item["img_src"] for item in (content.get("pictures") or [])[:9]
              if isinstance(item, dict) and isinstance(item.get("img_src"), str)]
    result = {
        "comment_id": comment_id, "root_comment_id": root_id, "parent_comment_id": parent_id,
        "author": {"id": identifier(member.get("mid")), "name": str(member.get("uname") or "")[:128]},
        "text": text[:2000], "text_truncated": len(text) > 2000,
        "created_at": reply.get("ctime"), "likes": reply.get("like", 0),
        "reply_count": reply_count, "images": images,
        "replies": [],
    }
    if include_replies:
        result["replies"] = [comment_data(item, root=root_id, parent=comment_id, include_replies=False)
                             for item in attached[:3] if isinstance(item, dict)]
    result["replies_complete"] = len(result["replies"]) >= reply_count if reply_count is not None else None
    return result