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
    for name in ("main", "accounts", "content", "feed", "video", "i18n", "schemas"):
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
        assert ctx.tool_mgr.register_tool.call_count == 8
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