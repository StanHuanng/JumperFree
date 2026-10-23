"""Pure board-level compilation; hardware assumptions remain unverified."""

from .led import GUIDE_PALETTE, RGB, LedPlan, LedPlanError, compile_led_plan, plan_guide

__all__ = ["GUIDE_PALETTE", "RGB", "LedPlan", "LedPlanError", "compile_led_plan", "plan_guide"]
