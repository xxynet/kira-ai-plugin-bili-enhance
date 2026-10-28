# BiliBili Enhance

[中文说明](README.zh.md)

Browse Bilibili, search videos, read subtitles, view frames and danmaku, post comments, like videos and publish dynamics with KiraAI. Uses accounts from the built-in Bilibili adapter, with no separate Cookie configuration.

## Setup

1. Use Python 3.10+ and place the plugin in `data/plugins/kira-ai-plugin-bili-enhance/`.
2. Configure and enable a built-in Bilibili adapter account in KiraAI.
3. Load and enable BiliBili Enhance in plugin management.

For multiple accounts, set `default_adapter` or supply `adapter_name` when calling a tool. Otherwise, the current Bilibili session or sole available account is used. Listing an account does not verify its login status.

## Features

| Tool | Purpose |
| --- | --- |
| `bili_accounts` | List available accounts and supported operations. |
| `bili_user_videos` | Browse a creator's uploads, with pagination, keywords and sorting. |
| `bili_feed` | Browse recommendations, followed accounts' dynamics or a user's dynamics. |
| `bili_search` | Search videos or articles by keyword. |
| `bili_video_info` | Read video details and parts. |
| `bili_video_subtitle` | Read CC subtitles, with paged previews and a complete SRT attachment. |
| `bili_frames` | View video frames in one combined image with timestamps: 3 frames by default, up to 6. |
| `bili_danmaku` | Read one six-minute danmaku segment, returning 5 items by default. |
| `bili_like_video` | Like or unlike a video. |
| `bili_comments` | Read the first batch of video/dynamic comments and some replies. |
| `bili_comment` | Post a comment or reply. |
| `bili_post` | Publish text/image dynamics, with mentions, topics, scheduling and other options. |

## Usage

Ask the AI in natural language, for example:

- "Find recent Python tutorials on Bilibili."
- "Show me this creator's latest uploads."
- "Read the subtitles for part 2 of this video."
- "Look at the frames around 30 and 60 seconds, then read some danmaku."

Video tools accept BV IDs, Bilibili video URLs and b23.tv short links. Use `page` to select a part; otherwise the URL's part or part 1 is used.

For frames, use `num` to choose the count or `at` to specify timestamps in seconds. Frames are returned in one combined image with actual timestamps; the previews may miss small text or brief actions. Enable session image recognition and configure a vision-capable model or VLM for the AI to view frames.

For danmaku, use `segment` (starting at 1) and `count`. Continue with the returned `next_segment`. Increase `count` to see more items from the current segment, within the configured limit.

Subtitles require an existing CC track; speech transcription is not provided. Comments include only the first batch and some replies, without full pagination.

## Configuration

| Key | Default | Description |
| --- | --- | --- |
| `default_adapter` | empty | Default Bilibili account instance name. |
| `enable_like` | `true` | Allow video likes and unlikes. |
| `enable_comment` | `true` | Allow comments and replies. |
| `enable_post` | `true` | Allow dynamic publishing. |
| `request_timeout` | `60` | Total tool timeout in seconds, from 5 to 180. |
| `max_concurrency` | `3` | Concurrent operations, from 1 to 16. |
| `max_count` | `20` | Item limit for browsing, search, uploads, comments and danmaku, from 5 to 100; upload pages are capped at 30. |
| `subtitle_max_chars` | `12000` | Subtitle preview character limit per call. |
| `subtitle_max_bytes` | `2097152` | Subtitle download size limit in bytes. |

The interface and tool descriptions support English and Chinese, following the backend language. Reload the plugin after changing languages.

Write operations use the selected account and remain subject to Bilibili permissions, restrictions and moderation. Check the outcome before retrying a timed-out write. Automatic incoming comment replies remain with existing handlers; use existing IM tools for private messages.

## Tests

Run from the KiraAI repository root in the real Python environment:

```powershell
python -m pytest data/plugins/kira-ai-plugin-bili-enhance/tests/ -q
```

Tests mock external services and do not replace real account validation.
