"""Pure board-level compilation; hardware assumptions remain unverified."""

from .led import GUIDE_PALETTE, RGB, LedPlan, LedPlanError, compile_led_plan, plan_guide

__all__ = ["GUIDE_PALETTE", "RGB", "LedPlan", "LedPlanError", "compile_led_plan", "plan_guide"]

from .types import PlanError, SwitchOp, SwitchPlan, Xp
from .profile import BoardProfile, Limits
from .plan import (
    compile_plan, net_tags, shadow_from_state, state_crosspoints_from_shadow,
    state_with_tags, verify_plan,
)

__all__ += [
    "BoardProfile", "Limits", "PlanError", "SwitchOp", "SwitchPlan", "Xp",
    "compile_plan", "net_tags", "shadow_from_state", "state_crosspoints_from_shadow",
    "state_with_tags", "verify_plan",
]
