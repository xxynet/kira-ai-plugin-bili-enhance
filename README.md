# BiliBili Enhance

[中文说明](README.zh.md)

BiliBili Enhance (`kira-ai-plugin-bili-enhance`) gives KiraAI ten LLM-native tools for browsing Bilibili, reading video details and CC subtitles, liking videos, reading/posting comments and publishing dynamics. It uses accounts already configured in the built-in Bilibili adapter. No separate cookies, commands or background polling are required.

## Setup

1. Use a KiraAI build with the current typed Feed capabilities and message-format metadata APIs. Python 3.10+ is required.
2. Place this directory under `data/plugins/kira-ai-plugin-bili-enhance/` and load/enable the plugin through KiraAI's plugin management.
3. Configure and enable a built-in Bilibili adapter account. Configure login credentials there; writing requires the account's `sessdata` and `bili_jct`. Account configuration and authentication remain the adapter's responsibility.
4. Ask the AI to list Bilibili accounts or use a tool. Optionally set `default_adapter` to a configured instance name.

This plugin does not start, stop or log in adapters. An instance appearing in `bili_accounts` means it is enabled and loaded, not that login or the connection is verified. A feed-only adapter can set `enable_im=false` in its adapter configuration. This plugin uses the existing application dependencies: `bilibili-api-python>=17.4.2` and `httpx>=0.28.1`.

## Tools

| Tool | Behavior |
| --- | --- |
| `bili_accounts` | List loaded account names, UIDs, capabilities, native emoji IDs, comment/post formats, action switches and result limits. |
| `bili_user_videos` | List a user's space uploads by UID, with page numbers, optional keywords and sorting. |
| `bili_feed` | Browse `recommended` videos, `following` dynamics or `user` dynamics (requires `author_id`). Optional `kinds`, `count`, `cursor` and author filtering. |
| `bili_search` | Search a nonempty `keyword` with `kind=video` or `article`; optional author filter and opaque pagination cursor. |
| `bili_video_info` | Read a BV ID, a Bilibili video URL or a `https://b23.tv/` short link. `page` selects a one-based part; otherwise use URL `p` or 1. |
| `bili_video_subtitle` | Read a part's CC subtitles, select `language`, preview with `entry_offset` and `max_entries`, and return the complete selected track as a unique SRT attachment. |
| `bili_like_video` | Explicitly set `liked=true` or `liked=false`. No toggle or automatic retry. |
| `bili_comments` | Read the first batch of comments on a video or dynamic; optionally include up to three attached replies per comment. |
| `bili_comment` | Comment on a resource reference, or reply with `root` and `parent` comment IDs. |
| `bili_post` | Publish text/image dynamics; optional topics, votes, live reservations, scheduled time and comment options. |

Tools that act on an account accept `adapter_name`. Selection order is explicit name, current Bilibili session, configured default, then the sole available Bilibili instance. Invalid explicit/default names fail without switching accounts. An ambiguous selection fails and asks for an account name. Names are configuration instance names, not platform names. Account objects are fetched for each call so reloading an adapter does not leave a stale reference.

Feed cursors remain opaque and tied to the originating account and query. Reuse the same source, filters and account. Cursors expire after ten minutes or adapter stop under the current adapter implementation. A filtered empty page can still have `has_more=true`. `count` is a per-page maximum; tools do not fetch indefinitely to fill it. General dynamic search and complete comment-list pagination are not provided.

`bili_comments` accepts the same `target` resource object as commenting. BV video IDs are supported; dynamic IDs are resolved to their actual comment resource. Use `count` (default 5) to limit top-level comments and `include_replies=false` to omit attached replies. Comment text is capped at 2,000 characters per item. Responses include comment/author IDs, text, creation time, likes, images and reply counts, with `first_batch_only=true`; `has_more` or `replies_complete` can be null when the response does not provide enough information. This tool does not fetch more pages or missing thread replies. To reply with `bili_comment`, use a returned comment's `root_comment_id` as `root` and its `comment_id` as `parent`. Reading remains available when the comment-writing switch is off.

`bili_user_videos` uses the selected adapter's existing credential with `bilibili_api.user.User.get_videos()`. Supply `author_id` as a UID string. Optional parameters: `page` (default 1), `count` (default 5, at most `min(max_count, 30)`), `keyword` (default empty) and `order` (`pubdate`, `view` or `favorite`). Results include `items`, `total`, `has_more` and `next_page`; totals and `has_more` are null if the SDK response omits the count. To continue, use `next_page` and keep the account, UID, page size and filters unchanged. Each item provides a video `ref` and `comment_target` for other tools. Use `bili_feed` with `source=user` and `author_id` for dynamics; filtering that stream to video does not list the space upload archive.

```json
{"adapter_name":"my-bili","author_id":"626885218","page":1,"count":5,"order":"pubdate"}
```

## Content and references

For comments, prefer a result's `comment_target`; otherwise use its `ref`. Resource IDs and UIDs should be strings to preserve integer precision. The adapter resolves BV IDs and dynamic comment resources.

```json
{
  "adapter_name": "my-bili",
  "target": {"resource_type": "video", "id": "BV17x411w7KC"},
  "content": [{"type": "text", "text": "Thanks for sharing!"}]
}
```

To reply, add both `root` (thread root ID) and `parent` (current comment ID). Neither is an IM session identifier.

`content` is an ordered list of up to 100 elements:

- Text: `{"type":"text","text":"hello"}`. Combined text is limited to 20,000 characters.
- Image: `{"type":"image","file":"https://example.com/image.png"}` or a local file path supported by KiraAI media elements.
- Emoji: `{"type":"emoji","id":"..."}`. Copy a native ID from the selected account's `bili_accounts` metadata.
- Mention, posts only: `{"type":"at","id":"12345","nickname":"optional name"}`.

Post `options` accepts only `topic_id`, `vote_id`, `live_reserve_id`, `send_time`, `up_choose_comment` and `close_comment`. ID options use positive integer strings. `send_time` uses ISO 8601 with an explicit timezone, for example `2030-01-01T12:00:00+08:00`. Credential overrides are rejected. Platform limits and moderation still apply. A successful receipt means the API accepted the request, not that the content is already publicly visible.

## Subtitles

Language matching is case-insensitive, normalizes `_`/`-`, Chinese region/script codes and the `ai-` prefix. Unsupported requested languages fail instead of silently switching to another language. No language selects the first track. Responses include the actual `language` and available language choices; use that actual language for subsequent pages.

`entry_offset` is zero-based. Continue with `next_entry_offset` until it is null. Each call returns up to `max_entries` (default 20, maximum 100) and the configured character budget. A long entry can be clipped with `text_truncated=true`; the SRT attachment preserves the full selected track. Files are created under `data/temp/bili_enhance_subtitles/` with unique filenames. They are kept for subsequent attachment delivery, including after plugin unload, and follow the application's temp-file retention. Only available CC subtitles are read; this plugin does not perform speech recognition or download the video.

Short links have bounded redirects to supported HTTPS Bilibili hosts. Subtitle requests validate official subtitle hosts, limit decoded response bytes and use bounded redirects and timeouts. Local file writing and subtitle parsing run outside the event loop.

## Configuration

All labels, tool descriptions and errors support English and Chinese. Language follows KiraAI's backend locale; unknown languages fall back to English. Reload the plugin after changing tool-description language.

| Key | Default | Meaning |
| --- | --- | --- |
| `default_adapter` | empty | Preferred configured account instance name. |
| `enable_like` | `true` | Enable explicit video like/unlike calls. |
| `enable_comment` | `true` | Enable comment/reply calls. |
| `enable_post` | `true` | Enable dynamic publishing calls. |
| `request_timeout` | `60` | Total tool timeout including queueing, in seconds; range 5–180. |
| `max_concurrency` | `3` | Concurrent operations per plugin; range 1–16. |
| `max_count` | `20` | Maximum feed/search/upload page or first comment batch items; range 5–100. Tool default is 5; space upload pages also have a plugin limit of 30. |
| `subtitle_max_chars` | `12000` | Subtitle preview character budget; range 100–60000. |
| `subtitle_max_bytes` | `2097152` | Subtitle download byte limit; range 1024–10485760. SRT is capped at twice this size. |

Writing is available through explicit tools by default and can be switched off individually. This plugin does not take over automatic incoming comment replies or duplicate private-message sending. The existing default-comment and IM/session tools continue their respective processing.

Errors contain safe localized messages without raw SDK exceptions or credentials. Write timeouts/failures are not automatically retried; the remote outcome may need checking. Unloading cancels and joins only this plugin's child tasks. The Bilibili SDK owns the shared session; this plugin never closes it.

## Offline verification

The optional test dependencies are listed in `requirements-dev.txt` (pytest, pytest-asyncio and jsonschema); they are not runtime dependencies. Run from the KiraAI repository root in the real Python environment:

```powershell
python -m pytest data/plugins/kira-ai-plugin-bili-enhance/tests/ -q
```

Tests cover actual plugin loading/registration, account routing and replacement, Feed mapping, native content, subtitle tracks and file output, bounded requests, action switches, localization, redacted errors and cancellation. External calls are mocked. Successful offline tests do not imply real account login, subtitle delivery or platform write validation.