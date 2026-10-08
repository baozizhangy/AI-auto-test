"""场景注册中心。"""

from __future__ import annotations

from scenarios.base import Scenario, Flow

_scenarios: dict[str, Scenario] = {}
_flows: dict[str, Flow] = {}


def register(obj: Scenario | Flow) -> None:
    """注册 Scenario 或 Flow。"""
    if isinstance(obj, Flow):
        _flows[obj.id] = obj
    else:
        _scenarios[obj.id] = obj


def list_all() -> list[Scenario | Flow]:
    """列出所有已注册的场景和流程。"""
    result: list[Scenario | Flow] = list(_scenarios.values())
    result.extend(_flows.values())
    return result


def get(scenario_id: str) -> Scenario | Flow | None:
    """按 ID 获取场景或流程。"""
    return _flows.get(scenario_id) or _scenarios.get(scenario_id)


def get_flow(flow_id: str) -> Flow | None:
    """按 ID 获取 Flow。"""
    return _flows.get(flow_id)


def list_flows() -> list[Flow]:
    """列出所有已注册的 Flow。"""
    return list(_flows.values())
