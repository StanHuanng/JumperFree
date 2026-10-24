"""Pure deterministic BBM compilation, replay, and state/shadow adaptation."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from ..core import JfError, SCHEMA
from ..core.shadow import Shadow, apply_ops, shadow_crc
from ..routing.crossbar import find_shorts
from ..routing.model import RouteError
from ..routing.steiner import crossbar_query
from .profile import BoardProfile, reject_bad_crosspoints
from .types import PlanError, SwitchOp, SwitchPlan, Xp


def _check_tags(tags: Mapping[str, int]) -> None:
    if (any(not isinstance(name, str) or type(tag) is not int or not 1 <= tag <= 15
            for name, tag in tags.items()) or len(set(tags.values())) != len(tags)):
        raise PlanError("BAD_ARG", "net 标签必须唯一且在 1..15")


def net_tags(state: dict[str, Any]) -> dict[str, int]:
    """Read active meta.tags; fill missing entries in lexical order, ignoring stale IDs."""
    names = sorted(state["nets"])
    if len(names) > 15:
        raise PlanError("PLAN_TOO_LARGE", "同时存在的 net 不能超过 15 个", nets=len(names))
    meta = state.get("meta", {})
    if not isinstance(meta, dict) or not isinstance(meta.get("tags", {}), dict):
        raise RouteError("INVALID_STATE", "state meta.tags 必须是对象")
    tags = {name: meta["tags"][name] for name in names if name in meta.get("tags", {})}
    _check_tags(tags)
    for name in names:
        if name not in tags:
            tags[name] = next(tag for tag in range(1, 16) if tag not in tags.values())
    return dict(sorted(tags.items()))


def state_with_tags(state: dict[str, Any], tags: Mapping[str, int]) -> dict[str, Any]:
    """Return a detached state with the optional append-only meta.tags key populated."""
    _check_tags(tags)
    if set(tags) != set(state["nets"]):
        raise PlanError("BAD_ARG", "标签表必须恰好覆盖当前 nets")
    updated = copy.deepcopy(state)
    meta = updated.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise RouteError("INVALID_STATE", "state meta 必须是对象")
    meta["tags"] = dict(sorted(tags.items()))
    return updated


def _points(state: dict[str, Any], profile: BoardProfile) -> dict[Xp, tuple[str, int]]:
    crossbar_query(state)  # Preserve routing-state errors and their layer.
    if state["topology"] != profile.topology:
        raise PlanError("PLAN_BOARD_MISMATCH", "状态拓扑与板级配置不一致")
    tags = net_tags(state)
    points: dict[Xp, tuple[str, int]] = {}
    for name, net in sorted(state["nets"].items()):
        for chip, x, y in net["crosspoints"]:
            point = Xp(profile.chip_map[chip], x, y)
            if point in points:
                raise PlanError("PLAN_VERIFY_FAILED", "交叉点被多个 net 占用")
            points[point] = name, tags[name]
    reject_bad_crosspoints(points, profile)
    return points


def shadow_from_state(state: dict[str, Any], profile: BoardProfile) -> Shadow:
    points = _points(state, profile)
    return apply_ops(Shadow(), makes=(SwitchOp(xp, True, tag) for xp, (_, tag) in sorted(points.items())))


def state_crosspoints_from_shadow(
    shadow: Shadow, profile: BoardProfile, tags: Mapping[str, int],
) -> dict[str, Any]:
    """Return only crosspoints/meta.tags, never infer intent or fabricate routing lines."""
    _check_tags(tags)
    owners = {tag: name for name, tag in tags.items()}
    nets: dict[str, Any] = {name: {"crosspoints": []} for name in sorted(tags)}
    points: dict[Xp, int] = {}
    x_owners: dict[tuple[str, str, int], str] = {}
    chips = {index: name for name, index in profile.chip_map.items()}
    for chip in range(2):
        for bit in range(128):
            tag = shadow.tags[chip * 128 + bit]
            if not shadow.closed[chip] & (1 << bit):
                if tag:
                    raise PlanError("PLAN_VERIFY_FAILED", "断开点仍有标签")
                continue
            point = Xp(chip, bit // 8, bit % 8)
            if tag not in owners:
                raise PlanError("PLAN_VERIFY_FAILED", "闭合点的标签没有对应 net")
            name = owners[tag]
            x_line = (chips[chip], "X", point.x)
            if x_line in x_owners and x_owners[x_line] != name:
                raise PlanError("PLAN_VERIFY_FAILED", "同一 X 线路有不同 net 标签")
            x_owners[x_line] = name
            points[point] = tag
            nets[name]["crosspoints"].append([chips[chip], point.x, point.y])
    reject_bad_crosspoints(points, profile)
    assignments = {node: x_owners[tuple(line)] for node, line in profile.topology["node_attachments"].items()
                   if tuple(line) in x_owners}
    shorts = find_shorts(profile.topology, [point for net in nets.values() for point in net["crosspoints"]],
                         assignments)
    if shorts:
        raise PlanError("PLAN_VERIFY_FAILED", "影子包含跨 net 短接", shorts=shorts)
    return {"nets": nets, "meta": {"tags": dict(sorted(tags.items()))}}


def compile_plan(
    old: dict[str, Any], new: dict[str, Any], prof: BoardProfile, *, base_epoch: int = 0,
) -> SwitchPlan:
    for state in (old, new):
        if isinstance(state, dict) and isinstance(state.get("nets"), dict) and len(state["nets"]) > 15:
            raise PlanError("PLAN_TOO_LARGE", "同时存在的 net 不能超过 15 个", nets=len(state["nets"]))
    crossbar_query(old)
    crossbar_query(new)
    if old["topology"] != new["topology"]:
        raise PlanError("PLAN_TOPOLOGY_MISMATCH", "新旧状态的拓扑不一致")
    old_points = _points(old, prof)
    previous = net_tags(old)
    # Surviving nets retain tags; input new.meta.tags cannot override that contract.
    tags = {name: previous[name] for name in sorted(new["nets"]) if name in previous}
    tagged_new = state_with_tags(new, net_tags({"nets": new["nets"], "meta": {"tags": tags}}))
    tags = tagged_new["meta"]["tags"]
    new_points = _points(tagged_new, prof)
    breaks = tuple(SwitchOp(xp, False, tag) for xp, (name, tag) in sorted(old_points.items())
                   if new_points.get(xp) != (name, tag))
    makes = tuple(SwitchOp(xp, True, tag) for xp, (name, tag) in sorted(new_points.items())
                  if old_points.get(xp) != (name, tag))
    disturbed = tuple(name for name in sorted(set(old["nets"]) & set(new["nets"]))
                      if old["nets"][name]["crosspoints"] != new["nets"][name]["crosspoints"])
    target = shadow_from_state(tagged_new, prof)
    plan = SwitchPlan(SCHEMA["plan"], base_epoch, prof.topology_id, breaks, makes,
                      shadow_crc(target), disturbed, tags)
    view = verify_plan(old, plan, prof)
    if (apply_ops(shadow_from_state(old, prof), plan.breaks, plan.makes) != target
            or view["nets"] != {name: {"crosspoints": net["crosspoints"]}
                                for name, net in tagged_new["nets"].items()}):
        raise PlanError("PLAN_VERIFY_FAILED", "重放结果与目标状态不一致")
    # Also check declared routing memberships, independently of physical tag ownership.
    shorts = find_shorts(prof.topology, [point for net in new["nets"].values() for point in net["crosspoints"]],
                         {name: net["nodes"] for name, net in new["nets"].items()})
    if shorts:
        raise PlanError("PLAN_VERIFY_FAILED", "目标状态包含跨 net 短接", shorts=shorts)
    return plan


def verify_plan(old: dict[str, Any], plan: SwitchPlan, prof: BoardProfile) -> dict[str, Any]:
    before = shadow_from_state(old, prof)
    if len(plan.breaks) + len(plan.makes) > prof.limits.max_ops:
        raise PlanError("PLAN_TOO_LARGE", "计划操作数超过 max_ops，M3 service 才负责拆分",
                        max_ops=prof.limits.max_ops, ops=len(plan.breaks) + len(plan.makes))
    try:
        if plan.topology_id != prof.topology_id:
            raise PlanError("PLAN_VERIFY_FAILED", "计划 topology_id 不匹配")
        after = apply_ops(before, plan.breaks, plan.makes)
        if shadow_crc(after) != plan.expect_crc:
            raise PlanError("PLAN_VERIFY_FAILED", "目标影子 CRC 不匹配")
        return state_crosspoints_from_shadow(after, prof, plan.tags)
    except JfError as exc:
        raise PlanError("PLAN_VERIFY_FAILED", "计划重放校验失败", cause=exc.as_dict()) from exc
