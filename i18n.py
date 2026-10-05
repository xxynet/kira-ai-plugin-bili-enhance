"""Localized messages and model-facing tool descriptions."""

TEXTS = {
    "en": {
        "invalid_input": "Invalid parameter: {parameter}.",
        "invalid_config": "Invalid plugin configuration: {parameter}.",
        "no_account": "No enabled Bilibili adapter instance is loaded. Configure an account in Adapters first.",
        "invalid_account": "The selected adapter is unavailable or is not an enabled Bilibili instance: {adapter_name}.",
        "ambiguous_account": "Multiple Bilibili instances are available. Call bili_accounts and specify adapter_name.",
        "missing_capability": "The selected account does not provide the required capability.",
        "disabled_action": "This action is disabled in the plugin configuration.",
        "closed": "BiliBili Enhance is stopping or has not been initialized.",
        "no_comment_target": "The selected dynamic has no supported comment resource.",
        "comments_note": "Only the client's first comment batch is fetched; attached replies are limited to three per comment and may be incomplete. To reply, pass root_comment_id as root and comment_id as parent to bili_comment.",
        "timeout": "The request timed out.",
        "write_timeout": "The write request timed out. Its remote outcome is unknown; check Bilibili before retrying.",
        "request_failed": "The Bilibili request failed. Check account credentials, connectivity and the requested resource.",
        "write_failed": "The write request failed. Check its remote outcome before retrying.",
        "invalid_video": "Provide a BV ID or a supported Bilibili video URL.",
        "invalid_download": "The remote URL or response is unsupported, invalid or exceeds the size limit.",
        "no_subtitle": "No CC subtitle track is available. Some AI subtitles require a logged-in account.",
        "empty_subtitle": "The selected subtitle track is empty.",
        "account_note": "Loaded instances are listed; availability does not confirm login or connection readiness.",
        "subtitle_ready": "Subtitle text is paged and may be truncated. The SRT attachment contains the full selected track.",
        "write_ready": "The platform accepted the request; moderation or scheduling may affect when it becomes visible.",
    },
    "zh": {
        "invalid_input": "参数无效：{parameter}。",
        "invalid_config": "插件配置无效：{parameter}。",
        "no_account": "没有已加载且启用的 B 站适配器实例，请先在适配器中配置账户。",
        "invalid_account": "所选适配器不可用，或不是已启用的 B 站实例：{adapter_name}。",
        "ambiguous_account": "存在多个 B 站实例，请调用 bili_accounts 并指定 adapter_name。",
        "missing_capability": "所选账户没有提供所需能力。",
        "disabled_action": "插件配置已关闭此操作。",
        "closed": "B站增强正在停止或尚未初始化。",
        "no_comment_target": "所选动态没有受支持的评论资源。",
        "comments_note": "仅获取客户端返回的首批评论，每条最多附带三条回复，可能不完整；回复时将 root_comment_id 作为 root、comment_id 作为 parent 传给 bili_comment。",
        "timeout": "请求超时。",
        "write_timeout": "写入请求超时，远端结果未知；重试前请先到 B 站核实。",
        "request_failed": "B 站请求失败，请检查账户凭据、网络和目标资源。",
        "write_failed": "写入请求失败，重试前请先核实远端结果。",
        "invalid_video": "请提供 BV 号或支持的 B 站视频链接。",
        "invalid_download": "远端地址或响应不受支持、无效或超过大小限制。",
        "no_subtitle": "没有可用的 CC 字幕，部分 AI 字幕需要已登录账户。",
        "empty_subtitle": "所选字幕轨道内容为空。",
        "account_note": "列表表示实例已加载，不代表登录或连接已经就绪。",
        "subtitle_ready": "字幕文本按段返回，可能截断；SRT 附件包含所选轨道的完整字幕。",
        "write_ready": "平台已接受请求；审核或定时发布可能影响实际可见时间。",
    },
}

TOOL_TEXTS = {
    "en": {
        "bili_accounts": "List loaded Bilibili account instances and comment/post formats, including native emoji IDs. Does not verify login.",
        "bili_user_videos": "List a user's space uploads by UID using the selected adapter account. Supports page numbers, optional keywords and sorting. Keep count, UID, account and filters unchanged when requesting next_page. Use bili_feed source=user for dynamics.",
        "bili_feed": "Browse recommended videos, following dynamics, or a user's dynamics. Reuse the exact account, filters and opaque cursor for pagination. An empty filtered page may have more results.",
        "bili_search": "Search Bilibili videos or articles by keyword. Use the returned opaque cursor with the same account and filters; general dynamic search is unsupported.",
        "bili_video_info": "Read video details from a BV ID, bilibili.com/video URL or b23.tv short URL. A URL's p parameter selects its part unless page is supplied.",
        "bili_video_subtitle": "Read CC subtitles for a video part. Return bounded, paged text and a complete SRT attachment; AI subtitles may require login. Use entry_offset to continue and the returned language for the same track.",
        "bili_comments": "Read comments on a video, dynamic or adapter-issued comment resource via the selected adapter client. Use comment_target from feed results where supplied, otherwise ref. Fetches the first batch only; no pagination or full thread retrieval. Reply with root_comment_id as root and comment_id as parent.",
        "bili_like_video": "Set a video's like state. liked=true likes it; liked=false removes the like. Uses the selected adapter account.",
        "bili_comment": "Publish a comment or reply using a resource reference returned by bili_feed/bili_search. To reply, supply both root and parent IDs. Use native emoji IDs from bili_accounts. Does not automatically reply to incoming comments.",
        "bili_post": "Publish a text or image dynamic with the selected account. Use native emoji IDs from bili_accounts. send_time must include a timezone. Only documented options are accepted.",
    },
    "zh": {
        "bili_accounts": "列出已加载的 B 站账户实例及评论/动态格式，含原生表情 ID，不验证登录状态。",
        "bili_user_videos": "复用所选适配器账户，按 UID 获取用户空间投稿视频，支持页码、可选关键词和排序；翻页时保持 count、UID、账户及筛选条件不变；用户动态使用 bili_feed 的 source=user。",
        "bili_feed": "浏览推荐视频、关注动态或指定用户动态。分页必须沿用相同账户、筛选条件和原样游标；筛选后的空页仍可能有后续结果。",
        "bili_search": "按关键词搜索 B 站视频或专栏。分页沿用相同账户、筛选条件和返回的原样游标，不支持通用动态搜索。",
        "bili_video_info": "通过 BV 号、bilibili.com/video 链接或 b23.tv 短链读取视频详情；未指定 page 时使用链接的 p 参数选择分 P。",
        "bili_video_subtitle": "读取指定分 P 的 CC 字幕，返回受限的分段文本和完整 SRT 附件；AI 字幕可能需要登录。使用 entry_offset 继续读取，并使用返回的 language 保持同一轨道。",
        "bili_comments": "通过所选适配器客户端读取视频、动态或适配器评论资源下的评论，优先使用查询结果的 comment_target，否则使用 ref；仅获取首批，不支持分页或完整楼中楼；回复时使用 root_comment_id 作为 root、comment_id 作为 parent。",
        "bili_like_video": "设置视频点赞状态：liked=true 点赞，liked=false 取消点赞，使用所选适配器账户。",
        "bili_comment": "使用 bili_feed/bili_search 返回的资源引用发表评论或回复；回复需同时提供 root、parent。原生表情 ID 从 bili_accounts 获取，不自动回复入站评论。",
        "bili_post": "使用所选账户发布文字或图文动态。原生表情 ID 从 bili_accounts 获取，send_time 必须包含时区，只接受已声明的选项。",
    },
}

PARAM_TEXTS = {
    "en": {
        "adapter_name": "Configured adapter instance name; not necessarily 'bilibili'. Selection: explicit name, current Bilibili session, configured default, sole instance.",
        "source": "Feed source; user requires author_id.",
        "order": "Upload sorting: pubdate (newest, default), view (most viewed), favorite (most favorited).",
        "user_video_page": "One-based space upload page, default 1. Continue using next_page with the same count and filters.",
        "user_video_count": "Upload page size, default 5; bounded by max_count and a plugin limit of 30. Keep unchanged for pagination.",
        "user_video_keyword": "Optional upload search keyword, default empty to list all uploads (up to 256 characters).",
        "count": "Maximum items in the feed/search page or first comment batch; default 5, bounded by plugin configuration.",
        "kinds": "Optional content kinds. Recommended only supports video.",
        "author_id": "Positive author UID; required for user feeds and space uploads.",
        "cursor": "Exact opaque cursor from the preceding page, tied to its account and filters.",
        "keyword": "Nonempty search keyword.",
        "kind": "Search content kind: video or article.",
        "video": "BV ID or https://www.bilibili.com/video/... or https://b23.tv/... URL.",
        "page": "One-based video part; defaults to URL p or 1.",
        "language": "Subtitle language, e.g. zh-CN, zh-Hans, zh-TW, en, ai-zh; defaults to the first track. Requested languages must match an available track.",
        "entry_offset": "Zero-based subtitle entry offset, default 0.",
        "max_entries": "Maximum subtitle entries to preview, default 20 (1-100).",
        "include_replies": "Include up to three replies attached to each top-level comment, default true; does not fetch additional replies.",
        "liked": "Explicit desired state: true to like, false to remove the like.",
        "target": "Resource object {resource_type, id}; use comment_target from results where supplied, otherwise ref. IDs remain strings to preserve precision.",
        "content": "Ordered elements: text {type,text}, image {type,file}, emoji {type,id}, or (posts only) at {type,id,nickname?}. Native emoji IDs come from bili_accounts.",
        "root": "Positive root comment ID for replies, kept as a string.",
        "parent": "Positive current comment ID to reply to; requires root.",
        "options": "Optional topic_id, vote_id, live_reserve_id, send_time (ISO 8601 with timezone), up_choose_comment, close_comment. No credentials.",
    },
    "zh": {
        "adapter_name": "用户配置的适配器实例名，不一定是 bilibili；依次选择明确实例、当前 B 站会话、配置默认实例、唯一实例。",
        "source": "内容来源；user 必须提供 author_id。",
        "order": "投稿排序：pubdate 最新发布（默认）、view 最多播放、favorite 最多收藏。",
        "user_video_page": "从 1 开始的投稿页码，默认 1；使用 next_page 继续，保持 count 和筛选条件不变。",
        "user_video_count": "投稿每页数量，默认 5，受 max_count 及插件上限 30 限制；分页时保持不变。",
        "user_video_keyword": "可选投稿搜索关键词，默认空字符串表示全部投稿，最多 256 字符。",
        "count": "内容页或首批评论最多返回数量，默认 5，受插件配置限制。",
        "kinds": "可选内容类型，推荐只支持 video。",
        "author_id": "正整数作者 UID，读取用户动态和空间投稿时必填。",
        "cursor": "上一页返回的原样游标，与账户及筛选条件绑定。",
        "keyword": "非空搜索关键词。",
        "kind": "搜索类型：video 视频或 article 专栏。",
        "video": "BV 号、https://www.bilibili.com/video/... 或 https://b23.tv/... 链接。",
        "page": "从 1 开始的分 P，默认使用链接的 p 参数或 1。",
        "language": "字幕语言，如 zh-CN、zh-Hans、zh-TW、en、ai-zh；默认第一条轨道，指定语言需匹配可用轨道。",
        "entry_offset": "字幕条目起始位置，从 0 开始，默认 0。",
        "max_entries": "最多预览字幕条目数，默认 20，范围 1-100。",
        "include_replies": "是否包含每条一级评论附带的最多三条回复，默认 true；不额外请求楼中楼。",
        "liked": "明确的目标状态：true 点赞，false 取消点赞。",
        "target": "资源对象 {resource_type,id}，优先使用结果的 comment_target，否则使用 ref；ID 使用字符串以保留精度。",
        "content": "按顺序提供元素：文字 {type,text}、图片 {type,file}、表情 {type,id}、仅动态可用的@ {type,id,nickname?}；原生表情 ID 从 bili_accounts 获取。",
        "root": "回复时提供线程根评论的正整数 ID，使用字符串。",
        "parent": "要回复的当前评论正整数 ID，使用字符串；必须同时提供 root。",
        "options": "可选 topic_id、vote_id、live_reserve_id、send_time（含时区的 ISO 8601）、up_choose_comment、close_comment，不接受凭据。",
    },
}


def language(code):
    return "zh" if str(code or "").lower().replace("_", "-").split("-")[0] == "zh" else "en"


def tr(code, key, **values):
    return TEXTS[language(code)][key].format(**values)


class PluginError(ValueError):
    """Carry a safe error code and public interpolation fields."""

    def __init__(self, code, **values):
        super().__init__(code)
        self.code = code
        self.values = values