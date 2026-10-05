"""Localized function-calling schemas shared by registration and reload."""

from copy import deepcopy

from .i18n import PARAM_TEXTS, TOOL_TEXTS, language


def element_schema(mentions=False):
    choices = []
    elements = [('text', 'text'), ('image', 'file'), ('emoji', 'id')]
    if mentions:
        elements.append(('at', 'id'))
    for kind, key in elements:
        properties = {"type": {"type": "string", "enum": [kind]}, key: {"type": "string"}}
        if kind == "at":
            properties["nickname"] = {"type": "string"}
        choices.append({"type": "object", "properties": properties,
                        "required": ["type", key], "additionalProperties": False})
    return {"type": "array", "items": {"oneOf": choices}, "minItems": 1, "maxItems": 100}


FIELDS = {
    "adapter_name": {"type": "string", "minLength": 1, "maxLength": 128},
    "source": {"type": "string", "enum": ["recommended", "following", "user"]},
    "count": {"type": "integer", "minimum": 1, "maximum": 100},
    "kinds": {"type": "array", "items": {"type": "string", "enum": ["post", "video", "article", "audio", "unknown"]}},
    "author_id": {"type": "string", "pattern": "^[1-9][0-9]*$"},
    "cursor": {"type": "string", "minLength": 1, "maxLength": 256},
    "keyword": {"type": "string", "minLength": 1, "maxLength": 256},
    "kind": {"type": "string", "enum": ["video", "article"]},
    "video": {"type": "string", "minLength": 1, "maxLength": 4096},
    "page": {"type": "integer", "minimum": 1, "maximum": 10000},
    "language": {"type": "string", "maxLength": 32},
    "entry_offset": {"type": "integer", "minimum": 0},
    "max_entries": {"type": "integer", "minimum": 1, "maximum": 100},
    "liked": {"type": "boolean"},
    "target": {"type": "object", "properties": {
        "resource_type": {"type": "string"}, "id": {"type": "string", "minLength": 1, "maxLength": 128},
    }, "required": ["resource_type", "id"], "additionalProperties": False},
    "content": element_schema(),
    "root": {"type": "string", "pattern": "^[1-9][0-9]*$"},
    "parent": {"type": "string", "pattern": "^[1-9][0-9]*$"},
    "options": {"type": "object", "properties": {
        **{key: {"type": "string", "pattern": "^[1-9][0-9]*$"} for key in ("topic_id", "vote_id", "live_reserve_id")},
        "send_time": {"type": "string"}, "up_choose_comment": {"type": "boolean"}, "close_comment": {"type": "boolean"},
    }, "additionalProperties": False},
}

TOOLS = {
    "bili_accounts": ([], []),
    "bili_feed": (["adapter_name", "source", "count", "kinds", "author_id", "cursor"], []),
    "bili_search": (["adapter_name", "keyword", "kind", "count", "author_id", "cursor"], ["keyword"]),
    "bili_video_info": (["adapter_name", "video", "page"], ["video"]),
    "bili_video_subtitle": (["adapter_name", "video", "page", "language", "entry_offset", "max_entries"], ["video"]),
    "bili_like_video": (["adapter_name", "video", "liked"], ["video", "liked"]),
    "bili_comment": (["adapter_name", "target", "content", "root", "parent"], ["target", "content"]),
    "bili_post": (["adapter_name", "content", "options"], ["content"]),
}


def tool_schema(name, lang="en"):
    lang = language(lang)
    fields, required = TOOLS[name]
    properties = {key: deepcopy(FIELDS[key]) for key in fields}
    if name == "bili_post":
        properties["content"] = element_schema(mentions=True)
    for key, value in properties.items():
        value["description"] = PARAM_TEXTS[lang][key]
    return {
        "name": name, "description": TOOL_TEXTS[lang][name],
        "params": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False},
    }