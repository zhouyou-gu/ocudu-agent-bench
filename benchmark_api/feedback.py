"""Safe immediate feedback frame construction."""

from __future__ import annotations

import time
from typing import Any

from benchmark_api.action import ActionRecord
from benchmark_api.types import RanActionType


def build_feedback(action_record: ActionRecord) -> dict[str, Any]:
    dispatch = action_record.dispatch
    if action_record.action.get("type") == RanActionType.NO_ACTION.value:
        status = "no_action"
        safe_message = "no action selected"
        safe_error_class = None
        dispatch_state = "not_dispatched"
    elif not action_record.valid:
        status = "rejected"
        safe_message = action_record.safe_message
        safe_error_class = action_record.safe_error_class.value if action_record.safe_error_class else None
        dispatch_state = "local_rejection"
    elif dispatch is not None and dispatch.accepted:
        status = "accepted"
        safe_message = dispatch.safe_message
        safe_error_class = None
        dispatch_state = "accepted"
    else:
        status = "rejected"
        safe_message = dispatch.safe_message if dispatch else "action was not dispatched"
        safe_error_class = dispatch.safe_error_class.value if dispatch and dispatch.safe_error_class else None
        dispatch_state = "runtime_rejection"
    return {
        "step_id": action_record.step_id,
        "action_id": action_record.action_id,
        "feedback_timestamp_s": time.time(),
        "status": status,
        "safe_error_class": safe_error_class,
        "safe_message": safe_message,
        "dispatch_state": dispatch_state,
    }
