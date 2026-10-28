"""Offline regression tests for account routing, tools and owned resources."""

import asyncio
import importlib
import inspect
import json
import string
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from core.adapter import FeedItem, FeedPage, FeedRef
from core.adapter.adapter_info import AdapterInfo
from core.adapter.context import AdapterContext
from core.adapter.src.bilibili.bilibili import BiliBiliAdapter
from core.agent.tool import ToolResult
from core.chat import MessageChain
from core.chat.message_elements import At, Emoji, Image, Text
from core.plugin import PluginManager, registry
from core.plugin import manager as manager_module

ROOT = Path(__file__).resolve().parents[1]
ID = "kira-ai-plugin-bili-enhance"
BV = "BV17x411w7KC"


@pytest.fixture
def modules(monkeypatch):
    for name in (
        "_plugin_classes", "_plugin_components", "_plugin_load_errors", "_plugin_manifests",
        "_plugin_module_dirs", "_plugin_module_paths", "_plugin_schemas", "_plugin_infos", "_module_to_plugin",
    ):
        monkeypatch.setattr(registry, name, {})
    package_name = "bili_enhance_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    monkeypatch.setitem(sys.modules, package_name, package)
    loaded = {}
    for name in ("main", "accounts", "content", "feed", "video", "user_videos", "i18n", "schemas", "frames", "danmaku"):
        loaded[name] = importlib.import_module(package_name + "." + name)
    yield SimpleNamespace(**loaded)
    for name in list(sys.modules):
        if name.startswith(package_name + "."):
            sys.modules.pop(name, None)


def adapter(name="bili-one", uid="42", **config):
    config = {"enable_comment_notifications": False, "enable_im": False, "dedeuserid": uid, **config}
    return BiliBiliAdapter(AdapterContext(AdapterInfo(True, name, name, "BiliBili", config=config), asyncio.Queue()))


def context(adapters, lang="en"):
    return SimpleNamespace(
        adapter_mgr=SimpleNamespace(get_adapters=lambda: adapters),
        get_lang=lambda: lang,
        plugin_mgr=SimpleNamespace(get_plugin_components=lambda: registry._plugin_components),
    )


async def plugin(modules, adapters, **cfg):
    instance = modules.main.BiliEnhancePlugin(context(adapters), cfg)
    await instance.initialize()
    return instance


def unpack(result):
    return json.loads(result.text if isinstance(result, ToolResult) else result)


def test_account_selection_precedence_and_explicit_failure(modules):
    first, second = adapter(), adapter("bili-two", "43")
    ctx = context({first.info.name: first, second.info.name: second})
    event = SimpleNamespace(session=SimpleNamespace(adapter_name=first.info.name))
    select = modules.accounts.select
    assert select(ctx, event, second.info.name, first.info.name) is second
    assert select(ctx, event, default=second.info.name) is first
    assert select(ctx, None, default=second.info.name) is second
    for name in ("missing", ""):
        with pytest.raises(modules.i18n.PluginError):
            select(ctx, event, name, first.info.name)
    with pytest.raises(modules.i18n.PluginError, match="ambiguous_account"):
        select(ctx, None)
    with pytest.raises(modules.i18n.PluginError, match="invalid_account"):
        select(ctx, None, default="missing")


def test_disabled_and_foreign_instances_are_not_candidates(modules):
    first = adapter()
    first.info.enabled = False
    ctx = context({"off": first, "qq": SimpleNamespace(info=SimpleNamespace(enabled=True))})
    assert modules.accounts.available(ctx) == {}
    with pytest.raises(modules.i18n.PluginError, match="no_account"):
        modules.accounts.select(ctx, None)
    second = adapter("sole")
    assert modules.accounts.select(context({"sole": second}), None) is second


@pytest.mark.asyncio
async def test_feed_pagination_filter_and_account_reload(modules):
    old = adapter()
    replacement = adapter()
    adapters = {old.info.name: old}
    instance = await plugin(modules, adapters)
    old.feed.get_feed = AsyncMock(return_value=FeedPage([], "opaque", True))
    first = unpack(await instance.bili_feed(source="user", author_id="42", kinds=["post"], count=2))
    assert first["items"] == [] and first["has_more"] and first["next_cursor"] == "opaque"
    adapters[old.info.name] = replacement
    replacement.feed.get_feed = AsyncMock(return_value=FeedPage([FeedItem(FeedRef("dynamic", "100"), "post", MessageChain([Text("hello")]), comment_target=FeedRef("draw", "101"))]))
    second = unpack(await instance.bili_feed(source="user", author_id="42", kinds=["post"], count=2, cursor="opaque"))
    query = replacement.feed.get_feed.await_args.args[0]
    assert query.cursor == "opaque" and query.author_id == "42" and query.kinds == ("post",)
    assert old.feed.get_feed.await_count == 1
    assert second["items"][0]["comment_target"] == {"resource_type": "draw", "id": "101"}


@pytest.mark.asyncio
async def test_search_maps_structured_query_and_preserves_empty_page(modules):
    account = adapter()
    account.feed.search_feed = AsyncMock(return_value=FeedPage([], "next", True))
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_search(keyword=" test ", kind="article", author_id="77", count=3, cursor="previous"))
    query = account.feed.search_feed.await_args.args[0]
    assert (query.keyword, query.kind, query.author_id, query.count, query.cursor) == ("test", "article", "77", 3, "previous")
    assert result["items"] == [] and result["has_more"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"count": True}, {"count": 21}, {"source": "user"}, {"source": "recommended", "kinds": ["post"]}, {"author_id": "0"}, {"cursor": ""}])
async def test_bad_feed_input_does_not_call_platform(modules, kwargs):
    account = adapter()
    account.feed.get_feed = AsyncMock()
    instance = await plugin(modules, {account.info.name: account})
    assert unpack(await instance.bili_feed(**kwargs))["code"] == "invalid_input"
    account.feed.get_feed.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_native_elements_and_reply_ids(modules):
    account = adapter()
    account.feed.send_comment = AsyncMock(return_value={"reply": {"rpid": 123}, "private": "not returned"})
    instance = await plugin(modules, {account.info.name: account})
    metadata = await account.feed.get_comment_metadata()
    emoji_id = next(iter(metadata.emojis))
    result = unpack(await instance.bili_comment(target={"resource_type": "draw", "id": "101"},
        content=[{"type": "text", "text": "hi"}, {"type": "emoji", "id": emoji_id}], root="10", parent="11"))
    args = account.feed.send_comment.await_args
    assert args.args[1] == FeedRef("draw", "101")
    assert args.kwargs == {"root": "10", "parent": "11"}
    assert isinstance(args.args[0][0], Text) and isinstance(args.args[0][1], Emoji)
    assert result["rpid"] == "123" and "private" not in result
    account.feed.send_comment.reset_mock()
    assert unpack(await instance.bili_comment(target={"resource_type": "video", "id": BV}, content=[{"type": "text", "text": "hi"}], parent="11"))["code"] == "invalid_input"
    account.feed.send_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_mentions_timezone_and_option_boundary(modules):
    account = adapter()
    account.feed.send_post = AsyncMock(return_value={"dynamic_id": 9007199254740993})
    instance = await plugin(modules, {account.info.name: account})
    content = [{"type": "text", "text": "hi"}, {"type": "at", "id": "42", "nickname": "user"}]
    result = unpack(await instance.bili_post(content=content, options={"send_time": "2026-10-06T12:00:00+08:00", "close_comment": True, "topic_id": "7"}))
    draft = account.feed.send_post.await_args.args[0]
    assert isinstance(draft.content[1], At)
    assert draft.extra["send_time"].utcoffset().total_seconds() == 28800
    assert draft.extra["topic_id"] == 7 and result["dynamic_id"] == "9007199254740993"
    account.feed.send_post.reset_mock()
    for options in ({"sessdata": "forbidden"}, {"send_time": "2026-10-06T12:00:00"}, {"close_comment": 1}):
        assert unpack(await instance.bili_post(content=content, options=options))["code"] == "invalid_input"
    account.feed.send_post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["like", "comment", "post"])
async def test_disabled_write_tools_do_not_resolve_accounts_or_call_platform(modules, action):
    instance = await plugin(modules, {}, **{"enable_" + action: False})
    result = await instance._execute(None, None, AsyncMock(), action=action)
    assert unpack(result)["code"] == "disabled_action"


@pytest.mark.asyncio
async def test_account_listing_does_not_expose_credentials_or_claim_readiness(modules):
    account = adapter(sessdata="secret-test-value", bili_jct="secret-jct")
    instance = await plugin(modules, {account.info.name: account})
    raw = await instance.bili_accounts()
    assert "secret-test-value" not in raw and "secret-jct" not in raw and "sessdata" not in raw
    result = unpack(raw)
    assert result["accounts"][0]["capabilities"] == ["feed"]
    assert result["accounts"][0]["uid"] == "42"
    assert "does not confirm" in result["note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cfg", [{"request_timeout": 0}, {"max_count": 4}, {"max_concurrency": True}, {"enable_post": "false"}, {"default_adapter": []}, {"subtitle_max_bytes": 0}])
async def test_invalid_config_is_rejected(modules, cfg):
    instance = modules.main.BiliEnhancePlugin(context({}), cfg)
    with pytest.raises(ValueError):
        await instance.initialize()
    assert instance._closing


@pytest.mark.asyncio
async def test_exception_redaction_and_write_timeout_are_not_retried(modules):
    account = adapter()
    instance = await plugin(modules, {account.info.name: account})
    failing = AsyncMock(side_effect=RuntimeError("sessdata=secret-test-value"))
    result = await instance._execute(None, None, failing)
    assert "secret-test-value" not in result and unpack(result)["code"] == "request_failed"
    timeout = AsyncMock(side_effect=TimeoutError())
    assert unpack(await instance._execute(None, None, timeout, action="post"))["code"] == "write_timeout"
    assert timeout.await_count == 1


@pytest.mark.asyncio
async def test_terminate_cancels_owned_tasks_and_keeps_shared_resources(modules):
    account = adapter()
    account.stop = AsyncMock()
    instance = await plugin(modules, {account.info.name: account}, max_concurrency=1)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async def waiting(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    running = asyncio.create_task(instance._execute(None, None, waiting))
    await started.wait()
    queued_operation = AsyncMock()
    queued = asyncio.create_task(instance._execute(None, None, queued_operation))
    await asyncio.sleep(0)
    await asyncio.gather(instance.terminate(), instance.terminate())
    results = await asyncio.gather(running, queued, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert cancelled.is_set() and not instance._pending
    queued_operation.assert_not_awaited()
    account.stop.assert_not_awaited()
    assert unpack(await instance.bili_accounts())["code"] == "closed"
    await instance.initialize()
    assert unpack(await instance.bili_accounts())["ok"]


@pytest.mark.asyncio
async def test_cancelling_shutdown_waiter_does_not_cancel_cleanup(modules):
    instance = await plugin(modules, {})
    started = asyncio.Event()
    release = asyncio.Event()
    async def drain():
        started.set()
        await release.wait()
    instance._drain = drain
    waiter = asyncio.create_task(instance.terminate())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not instance._shutdown_task.cancelled()
    release.set()
    await instance.terminate()


@pytest.mark.asyncio
async def test_video_details_and_likes_use_selected_account(modules, monkeypatch):
    account = adapter(sessdata="test-session", bili_jct="test-jct")
    handle = SimpleNamespace(get_info=AsyncMock(return_value={"aid": 1, "title": "Video", "pages": [
        {"cid": 100, "part": "one"}, {"cid": 200, "part": "two"},
    ]}), like=AsyncMock(return_value={}))
    factory = Mock(return_value=handle)
    monkeypatch.setattr(modules.video, "get_bilibili_client", Mock())
    monkeypatch.setattr(modules.video.sdk_video, "Video", factory)
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_video_info(video=f"https://www.bilibili.com/video/{BV}?p=2"))
    assert result["page"] == 2 and result["part"] == "two"
    assert factory.call_args.kwargs["credential"] is account.credential
    assert unpack(await instance.bili_like_video(video=BV, liked=False))["liked"] is False
    handle.like.assert_awaited_once_with(status=False)
    assert unpack(await instance.bili_like_video(video=BV, liked=1))["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_short_link_is_bounded_and_rejects_foreign_redirects(modules, monkeypatch):
    actual_client = httpx.AsyncClient
    requests = []
    def transport(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": f"https://www.bilibili.com/video/{BV}?p=2"})
    monkeypatch.setattr(modules.video.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(transport), **kwargs))
    assert await modules.video.resolve_video("https://b23.tv/example", 5) == (BV, 2)
    assert len(requests) == 1
    def bad_transport(request):
        return httpx.Response(302, headers={"Location": "https://example.com/video/" + BV})
    monkeypatch.setattr(modules.video.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(bad_transport), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_video"):
        await modules.video.resolve_video("https://b23.tv/example", 5)
    def looping(request):
        return httpx.Response(302, headers={"Location": str(request.url)})
    monkeypatch.setattr(modules.video.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(looping), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_video"):
        await modules.video.resolve_video("https://b23.tv/example", 5)


@pytest.mark.parametrize("value", ["https://example.com/video/" + BV, "https://user:pass@www.bilibili.com/video/" + BV, "http://b23.tv/example", "https://b23.tv:444/example", "https://b23.tv.evil.test/example"])
def test_url_validation_rejects_unsupported_targets(modules, value):
    with pytest.raises(modules.i18n.PluginError):
        modules.video.checked_url(value)


def test_subtitle_language_normalization_and_srt_timestamps(modules):
    tracks = [{"lan": "ai-zh"}, {"lan": "zh-Hant"}, {"lan": "en"}]
    assert modules.video.pick_track(tracks, "zh_CN")[0]["lan"] == "ai-zh"
    assert modules.video.pick_track(tracks, "zh-TW")[0]["lan"] == "zh-Hant"
    with pytest.raises(modules.i18n.PluginError):
        modules.video.pick_track(tracks, "ja")
    assert modules.video.srt_timestamp(3661.234) == "01:01:01,234"
    assert "00:00:01,000 --> 00:00:03,000" in modules.video.build_srt([(1, 3, "hello")])


@pytest.mark.asyncio
async def test_subtitle_parts_preview_and_unique_complete_attachments(modules, monkeypatch, tmp_path):
    account = adapter()
    handle = SimpleNamespace(get_info=AsyncMock(return_value={"pages": [{"cid": 100}, {"cid": 200}]}),
        get_subtitle=AsyncMock(return_value={"subtitles": [{"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/test"}]}))
    monkeypatch.setattr(modules.video, "get_bilibili_client", Mock())
    monkeypatch.setattr(modules.video.sdk_video, "Video", Mock(return_value=handle))
    monkeypatch.setattr(modules.video, "get_data_path", lambda: tmp_path)
    instance = await plugin(modules, {account.info.name: account}, subtitle_max_chars=100)
    instance.videos.download_subtitle = AsyncMock(return_value={"body": [
        {"from": 0, "to": 1, "content": "<i>" + "字" * 110 + "</i>"},
        {"from": 1, "to": 2, "content": "second"},
    ]})
    first = await instance.bili_video_subtitle(video=BV, page=2, language="zh-CN")
    assert isinstance(first, ToolResult)
    result = unpack(first)
    assert result["language"] == "ai-zh" and result["next_entry_offset"] == 1 and result["text_truncated"]
    assert len(result["entries"][0]["text"]) == 100
    handle.get_subtitle.assert_awaited_once_with(cid=200)
    saved = Path(first.attachments[0].file).read_text(encoding="utf-8")
    assert "字" * 110 in saved and "second" in saved and "<i>" not in saved
    second = await instance.bili_video_subtitle(video=BV, page=2, language="ai-zh", entry_offset=1)
    assert unpack(second)["entries"][0]["text"] == "second"
    assert unpack(second)["next_entry_offset"] is None
    assert first.attachments[0].file != second.attachments[0].file


@pytest.mark.asyncio
async def test_subtitle_download_checks_stream_size_and_redirect_hosts(modules, monkeypatch):
    actual_client = httpx.AsyncClient
    service = modules.video.VideoService(max_bytes=10)
    def large(request):
        return httpx.Response(200, content=b"a" * 11)
    monkeypatch.setattr(modules.video.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(large), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        await service.download_subtitle("https://aisubtitle.hdslb.com/test")
    def redirect(request):
        return httpx.Response(302, headers={"Location": "https://localhost/test"})
    monkeypatch.setattr(modules.video.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(redirect), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        await service.download_subtitle("https://aisubtitle.hdslb.com/test")


def test_translation_keys_placeholders_and_schemas_match(modules):
    for catalog in (modules.i18n.TEXTS, modules.i18n.TOOL_TEXTS, modules.i18n.PARAM_TEXTS):
        assert catalog["en"].keys() == catalog["zh"].keys()
        for key in catalog["en"] if catalog is modules.i18n.TEXTS else []:
            placeholders = lambda value: {name for _, name, _, _ in string.Formatter().parse(value) if name is not None}
            assert placeholders(catalog["en"][key]) == placeholders(catalog["zh"][key])
    assert modules.i18n.language("zh_TW") == "zh" and modules.i18n.language("unknown") == "en"
    for name in modules.schemas.TOOLS:
        properties = modules.schemas.tool_schema(name)["params"]["properties"]
        signature = inspect.signature(getattr(modules.main.BiliEnhancePlugin, name))
        assert set(properties) == set(signature.parameters) - {"self", "event"}
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["locales"]["zh"]["display_name"] == "B站增强"
    assert manifest["locales"]["en"]["display_name"] == "BiliBili Enhance"
    schema = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
    assert schema.keys() == modules.main.DEFAULTS.keys()
    for key, value in schema.items():
        assert value["default"] == modules.main.DEFAULTS[key]
        assert value["locales"]["zh"].keys() == value["locales"]["en"].keys()


@pytest.mark.asyncio
async def test_real_manager_load_register_localize_and_unload(modules, monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module, "PLUGIN_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(manager_module, "PLUGIN_STATE_FILE", tmp_path / "state.json")
    manager = PluginManager()
    manager.plugin_dir = ROOT.parent
    ctx = context({}, "zh-CN")
    ctx.plugin_mgr = manager
    ctx.tool_mgr = Mock()
    manager.ctx = ctx
    package_prefix = "plugins." + ROOT.name
    try:
        assert await manager.load_plugin_from_dir(ROOT, auto_install=False) == ID
        assert registry._plugin_infos[ID].status == "ready"
        assert set(manager.get_plugin_tools(ID)) == set(modules.schemas.TOOLS)
        assert ctx.tool_mgr.register_tool.call_count == len(modules.schemas.TOOLS)
        for call in ctx.tool_mgr.register_tool.call_args_list:
            assert call.kwargs["description"] == modules.i18n.TOOL_TEXTS["zh"][call.kwargs["name"]]
        instance = manager.plugin_instances[ID]
        assert unpack(await instance.bili_feed())["message"] == modules.i18n.TEXTS["zh"]["no_account"]
        await manager.terminate(ID)
        assert ID not in manager.plugin_instances
    finally:
        for name in list(sys.modules):
            if name == package_prefix or name.startswith(package_prefix + "."):
                sys.modules.pop(name, None)

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["comment", "post"])
async def test_images_are_real_media_elements_for_both_write_paths(modules, tmp_path, method):
    account = adapter()
    account.feed.send_comment = AsyncMock(return_value={"rpid": "1"})
    account.feed.send_post = AsyncMock(return_value={"dynamic_id": "2"})
    instance = await plugin(modules, {account.info.name: account})
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    content = [{"type": "image", "file": str(path)}, {"type": "image", "file": "https://i0.hdslb.com/test.png"}]
    if method == "comment":
        result = await instance.bili_comment(target={"resource_type": "video", "id": BV}, content=content)
        message = account.feed.send_comment.await_args.args[0]
    else:
        result = await instance.bili_post(content=content)
        message = account.feed.send_post.await_args.args[0].content
    assert unpack(result)["ok"]
    assert all(isinstance(element, Image) for element in message)
    assert message[0].file == str(path) and message[0].file_type == "path"
    assert message[1].file_type == "url"


@pytest.mark.asyncio
async def test_overall_timeout_cancels_request_and_clears_owned_tasks(modules):
    account = adapter()
    instance = await plugin(modules, {account.info.name: account})
    instance.settings["request_timeout"] = 0.02
    cancelled = asyncio.Event()
    async def waiting(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    result = unpack(await instance._execute(None, None, waiting))
    assert result["code"] == "timeout" and cancelled.is_set() and not instance._pending


@pytest.mark.asyncio
async def test_subtitle_without_tracks_returns_localized_failure(modules, monkeypatch, tmp_path):
    account = adapter()
    handle = SimpleNamespace(get_info=AsyncMock(return_value={"pages": [{"cid": 100}]}),
        get_subtitle=AsyncMock(return_value={"subtitles": []}))
    monkeypatch.setattr(modules.video, "get_bilibili_client", Mock())
    monkeypatch.setattr(modules.video.sdk_video, "Video", Mock(return_value=handle))
    monkeypatch.setattr(modules.video, "get_data_path", lambda: tmp_path)
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_video_subtitle(video=BV))
    assert result["code"] == "no_subtitle" and not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_subtitle_stream_limit_without_content_length(modules, monkeypatch):
    actual_client = httpx.AsyncClient
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"a" * 65536
            yield b"b" * 65536
    def response(request):
        return httpx.Response(200, stream=Stream())
    monkeypatch.setattr(modules.video.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(response), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        await modules.video.VideoService(max_bytes=70000).download_subtitle("https://aisubtitle.hdslb.com/test")


@pytest.mark.asyncio
async def test_serialized_schemas_validate_real_tool_arguments(modules):
    import jsonschema
    for lang in ("en", "zh"):
        for name in modules.schemas.TOOLS:
            jsonschema.Draft202012Validator.check_schema(modules.schemas.tool_schema(name, lang)["params"])
    jsonschema.validate({"video": BV, "liked": False}, modules.schemas.tool_schema("bili_like_video")["params"])
    jsonschema.validate({"content": [{"type": "at", "id": "42"}, {"type": "image", "file": "https://i0.hdslb.com/test.png"}]},
                        modules.schemas.tool_schema("bili_post")["params"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"target": {"resource_type": "video", "id": BV}, "content": [{"type": "at", "id": "42"}]},
                            modules.schemas.tool_schema("bili_comment")["params"])

@pytest.mark.asyncio
async def test_real_adapter_scan_accepts_dynamically_loaded_bilibili(modules, monkeypatch):
    from core.adapter import AdapterManager
    from core.adapter import adapter_registry as adapter_registry_module

    src = Path(adapter_registry_module.__file__).resolve().parent / "src"
    original_listdir = adapter_registry_module.os.listdir
    with monkeypatch.context() as scan_patch:
        for key in ("_registry", "_manifests", "_manifest_dirs", "_schemas"):
            scan_patch.setattr(AdapterManager, key, {})
        scan_patch.setattr(adapter_registry_module.os, "listdir", lambda path:
            ["bilibili"] if Path(path).resolve() == src else original_listdir(path))
        AdapterManager.scan_adapters(str(src))
        dynamic_class = AdapterManager.get_adapter_class("BiliBili")
    assert dynamic_class is not None and dynamic_class is not BiliBiliAdapter
    account = dynamic_class(AdapterContext(
        AdapterInfo(True, "test-id", "bili-new", "BiliBili", config={"enable_im": False}), asyncio.Queue(),
    ))
    assert not isinstance(account, BiliBiliAdapter)
    account.feed.get_feed = AsyncMock(return_value=FeedPage([], "next", True))
    instance = await plugin(modules, {"bili-new": account})
    listing = unpack(await instance.bili_accounts())
    assert [item["adapter_name"] for item in listing["accounts"]] == ["bili-new"]
    result = unpack(await instance.bili_feed(adapter_name="bili-new", source="user", author_id="12345678"))
    assert result["ok"] and result["adapter_name"] == "bili-new" and result["next_cursor"] == "next"
    assert account.feed.get_feed.await_args.args[0].author_id == "12345678"
    await instance.terminate()


def test_other_platform_base_adapter_is_not_a_bilibili_account(modules):
    account = adapter()
    account.info.platform = "QQ"
    assert modules.accounts.available(context({account.info.name: account})) == {}

@pytest.mark.asyncio
async def test_read_comments_uses_adapter_client_for_bv_and_bounds_public_result(modules):
    from bilibili_api.comment import CommentResourceType

    account = adapter()
    client = account.get_client()
    child = {"rpid": 12, "member": {"mid": 42, "uname": "reply author"},
             "content": {"message": "reply"}, "rcount": 0}
    first = {"rpid": 9007199254740993, "member": {"mid": 43, "uname": "author"},
             "content": {"message": "x" * 2100, "pictures": [{"img_src": "https://i0.hdslb.com/image.png"}]},
             "ctime": 123, "like": 7, "rcount": 4, "replies": [dict(child, rpid=12 + index) for index in range(4)],
             "private": "do not return"}
    client.get_comments_lazy = AsyncMock(return_value={"top_replies": [first], "replies": [first, dict(first, rpid=99)],
                                                      "cursor": {"is_end": False}})
    account.get_client = Mock(return_value=client)
    account.feed.get_feed = AsyncMock()
    instance = await plugin(modules, {account.info.name: account}, enable_comment=False)
    result = unpack(await instance.bili_comments(target={"resource_type": "video", "id": BV}, count=1))
    client.get_comments_lazy.assert_awaited_once_with(oid=170001, type_=CommentResourceType.VIDEO)
    account.get_client.assert_called_once_with()
    account.feed.get_feed.assert_not_awaited()
    assert result["ok"] and result["first_batch_only"] and result["has_more"] and result["truncated"]
    assert "do not return" not in json.dumps(result)
    item = result["comments"][0]
    assert item["comment_id"] == "9007199254740993" and item["root_comment_id"] == item["comment_id"]
    assert item["pinned"] and len(item["text"]) == 2000 and item["text_truncated"]
    assert len(item["replies"]) == 3 and not item["replies_complete"]
    assert item["replies"][0]["root_comment_id"] == item["comment_id"]
    assert item["replies"][0]["parent_comment_id"] == item["comment_id"]
    assert item["images"] == ["https://i0.hdslb.com/image.png"]


@pytest.mark.asyncio
async def test_read_dynamic_comments_resolves_actual_resource_through_client(modules):
    from bilibili_api.comment import CommentResourceType

    account = adapter()
    client = account.get_client()
    client.get_dynamic_info = AsyncMock(return_value={"item": {"id_str": "100", "type": "DYNAMIC_TYPE_DRAW",
        "basic": {"comment_type": 11, "comment_id_str": "200", "rid_str": "999"}}})
    client.get_comments_lazy = AsyncMock(return_value={"replies": [], "cursor": {"is_end": True}})
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_comments(target={"resource_type": "dynamic", "id": "100"}))
    client.get_dynamic_info.assert_awaited_once_with(100)
    client.get_comments_lazy.assert_awaited_once_with(oid=200, type_=CommentResourceType.DYNAMIC_DRAW)
    assert result["target"] == {"resource_type": "draw", "id": "200"}
    assert result["comments"] == [] and result["has_more"] is False


@pytest.mark.asyncio
async def test_read_dynamic_without_comment_resource_does_not_fetch_comments(modules):
    account = adapter()
    client = account.get_client()
    client.get_dynamic_info = AsyncMock(return_value={"item": {}})
    client.get_comments_lazy = AsyncMock()
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_comments(target={"resource_type": "dynamic", "id": "100"}))
    assert result["code"] == "no_comment_target"
    client.get_comments_lazy.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_numeric_resource_deduplicates_comments_and_can_omit_replies(modules):
    from bilibili_api.comment import CommentResourceType

    account = adapter()
    client = account.get_client()
    first = {"rpid": 1, "content": {"message": "top"}, "rcount": 2,
             "replies": [{"rpid": 2, "root": 1, "parent": 1, "content": {"message": "child"}}]}
    client.get_comments_lazy = AsyncMock(return_value={"top_replies": [first], "replies": [first, {"rpid": 3}],
                                                      "cursor": {"is_end": True}})
    client.get_dynamic_info = AsyncMock()
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_comments(target={"resource_type": "video", "id": "170001"}, include_replies=False))
    client.get_comments_lazy.assert_awaited_once_with(oid=170001, type_=CommentResourceType.VIDEO)
    client.get_dynamic_info.assert_not_awaited()
    assert [item["comment_id"] for item in result["comments"]] == ["1", "3"]
    assert result["comments"][0]["replies"] == [] and result["comments"][0]["replies_complete"] is False
    assert result["comments"][1]["replies_complete"] is None
    assert result["has_more"] is False and not result["truncated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"count": True}, {"count": 21}, {"include_replies": 1},
    {"target": {"resource_type": "video", "id": "bad"}}, {"target": {"resource_type": "invalid", "id": "1"}}])
async def test_bad_comment_read_input_does_not_call_client(modules, kwargs):
    account = adapter()
    client = account.get_client()
    client.get_comments_lazy = AsyncMock()
    client.get_dynamic_info = AsyncMock()
    instance = await plugin(modules, {account.info.name: account})
    arguments = {"target": {"resource_type": "video", "id": BV}, **kwargs}
    result = unpack(await instance.bili_comments(**arguments))
    assert result["code"] == "invalid_input"
    client.get_comments_lazy.assert_not_awaited()
    client.get_dynamic_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_read_client_error_is_redacted(modules):
    account = adapter()
    client = account.get_client()
    client.get_comments_lazy = AsyncMock(side_effect=RuntimeError("sessdata=private-test-value"))
    instance = await plugin(modules, {account.info.name: account})
    result = await instance.bili_comments(target={"resource_type": "video", "id": BV})
    assert unpack(result)["code"] == "request_failed" and "private-test-value" not in result


@pytest.mark.asyncio
async def test_comment_read_empty_batch_and_unknown_cursor_are_valid(modules):
    account = adapter()
    client = account.get_client()
    client.get_comments_lazy = AsyncMock(return_value={"replies": None})
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_comments(target={"resource_type": "video", "id": BV}))
    assert result["ok"] and result["comments"] == [] and result["has_more"] is None

@pytest.mark.asyncio
async def test_user_uploads_borrow_selected_credential_and_map_public_fields(modules, monkeypatch):
    first, selected = adapter(), adapter("bili-two", "43", sessdata="private-cookie")
    handle = SimpleNamespace(get_videos=AsyncMock(return_value={
        "list": {"vlist": [
            {"bvid": BV, "aid": 170001, "author": "Uploader", "title": "<em>Video</em>",
             "description": "x" * 3100, "created": 1700000000, "length": "01:02",
             "pic": "//i0.hdslb.com/cover.jpg", "play": 100, "video_review": 12,
             "private": "private-body"},
            {"aid": 170002, "title": "Second", "created": 1700000001, "length": "00:30"},
            {"aid": 170003, "title": "Beyond requested size"},
        ]}, "page": {"count": 5}, "private": "private-body",
    }))
    factory = Mock(return_value=handle)
    transport = Mock()
    monkeypatch.setattr(modules.user_videos.sdk_user, "User", factory)
    monkeypatch.setattr(modules.user_videos, "get_bilibili_client", transport)
    instance = await plugin(modules, {first.info.name: first, selected.info.name: selected})
    raw = await instance.bili_user_videos(author_id="9007199254740993", adapter_name=selected.info.name,
                                         page=2, count=2, keyword="  test  ", order="view")
    result = unpack(raw)
    factory.assert_called_once_with(uid=9007199254740993, credential=selected.credential)
    transport.assert_called_once_with()
    handle.get_videos.assert_awaited_once_with(pn=2, ps=2, keyword="test", order=modules.user_videos.sdk_user.VideoOrder.VIEW)
    assert result["ok"] and result["adapter_name"] == selected.info.name
    assert result["author_id"] == "9007199254740993" and result["next_page"] == 3
    assert result["total"] == 5 and result["has_more"] and len(result["items"]) == 2
    item = result["items"][0]
    assert item["ref"] == item["comment_target"] == {"resource_type": "video", "id": BV}
    assert item["author"] == {"id": "9007199254740993", "name": "Uploader"}
    assert item["title"] == "Video" and item["duration"] == 62
    assert item["published_at"] == "2023-11-14T22:13:20+00:00"
    assert len(item["text"]) == 3000 and item["cover_url"] == "https://i0.hdslb.com/cover.jpg"
    assert item["stats"] == {"view": 100, "danmaku": 12}
    assert "private-cookie" not in raw and "private-body" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("order,enum", [("pubdate", "PUBDATE"), ("view", "VIEW"), ("favorite", "FAVORITE")])
async def test_user_upload_sorting_and_empty_page(modules, monkeypatch, order, enum):
    account = adapter()
    handle = SimpleNamespace(get_videos=AsyncMock(return_value={"list": {"vlist": None}, "page": {"count": 0}}))
    monkeypatch.setattr(modules.user_videos.sdk_user, "User", Mock(return_value=handle))
    monkeypatch.setattr(modules.user_videos, "get_bilibili_client", Mock())
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_user_videos(author_id="42", order=order))
    handle.get_videos.assert_awaited_once_with(pn=1, ps=5, keyword="", order=getattr(modules.user_videos.sdk_user.VideoOrder, enum))
    assert result["ok"] and result["items"] == [] and result["total"] == 0
    assert result["has_more"] is False and result["next_page"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,max_count", [
    ({"author_id": "0"}, 20), ({"author_id": True}, 20),
    ({"page": 0}, 20), ({"page": True}, 20), ({"page": 10001}, 20),
    ({"count": 0}, 20), ({"count": True}, 20), ({"count": 21}, 20), ({"count": 31}, 100),
    ({"keyword": None}, 20), ({"keyword": "x" * 257}, 20),
    ({"order": "random"}, 20), ({"order": []}, 20),
])
async def test_invalid_user_upload_parameters_do_not_call_sdk(modules, monkeypatch, arguments, max_count):
    account = adapter()
    factory, transport = Mock(), Mock()
    monkeypatch.setattr(modules.user_videos.sdk_user, "User", factory)
    monkeypatch.setattr(modules.user_videos, "get_bilibili_client", transport)
    instance = await plugin(modules, {account.info.name: account}, max_count=max_count)
    result = unpack(await instance.bili_user_videos(**{"author_id": "42", **arguments}))
    assert result["code"] == "invalid_input"
    factory.assert_not_called()
    transport.assert_not_called()


@pytest.mark.asyncio
async def test_user_upload_unknown_total_and_sdk_failure_redaction(modules, monkeypatch):
    account = adapter()
    handle = SimpleNamespace(get_videos=AsyncMock(return_value={"list": {"vlist": []}}))
    monkeypatch.setattr(modules.user_videos.sdk_user, "User", Mock(return_value=handle))
    monkeypatch.setattr(modules.user_videos, "get_bilibili_client", Mock())
    instance = await plugin(modules, {account.info.name: account})
    result = unpack(await instance.bili_user_videos(author_id="42"))
    assert result["ok"] and result["total"] is None and result["has_more"] is None
    handle.get_videos.side_effect = RuntimeError("SESSDATA=private-sdk-exception")
    raw = await instance.bili_user_videos(author_id="42")
    assert unpack(raw)["code"] == "request_failed" and "private-sdk-exception" not in raw


def test_user_upload_schema_has_distinct_page_and_keyword_semantics(modules):
    import jsonschema

    for lang in ("en", "zh"):
        schema = modules.schemas.tool_schema("bili_user_videos", lang)["params"]
        jsonschema.validate({"author_id": "42", "keyword": "", "page": 2, "count": 30}, schema)
        assert schema["required"] == ["author_id"]
        assert schema["properties"]["page"]["description"] == modules.i18n.PARAM_TEXTS[lang]["user_video_page"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"author_id": "42", "count": 31}, schema)


def storyboard_sheet(colors, width=8, height=6):
    import io
    from PIL import Image as PillowImage
    sheet = PillowImage.new("RGB", (width * 2, height * 2))
    for index, color in enumerate(colors):
        sheet.paste(color, ((index % 2) * width, (index // 2) * height,
                            (index % 2 + 1) * width, (index // 2 + 1) * height))
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


def storyboard_handle(modules, monkeypatch, snapshot=None):
    handle = SimpleNamespace(
        get_info=AsyncMock(return_value={"pages": [
            {"cid": 100, "duration": 30}, {"cid": 200, "duration": 100},
        ]}),
        get_video_snapshot=AsyncMock(return_value=snapshot or {
            "index": list(range(0, 80, 10)), "image": ["https://i0.hdslb.com/one", "https://i0.hdslb.com/two"],
            "img_x_size": 8, "img_y_size": 6,
        }),
    )
    monkeypatch.setattr(modules.video, "get_bilibili_client", Mock())
    factory = Mock(return_value=handle)
    monkeypatch.setattr(modules.video.sdk_video, "Video", factory)
    return handle, factory


@pytest.mark.asyncio
async def test_frames_crop_multiple_sheets_use_part_and_deliver_native_media(modules, monkeypatch, tmp_path):
    import io
    from PIL import Image as PillowImage
    from core.agent.agent_executor import AgentExecutor
    from core.utils import media_refs
    first, second = adapter(), adapter("second", "43")
    handle, factory = storyboard_handle(modules, monkeypatch)
    sheets = [storyboard_sheet(["red", "green", "blue", "yellow"]), storyboard_sheet(["purple", "white", "black", "orange"])]
    downloader = AsyncMock(side_effect=lambda url, *args: sheets[0 if url.endswith("one") else 1])
    monkeypatch.setattr(modules.frames, "download", downloader)
    monkeypatch.setattr(media_refs, "get_data_path", lambda: tmp_path)
    instance = await plugin(modules, {first.info.name: first, second.info.name: second})
    instance.ctx.get_session_capabilities = AsyncMock(return_value={"image_recognition": {"mode": "native"}})
    result = await instance.bili_frames(SimpleNamespace(sid="session"), video=f"https://www.bilibili.com/video/{BV}?p=2",
                                        adapter_name="second", at=[0, 40, 60])
    payload = unpack(result)
    assert payload["adapter_name"] == "second" and factory.call_args.kwargs["credential"] is second.credential
    assert payload["page"] == 2 and payload["covered_sec"] == 70
    assert payload["image_attached"] and len(result.media_refs) == len(result.attachments) == 1
    assert [item["got_sec"] for item in payload["frames"]] == [0, 40, 60]
    handle.get_video_snapshot.assert_awaited_once_with(cid=200, json_index=True, pvideo=False)
    assert downloader.await_count == 2
    resolved = await media_refs.resolve_media_reference(result.media_refs[0])
    assert resolved["image_url"]["url"].startswith("data:image/jpeg;base64,")
    raw = await result.attachments[0].to_base64()
    import base64
    image = PillowImage.open(io.BytesIO(base64.b64decode(raw)))
    assert image.size == (payload["width"], payload["height"])
    assert payload["columns"] == 3 and payload["rows"] == 1
    assert image.getpixel((88, 37))[0] > 220
    assert image.getpixel((256, 37))[0] > 80 and image.getpixel((256, 37))[2] > 80
    assert max(image.getpixel((424, 37))) < 30
    assert all((tmp_path / ref["path"]).is_file() for ref in result.media_refs)
    content = result.build_content(result.text)
    messages = AgentExecutor._build_tool_messages([{"role": "tool", "name": "bili_frames", "tool_call_id": "frames", "content": content}])
    assert messages[0].role == "tool" and messages[1].role == "user"
    assert sum(part.get("type") == "kira_image_ref" for part in messages[1].content) == 1


@pytest.mark.asyncio
async def test_frames_even_sampling_and_vlm_descriptions(modules, monkeypatch):
    account = adapter()
    storyboard_handle(modules, monkeypatch)
    monkeypatch.setattr(modules.frames, "download", AsyncMock(return_value=storyboard_sheet(["red"] * 4)))
    describe = AsyncMock(return_value="A red frame")
    monkeypatch.setattr(modules.frames, "desc_img", describe)
    instance = await plugin(modules, {account.info.name: account})
    instance.ctx.get_session_capabilities = AsyncMock(return_value={"image_recognition": {"mode": "vlm_description"}})
    instance.ctx.provider_mgr = SimpleNamespace(get_default_vlm=Mock(return_value=object()))
    result = await instance.bili_frames(SimpleNamespace(sid="session"), video=BV, page=2)
    payload = unpack(result)
    assert [item["want_sec"] for item in payload["frames"]] == [17.5, 35, 52.5]
    assert [item["got_sec"] for item in payload["frames"]] == [20, 30, 50]
    assert payload["image_described"] and not payload["image_attached"] and not result.media_refs
    assert describe.await_count == 1
    assert payload["description"] == "A red frame"
    assert not any("description" in item for item in payload["frames"])
    assert len(result.attachments) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("recognition", [{"enabled": False, "mode": "native"}, {"mode": "vlm_description"}])
async def test_frames_disabled_or_unavailable_vision_is_explicit(modules, monkeypatch, recognition):
    account = adapter()
    storyboard_handle(modules, monkeypatch)
    monkeypatch.setattr(modules.frames, "download", AsyncMock(return_value=storyboard_sheet(["red"] * 4)))
    instance = await plugin(modules, {account.info.name: account})
    instance.ctx.get_session_capabilities = AsyncMock(return_value={"image_recognition": recognition})
    result = await instance.bili_frames(SimpleNamespace(sid="session"), video=BV, num=1)
    payload = unpack(result)
    assert payload["ok"] and not payload["image_attached"] and payload["image_skip_reason"]
    assert not result.media_refs and len(result.attachments) == 1


@pytest.mark.asyncio
async def test_frames_binary_index_fallback_and_millisecond_units(modules, monkeypatch):
    import struct
    account = adapter()
    handle, _ = storyboard_handle(modules, monkeypatch, {
        "pvdata": "https://i0.hdslb.com/index", "image": ["https://i0.hdslb.com/sheet"],
        "img_x_size": 8, "img_y_size": 6,
    })
    downloader = AsyncMock(side_effect=lambda url, *args: struct.pack(">3H", 0, 30, 60) if url.endswith("index") else storyboard_sheet(["red"] * 4))
    monkeypatch.setattr(modules.frames, "download", downloader)
    payload, images = await modules.frames.read(modules.video.VideoService(), account, video=BV, page=2, at=[29])
    assert payload["frames"][0]["got_sec"] == 30 and len(images) == 1
    assert modules.frames.snapshot_entries({"index": [0, 30000, 60000]}, 100) == [(0, 0), (30, 1), (60, 2)]
    assert modules.frames.snapshot_entries({"index": [0, 10000, 22163]}, 22163)[-1] == (22163, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"num": True}, {"num": 0}, {"num": 7}, {"at": []}, {"at": [-1]},
    {"at": [float("nan")]}, {"at": [float("inf")]}, {"at": [True]}, {"at": "30"}, {"at": [1] * 7}])
async def test_frames_invalid_arguments_fail_before_platform_request(modules, arguments):
    account = adapter()
    instance = await plugin(modules, {account.info.name: account})
    instance.videos.part = AsyncMock()
    assert unpack(await instance.bili_frames(video=BV, **arguments))["code"] == "invalid_input"
    instance.videos.part.assert_not_awaited()


@pytest.mark.asyncio
async def test_frames_empty_storyboard_out_of_coverage_partial_failure_and_redaction(modules, monkeypatch):
    account = adapter()
    handle, _ = storyboard_handle(modules, monkeypatch)
    instance = await plugin(modules, {account.info.name: account})
    assert unpack(await instance.bili_frames(video=BV, page=2, at=[90]))["code"] == "invalid_input"
    handle.get_video_snapshot.return_value = {"image": [], "index": []}
    assert unpack(await instance.bili_frames(video=BV))["code"] == "no_frames"
    handle.get_video_snapshot.return_value = {"image": ["https://i0.hdslb.com/one"], "index": list(range(0, 80, 10)),
                                             "img_x_size": 8, "img_y_size": 6}
    monkeypatch.setattr(modules.frames, "download", AsyncMock(return_value=storyboard_sheet(["red"] * 4)))
    payload, images = await modules.frames.read(instance.videos, account, video=BV, page=2, at=[0, 60])
    assert len(images) == 1 and payload["failed"] == [{"index": 1, "code": "invalid_download"}]
    handle.get_video_snapshot.side_effect = RuntimeError("sessdata=do-not-expose")
    result = await instance.bili_frames(video=BV)
    assert "do-not-expose" not in result and unpack(result)["code"] == "request_failed"


@pytest.mark.asyncio
async def test_storyboard_download_bounds_redirects_and_sends_no_credentials(modules, monkeypatch):
    actual_client = httpx.AsyncClient
    seen = []
    def transport(request):
        seen.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(302, headers={"Location": "https://localhost/private"})
    monkeypatch.setattr(modules.frames.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(transport), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        await modules.frames.download("http://i0.hdslb.com/sheet", 5)
    assert len(seen) == 1 and seen[0].url.scheme == "https"
    def large(request):
        return httpx.Response(200, content=b"a" * 11)
    monkeypatch.setattr(modules.frames.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(large), **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        await modules.frames.download("https://i0.hdslb.com/sheet", 5, limit=10)
    for url in ["https://localhost/a", "https://hdslb.com.evil.test/a", "https://user:pass@i0.hdslb.com/a", "https://i0.hdslb.com:444/a"]:
        with pytest.raises(modules.i18n.PluginError):
            modules.frames.image_url(url)
    monkeypatch.setattr(modules.frames, "MAX_SHEET_PIXELS", 1)
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        modules.frames.crop_tile(storyboard_sheet(["red"] * 4), 0, 8, 6)


@pytest.mark.asyncio
async def test_danmaku_bounds_segment_sorts_caps_and_uses_selected_credential(modules, monkeypatch):
    from bilibili_api.utils.danmaku import Danmaku
    first, second = adapter(), adapter("second", "43")
    handle, factory = storyboard_handle(modules, monkeypatch)
    handle.get_info.return_value = {"pages": [{"cid": 100, "duration": 20}, {"cid": 200, "duration": 800}]}
    handle.get_danmakus = AsyncMock(return_value=[
        Danmaku("later", dm_time=370, id_=9007199254740993),
        Danmaku("字" * 600, dm_time=361, id_=2), Danmaku("last", dm_time=390, id_=3),
    ])
    instance = await plugin(modules, {first.info.name: first, second.info.name: second})
    result = unpack(await instance.bili_danmaku(video=f"https://www.bilibili.com/video/{BV}?p=2", adapter_name="second", segment=2, count=2))
    handle.get_danmakus.assert_awaited_once_with(cid=200, from_seg=1, to_seg=1)
    assert factory.call_args.kwargs["credential"] is second.credential
    assert result["segment_count"] == 3 and result["next_segment"] == 3 and result["truncated"]
    assert result["segment_start_sec"] == 360 and result["segment_end_sec"] == 720
    assert result["items"][1]["id"] == "9007199254740993" and len(result["items"][0]["text"]) == 500
    assert result["items"][0]["text_truncated"] and result["page"] == 2
    handle.get_danmakus.return_value = []
    result = unpack(await instance.bili_danmaku(video=BV, adapter_name="second", page=2, segment=3))
    assert result["items"] == [] and result["next_segment"] is None and result["segment_end_sec"] == 800
    assert unpack(await instance.bili_danmaku(video=BV, adapter_name="second", page=2, segment=4))["code"] == "invalid_input"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"segment": 0}, {"segment": True}, {"segment": 10001}, {"count": True}, {"count": 0}, {"count": 21}])
async def test_danmaku_invalid_input_does_not_call_platform(modules, arguments):
    account = adapter()
    instance = await plugin(modules, {account.info.name: account})
    instance.videos.part = AsyncMock()
    assert unpack(await instance.bili_danmaku(video=BV, **arguments))["code"] == "invalid_input"
    instance.videos.part.assert_not_awaited()


@pytest.mark.asyncio
async def test_danmaku_failures_are_redacted_and_timeout_does_not_retry(modules, monkeypatch):
    account = adapter()
    handle, _ = storyboard_handle(modules, monkeypatch)
    handle.get_danmakus = AsyncMock(side_effect=RuntimeError("sessdata=private"))
    instance = await plugin(modules, {account.info.name: account})
    result = await instance.bili_danmaku(video=BV)
    assert "private" not in result and unpack(result)["code"] == "request_failed"
    handle.get_danmakus.side_effect = TimeoutError()
    assert unpack(await instance.bili_danmaku(video=BV))["code"] == "timeout"
    assert handle.get_danmakus.await_count == 2


@pytest.mark.asyncio
async def test_storyboard_chunked_limit_without_content_length(modules, monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"a" * 8
            yield b"b" * 8
    actual_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    monkeypatch.setattr(modules.frames.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=transport, **kwargs))
    with pytest.raises(modules.i18n.PluginError, match="invalid_download"):
        await modules.frames.download("https://i0.hdslb.com/sheet", 5, limit=10)


@pytest.mark.asyncio
async def test_new_tools_cancel_owned_work_on_unload(modules, monkeypatch):
    account = adapter()
    handle, _ = storyboard_handle(modules, monkeypatch)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async def waiting(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    handle.get_video_snapshot = waiting
    instance = await plugin(modules, {account.info.name: account})
    running = asyncio.create_task(instance.bili_frames(video=BV))
    await started.wait()
    await instance.terminate()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert cancelled.is_set() and not instance._pending


@pytest.mark.asyncio
async def test_danmaku_default_works_with_minimum_config_and_unknown_duration(modules, monkeypatch):
    from bilibili_api.utils.danmaku import Danmaku
    account = adapter()
    handle, _ = storyboard_handle(modules, monkeypatch)
    handle.get_info.return_value = {"pages": [{"cid": 100}]}
    handle.get_danmakus = AsyncMock(return_value=[Danmaku(str(i), dm_time=i) for i in range(8)])
    instance = await plugin(modules, {account.info.name: account}, max_count=5)
    result = unpack(await instance.bili_danmaku(video=BV))
    assert result["ok"] and len(result["items"]) == 5 and result["truncated"]
    assert result["segment_count"] is None and result["next_segment"] is None
    assert unpack(await instance.bili_danmaku(video=BV, count=6))["code"] == "invalid_input"


def test_new_tool_schemas_validate_parameters_and_limits(modules):
    import jsonschema
    for name, valid in [("bili_frames", {"video": BV, "page": 2, "at": [0, 30.5]}),
                        ("bili_danmaku", {"video": BV, "segment": 2, "count": 20})]:
        jsonschema.validate(valid, modules.schemas.tool_schema(name)["params"])
    for arguments in [{"num": 7}, {"at": [-1]}, {"at": []}, {"at": [1] * 7}, {"at": [True]}]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"video": BV, **arguments}, modules.schemas.tool_schema("bili_frames")["params"])
    for arguments in [{"segment": 0}, {"count": 101}]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"video": BV, **arguments}, modules.schemas.tool_schema("bili_danmaku")["params"])


@pytest.mark.parametrize("count", range(1, 7))
def test_frame_collage_layout_preserves_order_and_actual_time_labels(modules, monkeypatch, count):
    import io
    from PIL import Image as PillowImage
    colors = ["red", "green", "blue", "yellow", "purple", "white"][:count]
    tiles = []
    for color in colors:
        with PillowImage.new("RGB", (80, 40), color) as tile:
            output = io.BytesIO()
            tile.save(output, format="PNG")
            tiles.append(output.getvalue())
    frames = [{"index": i, "want_sec": i * 20, "got_sec": i * 20 + 0.125} for i in range(count)]
    labels = []
    original_draw = modules.frames.ImageDraw.Draw
    def track_draw(image):
        draw = original_draw(image)
        original_text = draw.text
        def text(position, label, **kwargs):
            labels.append(label)
            return original_text(position, label, **kwargs)
        draw.text = text
        return draw
    monkeypatch.setattr(modules.frames.ImageDraw, "Draw", track_draw)
    raw, width, height, columns, rows = modules.frames.compose_frames(frames, tiles, 80, 40)
    assert columns == min(3, count) and rows == (count + columns - 1) // columns
    assert labels == [f"#{i + 1} {i * 20 + 0.125:.3f}s" for i in range(count)]
    with PillowImage.open(io.BytesIO(raw)) as collage:
        assert collage.format == "JPEG" and collage.size == (width, height)
        from PIL import ImageColor
        for i, color in enumerate(colors):
            x, y = 88 + i % columns * 168, 54 + i // columns * 74
            expected = ImageColor.getrgb(color)
            actual = collage.getpixel((x, y))
            assert max(abs(a - b) for a, b in zip(actual, expected)) < 10
        assert collage.crop((8, 8, 168, 34)).getextrema()[0][1] > 180


def test_frame_collage_limits_large_tiles_and_keeps_failed_frame_number_gaps(modules, monkeypatch):
    import io
    from PIL import Image as PillowImage
    with PillowImage.new("RGB", (2048, 1024), "red") as tile:
        output = io.BytesIO()
        tile.save(output, format="PNG")
    frames = [{"index": i, "got_sec": i * 30} for i in [0, 2, 3, 4, 5]]
    labels = []
    original_draw = modules.frames.ImageDraw.Draw
    def track_draw(image):
        draw = original_draw(image)
        original_text = draw.text
        def text(position, label, **kwargs):
            labels.append(label)
            return original_text(position, label, **kwargs)
        draw.text = text
        return draw
    monkeypatch.setattr(modules.frames.ImageDraw, "Draw", track_draw)
    raw, width, height, columns, rows = modules.frames.compose_frames(frames, [output.getvalue()] * 5, 2048, 1024)
    assert (columns, rows) == (3, 2) and width < 2000 and height < 1400
    assert width * height < modules.frames.MAX_SHEET_PIXELS
    assert labels[1] == "#3 60.000s"
    with PillowImage.open(io.BytesIO(raw)) as collage:
        assert collage.size == (width, height)


@pytest.mark.asyncio
async def test_frame_collage_vlm_description_is_bounded_and_processed_once(modules, monkeypatch):
    account = adapter()
    storyboard_handle(modules, monkeypatch)
    monkeypatch.setattr(modules.frames, "download", AsyncMock(return_value=storyboard_sheet(["red"] * 4)))
    describe = AsyncMock(return_value="x" * 7000)
    monkeypatch.setattr(modules.frames, "desc_img", describe)
    instance = await plugin(modules, {account.info.name: account})
    instance.ctx.get_session_capabilities = AsyncMock(return_value={"image_recognition": {"mode": "vlm_description"}})
    instance.ctx.provider_mgr = SimpleNamespace(get_default_vlm=Mock(return_value=object()))
    result = await instance.bili_frames(SimpleNamespace(sid="session"), video=BV, page=2, num=6)
    assert len(result.attachments) == 1 and len(unpack(result)["frames"]) == 6
    assert len(unpack(result)["description"]) == 6000
    describe.assert_awaited_once()
    assert describe.await_args.kwargs["image"] is result.attachments[0]
