"""LLM-native Bilibili tools using existing adapter accounts."""

import asyncio
import json

from core.agent.tool import ToolResult
from core.plugin import BasePlugin, register

from . import accounts, feed
from .i18n import PluginError, language, tr
from .schemas import TOOLS, tool_schema
from .video import VideoService

PLUGIN_ID = "kira-ai-plugin-bili-enhance"
DEFAULTS = {
    "default_adapter": "", "enable_like": True, "enable_comment": True, "enable_post": True,
    "request_timeout": 60, "max_concurrency": 3, "max_count": 20,
    "subtitle_max_chars": 12000, "subtitle_max_bytes": 2097152,
}


class BiliEnhancePlugin(BasePlugin):
    def __init__(self, ctx, cfg):
        super().__init__(ctx, cfg)
        self._closing = True
        self._pending = set()
        self._shutdown_task = None
        self._semaphore = None
        self.settings = {}
        self.videos = None

    async def initialize(self):
        if self._pending or (self._shutdown_task is not None and not self._shutdown_task.done()):
            raise RuntimeError(tr(self.ctx.get_lang(), "closed"))
        settings = {**DEFAULTS, **self.plugin_cfg}
        lang = self.ctx.get_lang()
        ranges = {
            "request_timeout": (5, 180), "max_concurrency": (1, 16), "max_count": (5, 100),
            "subtitle_max_chars": (100, 60000), "subtitle_max_bytes": (1024, 10485760),
        }
        for key, (minimum, maximum) in ranges.items():
            if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
                raise ValueError(tr(lang, "invalid_config", parameter=key))
        for key in ("enable_like", "enable_comment", "enable_post"):
            if type(settings[key]) is not bool:
                raise ValueError(tr(lang, "invalid_config", parameter=key))
        if not isinstance(settings["default_adapter"], str) or len(settings["default_adapter"]) > 128:
            raise ValueError(tr(lang, "invalid_config", parameter="default_adapter"))
        settings["default_adapter"] = settings["default_adapter"].strip()
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings["max_concurrency"])
        self.videos = VideoService(settings["request_timeout"], settings["subtitle_max_bytes"], settings["subtitle_max_chars"])
        # Initialize runs before the manager binds tools; use its public component interface.
        manager = getattr(self.ctx, "plugin_mgr", None)
        if manager is not None:
            component = manager.get_plugin_components().get(PLUGIN_ID)
            if component is not None:
                for name in TOOLS:
                    meta = tool_schema(name, language(lang))
                    component.tools[name]["description"] = meta["description"]
                    if 'count' in meta['params']['properties']:
                        meta['params']['properties']['count']['maximum'] = settings['max_count']
                    component.tools[name]['parameters'] = meta['params']
        self._closing = False

    async def _drain(self):
        pending = list(self._pending)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def terminate(self):
        self._closing = True
        if self._shutdown_task is None or self._shutdown_task.done():
            self._shutdown_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._shutdown_task)

    def _failure(self, code, **values):
        return json.dumps({"ok": False, "code": code, "message": tr(self.ctx.get_lang(), code, **values)}, ensure_ascii=False)

    async def _execute(self, event, adapter_name, operation, *, action=None, account=True):
        if self._closing:
            return self._failure("closed")
        if action and not self.settings["enable_" + action]:
            return self._failure("disabled_action")

        async def run():
            async with self._semaphore:
                if self._closing:
                    raise PluginError("closed")
                adapter = accounts.select(self.ctx, event, adapter_name, self.settings["default_adapter"]) if account else None
                payload = await operation(adapter)
                attachments = []
                if isinstance(payload, tuple):
                    payload, attachments = payload
                result = {"ok": True, **payload}
                if adapter is not None:
                    result["adapter_name"] = adapter.info.name
                if action:
                    result["message"] = tr(self.ctx.get_lang(), "write_ready")
                serialized = json.dumps(result, ensure_ascii=False)
                return ToolResult(text=serialized, attachments=attachments) if attachments else serialized

        # Track an owned child task rather than cancelling the caller's message-processing task.
        task = asyncio.create_task(run())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        try:
            return await asyncio.wait_for(task, timeout=self.settings["request_timeout"])
        except PluginError as exc:
            return self._failure(exc.code, **exc.values)
        except (asyncio.TimeoutError, TimeoutError):
            return self._failure("write_timeout" if action else "timeout")
        except asyncio.CancelledError:
            raise
        except Exception:
            # SDK exceptions can contain credentials or private response bodies.
            return self._failure("write_failed" if action else "request_failed")
        finally:
            self._pending.discard(task)

    @register.tool(**tool_schema("bili_accounts"))
    async def bili_accounts(self, event=None):
        async def operation(_):
            return {
                "accounts": await accounts.describe(self.ctx),
                "actions": {key: self.settings["enable_" + key] for key in ("like", "comment", "post")},
                "limits": {key: self.settings[key] for key in ("max_count", "subtitle_max_chars", "subtitle_max_bytes")},
                "note": tr(self.ctx.get_lang(), "account_note"),
            }
        return await self._execute(event, None, operation, account=False)

    @register.tool(**tool_schema("bili_feed"))
    async def bili_feed(self, event=None, *, adapter_name=None, source="recommended", count=5, kinds=None, author_id=None, cursor=None):
        return await self._execute(event, adapter_name, lambda adapter: feed.browse(
            adapter, source=source, count=count, kinds=kinds, author_id=author_id, cursor=cursor, max_count=self.settings["max_count"],
        ))

    @register.tool(**tool_schema("bili_search"))
    async def bili_search(self, event=None, *, keyword, adapter_name=None, kind="video", count=5, author_id=None, cursor=None):
        return await self._execute(event, adapter_name, lambda adapter: feed.search(
            adapter, keyword=keyword, kind=kind, count=count, author_id=author_id, cursor=cursor, max_count=self.settings["max_count"],
        ))

    @register.tool(**tool_schema("bili_video_info"))
    async def bili_video_info(self, event=None, *, video, adapter_name=None, page=None):
        return await self._execute(event, adapter_name, lambda adapter: self.videos.info(adapter, video=video, page=page))

    @register.tool(**tool_schema("bili_video_subtitle"))
    async def bili_video_subtitle(self, event=None, *, video, adapter_name=None, page=None, language="", entry_offset=0, max_entries=20):
        async def operation(adapter):
            result, attachments = await self.videos.subtitle(
                adapter, video=video, page=page, language=language, entry_offset=entry_offset, max_entries=max_entries,
            )
            return {**result, "note": tr(self.ctx.get_lang(), "subtitle_ready")}, attachments
        return await self._execute(event, adapter_name, operation)

    @register.tool(**tool_schema("bili_like_video"))
    async def bili_like_video(self, event=None, *, video, liked, adapter_name=None):
        return await self._execute(event, adapter_name, lambda adapter: self.videos.like(adapter, video=video, liked=liked), action="like")

    @register.tool(**tool_schema("bili_comment"))
    async def bili_comment(self, event=None, *, target, content, adapter_name=None, root=None, parent=None):
        return await self._execute(event, adapter_name, lambda adapter: feed.comment(
            adapter, target=target, content=content, root=root, parent=parent,
        ), action="comment")

    @register.tool(**tool_schema("bili_post"))
    async def bili_post(self, event=None, *, content, adapter_name=None, options=None):
        return await self._execute(event, adapter_name, lambda adapter: feed.post(adapter, content=content, options=options), action="post")