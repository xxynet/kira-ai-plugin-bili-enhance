"""Resolve current adapter instances without owning their account lifecycle."""

from core.adapter import BaseAdapter, FeedCapability, IMCapability

from .i18n import PluginError


def available(ctx):
    return {
        name: adapter for name, adapter in ctx.adapter_mgr.get_adapters().items()
        # Dynamically scanned classes need not share identity with direct imports.
        if isinstance(adapter, BaseAdapter) and adapter.info.platform == 'BiliBili' and adapter.info.enabled
    }


def select(ctx, event, explicit=None, default=""):
    candidates = available(ctx)
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise PluginError("invalid_input", parameter="adapter_name")
        if explicit not in candidates:
            raise PluginError("invalid_account", adapter_name=explicit[:128])
        return candidates[explicit]
    session = getattr(event, "session", None)
    current = getattr(session, "adapter_name", None) or getattr(event, "adapter_name", None)
    if current in candidates:
        return candidates[current]
    if default:
        if default not in candidates:
            raise PluginError("invalid_account", adapter_name=default[:128])
        return candidates[default]
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    raise PluginError("ambiguous_account" if candidates else "no_account")


def capability(adapter, kind=FeedCapability):
    try:
        return adapter.get_capability(kind)
    except ValueError:
        raise PluginError("missing_capability") from None


async def describe(ctx):
    result = []
    for name, adapter in available(ctx).items():
        feed = capability(adapter)
        comment = await feed.get_comment_metadata()
        post = await feed.get_post_metadata()
        try:
            adapter.get_capability(IMCapability)
            im = True
        except ValueError:
            im = False
        result.append({
            "adapter_name": name,
            "uid": str(adapter.bot_uid) if adapter.bot_uid else None,
            "capabilities": ["feed"] + (["im"] if im else []),
            "comment_format": {"elements": comment.supported_elements, "emojis": comment.emojis},
            "post_format": {"elements": post.supported_elements, "emojis": post.emojis},
        })
    return result