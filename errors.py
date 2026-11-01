"""Preserve complete Bilibili tool exceptions in results and project logs."""

import json
import traceback

from core.plugin import logger


def report_error(exc, context):
    errors = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        errors.append({
            "type": type(current).__name__,
            "message": str(current),
            "attributes": vars(current),
            "traceback": "".join(traceback.format_exception(
                type(current), current, current.__traceback__, chain=False,
            )),
        })
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    details = {**errors[0], "traceback": "\n".join(error["traceback"] for error in reversed(errors))}
    if len(errors) > 1:
        details["chain"] = errors[1:]
    # SDK attributes can contain objects such as HTTP responses, not just JSON values.
    serialized = json.dumps(details, ensure_ascii=False, default=str)
    details = json.loads(serialized)
    logger.error("%s\n%s", context, json.dumps(details, ensure_ascii=False, indent=2))
    return details
