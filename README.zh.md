# B站增强

[English](README.md)

B站增强（`kira-ai-plugin-bili-enhance`）为 KiraAI 提供八个 LLM 原生工具，用于浏览 B 站内容、读取视频信息和 CC 字幕、点赞、评论及发布动态。账户复用内置 B 站适配器，无需在插件中重复配置 Cookie，无命令入口或后台轮询。

## 安装与账户

1. 使用已具备当前类型化 Feed 能力和消息格式元数据接口的 KiraAI 版本，Python 要求 3.10+。
2. 将本目录放在 `data/plugins/kira-ai-plugin-bili-enhance/`，通过 KiraAI 插件管理加载并启用。
3. 配置并启用内置 B 站适配器账户，登录凭据填写在适配器中；写入需要该账户的 `sessdata` 和 `bili_jct`。账户配置与认证由适配器管理。
4. 让 AI 列出 B 站账户或调用工具。可在插件配置的 `default_adapter` 中指定默认实例名。

插件不启动、停止适配器或进行登录。`bili_accounts` 中出现某实例只表示已启用且加载，不代表登录或连接已经验证。仅使用 Feed 的适配器可以在其配置中设置 `enable_im=false`。插件沿用主项目已有依赖：`bilibili-api-python>=17.4.2`、`httpx>=0.28.1`。

## 工具

| 工具 | 行为 |
| --- | --- |
| `bili_accounts` | 列出已加载实例的名称、UID、能力、原生表情 ID、评论/动态格式、操作开关和结果上限。 |
| `bili_feed` | 浏览推荐视频（recommended）、关注动态（following）或指定用户动态（user，需 author_id）；支持类型、数量、作者筛选与游标分页。 |
| `bili_search` | 按非空 keyword 搜索 video 视频或 article 专栏，支持作者筛选和原样游标分页。 |
| `bili_video_info` | 读取 BV 号、B站视频链接或 https://b23.tv/ 短链；page 为从1开始的分P，默认取链接 p 参数或1。 |
| `bili_video_subtitle` | 获取指定分P的 CC 字幕，选择 language，以 entry_offset 和 max_entries 分段预览，并返回所选轨道的完整 SRT 附件。 |
| `bili_like_video` | 显式设置 liked=true 点赞、liked=false 取消点赞，不提供切换或自动重试。 |
| `bili_comment` | 对资源引用发表评论，或通过 root 和 parent 评论 ID 回复。 |
| `bili_post` | 发布文字或图文动态，可选话题、投票、直播预约、定时发布和评论选项。 |

涉及账户的工具接受 `adapter_name`。选择顺序为明确指定的实例、当前 B 站会话、配置的默认实例、唯一可用实例。明确实例或默认实例无效时直接失败，不切换账户；存在多个候选且无法确定时返回错误，要求指定账户。实例名是用户配置名称，不等于平台名称。每次调用重新获取账户对象，适配器重载后不会沿用旧引用。

Feed 游标属于原账户和查询，请沿用相同来源、筛选条件与账户。当前适配器游标在十分钟后或停止时失效。筛选后的空页仍可能有 `has_more=true`。count 表示本页最大数量，工具不会为凑足数量无限抓取。不提供通用动态搜索或完整评论列表分页。

## 内容与目标引用

评论优先使用查询结果的 `comment_target`，否则使用 `ref`。资源 ID 和 UID 应使用字符串以保留整数精度，BV 转换和动态评论资源解析交给适配器。

```json
{
  "adapter_name": "my-bili",
  "target": {"resource_type": "video", "id": "BV17x411w7KC"},
  "content": [{"type": "text", "text": "谢谢分享！"}]
}
```

回复时同时提供 root（线程根评论 ID）和 parent（当前评论 ID），这两个参数均不是 IM 会话标识。

content 是最多100个元素的有序列表：

- 文字：`{"type":"text","text":"你好"}`，合计文字最多20,000字符。
- 图片：`{"type":"image","file":"https://example.com/image.png"}`，或 KiraAI 媒体元素支持的本地文件路径。
- 表情：`{"type":"emoji","id":"..."}`，从所选账户的 bili_accounts 元数据复制原生 ID。
- @用户，仅动态可用：`{"type":"at","id":"12345","nickname":"可选昵称"}`。

动态 options 只接受 topic_id、vote_id、live_reserve_id、send_time、up_choose_comment、close_comment。ID选项使用正整数字符串。send_time 使用含明确时区的 ISO 8601，例如 `2030-01-01T12:00:00+08:00`。不允许覆盖账户凭据。平台自身的限制和审核仍然适用，成功回执表示接口接受了请求，不表示内容已经公开可见。

## 字幕

语言匹配不区分大小写，归一化下划线/连字符、中文地区/书写体系代码和 ai- 前缀。指定语言没有匹配轨道时失败，不静默切换为其他语言；留空使用第一条轨道。响应提供实际 language 和可用语言列表，后续分页请使用返回的实际语言。

entry_offset 从0开始，使用 next_entry_offset 继续读取，直到返回 null。每次最多返回 max_entries 条（默认20，上限100），并受字符预算限制。单条过长时可能截断并返回 text_truncated=true；SRT附件保留所选轨道的完整内容。文件保存到 `data/temp/bili_enhance_subtitles/`，使用唯一名称，卸载插件后仍保留以供附件发送，并遵循应用的临时文件保留机制。插件只读取现有 CC 字幕，不进行语音识别或下载视频。

短链限制重定向次数，只接受支持的 HTTPS B站域名。字幕下载验证官方字幕域名、限制解压后的响应大小，并设置重定向上限和超时。文件写入及字幕解析移出事件循环。

## 配置

所有标签、工具说明和错误支持中英文，跟随 KiraAI 后端语言；未知语言回退英文。修改工具说明语言后重新加载插件。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| default_adapter | 空 | 默认账户的配置实例名。 |
| enable_like | true | 启用视频点赞/取消点赞工具。 |
| enable_comment | true | 启用评论/回复工具。 |
| enable_post | true | 启用动态发布工具。 |
| request_timeout | 60 | 含排队时间的工具总超时，单位秒，范围5–180。 |
| max_concurrency | 3 | 本插件并发操作数，范围1–16。 |
| max_count | 20 | 浏览/搜索每页数量上限，范围5–100，工具默认5条。 |
| subtitle_max_chars | 12000 | 字幕预览字符预算，范围100–60000。 |
| subtitle_max_bytes | 2097152 | 字幕下载字节上限，范围1024–10485760，SRT上限为其两倍。 |

写入默认通过明确工具调用提供，可分别关闭。插件不接管入站评论自动回复，也不重复提供私信发送工具；现有默认评论插件和 IM/跨会话工具继续处理各自流程。

错误只返回安全的本地化信息，不输出原始SDK异常或凭据。写入超时/失败不自动重试，需要核实远端结果。卸载时仅取消并等待本插件的子任务，SDK共享会话由SDK管理，插件不会关闭它。

## 离线验证

可选测试依赖见 `requirements-dev.txt`（pytest、pytest-asyncio、jsonschema），不属于运行依赖。在KiraAI仓库根目录的真实Python环境执行：

```powershell
python -m pytest data/plugins/kira-ai-plugin-bili-enhance/tests/ -q
```

测试覆盖真实插件加载/注册、账户选择与实例替换、Feed映射、原生内容、字幕轨道与文件输出、请求边界、操作开关、国际化、异常脱敏和取消清理。外部调用均为mock。离线通过不代表已完成真实账户登录、字幕投递或平台写入验证。