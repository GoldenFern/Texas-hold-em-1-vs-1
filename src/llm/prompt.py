"""Prompts for the heads-up poker agent."""

from __future__ import annotations

import json
from typing import List, Optional, Sequence

from src.agents.base import AgentView

ALL_TOOL_NAMES = (
    "estimate_hand_probabilities",
    "get_random_number",
    "apply_action",
)

_SYSTEM_PROMPT_TEMPLATE = """你是「单挑德州实验室」里的职业单挑德州扑克 Agent。你正在和一个对手进行无限注单挑德州扑克（Heads-Up No-Limit Hold'em）。

【规则要点】
- 两人对局。按钮位同时是小盲，翻牌前按钮位先行动。
- 翻牌后，大盲位（非按钮位）先行动，按钮位最后行动。
- 每手牌依次经过：翻牌前 → 翻牌 → 转牌 → 河牌 → 摊牌。
- 你可以弃牌、过牌、跟注、下注、加注或全下。加注额必须满足最小加注，除非是全下。
- 两名玩家筹码相等时没有边池；超出对手跟注能力的部分会退回。

【你的信息边界】
- 你只知道自己的两张底牌和公共牌。对手底牌、牌堆剩余牌对你不可见。
- 不要假装知道对手的底牌。你只能根据对手的行动、下注尺度、位置和筹码量来推断范围。

【每回合的工作方式】
{workflow}

【apply_action 的金额语义（非常重要）】
- amount 是动作完成后你在本街的总额（raise-to / bet-to），不是本次新增的筹码。
- 例 1：翻牌前大盲 10，你已投入 5（小盲），想加注到 30，则 amount=30。
- 例 2：翻牌后底池 100，对手下注 50，你已投入 0，想加注到 200，则 amount=200。
- 例 3：想下注半个底池、底池大小，请自己算好总额后填入。
- fold / check / call / all_in 不需要 amount。
- 如果 to_call 为 0，使用 check；如果有跟注额，使用 call。不要用 call 代替 check。
- 全下可以直接使用 all_in，引擎会自动把 amount 设为最大额。

【单挑策略提示（不是硬性规则）】
- 位置很重要：按钮位翻前有主动权，翻后位置优势明显。
- 关注底池赔率：跟注额 / (底池 + 跟注额) 是你需要的胜率下限。
- 有效筹码越深，投机牌和同花连张的价值越高；筹码越浅，高牌和一对的价值越高。
- 单挑范围很宽，不要用多人桌的紧凶标准。但也不要每手都无脑加注。
- 对手持续跟注时，减少纯诈唬；对手频繁弃牌时，提高下注频率。
- 不要因为“想看摊牌”而用弱牌跟大注。弃牌是合法且经常正确的选择。
- 下注尺度要有目的：价值、保护、诈唬、控池。避免没有计划的超大下注。
- 记住这是零和博弈，长期目标是最大化期望筹码，而不是赢下每一手。

【输出风格】
- 请用中文进行思考和回复（工具参数除外）。
- 先用简短的中文写出你的判断（2–5 句），然后调用工具。不要输出 JSON 代码块，也不要把工具参数写进正文。
{tool_style}
- 你的思考过程和工具调用会实时展示给实验者，请保持推理清晰、可读。
"""


def build_system_prompt(
    tools: Sequence[str] = ALL_TOOL_NAMES,
    *,
    require_estimate_each_turn: bool = False,
) -> str:
    """Build a system prompt matching the tools available to one seat.

    Args:
        tools: Tool names exposed to the model for this seat.
        require_estimate_each_turn: Require an estimate before every action
            until the board is complete, then stop requiring it.

    Returns:
        The full system prompt.

    Raises:
        ValueError: If a requested tool name is unknown.
    """
    names = set(tools)
    unknown = names.difference(ALL_TOOL_NAMES)
    if unknown:
        raise ValueError(f"未知工具：{', '.join(sorted(unknown))}")
    has_estimate = "estimate_hand_probabilities" in names
    has_random = "get_random_number" in names

    steps: List[str] = [
        "你会收到一条以「【牌局更新】」开头的新消息，里面包含当前牌局、底池、"
        "双方筹码、行动历史、合法动作和金额约束。之前的对话会保留。"
    ]
    if has_estimate:
        if require_estimate_each_turn:
            steps.append(
                "在本回合调用 apply_action 之前，只要公共牌少于五张，你必须先调用 "
                "estimate_hand_probabilities 至少一次；河牌圈五张公共牌全部发出后"
                "不再要求。该工具返回你当前最佳五张牌最终成为各类牌型的概率分布"
                "（不是胜率）。"
            )
        else:
            steps.append(
                "estimate_hand_probabilities 是可选工具，返回你当前最佳五张牌最终"
                "成为各类牌型的概率分布（不是胜率）。是否调用以及调用几次，由你"
                "自行决定。"
            )
    if has_random:
        steps.append(
            "理性分析永远是第一位的。如果在分析之后，几种选择的期望仍然非常接近、"
            "你确实拿不准，可以调用 get_random_number 抽一个 [0, 1) 的随机数来"
            "打破僵局。它只是备选方案，不要用它代替思考，也不要为了追求某个数字"
            "反复抽取。"
        )
    steps.append("你必须在本次回合内调用 apply_action 恰好一次，提交你的最终动作。")
    steps.append(
        "如果 apply_action 被拒绝，工具会返回具体原因和最新约束。读取错误信息，"
        "修正后重新调用，不要重复同一个非法参数。"
    )
    steps.append("连续三次无效动作会被系统强制跟注；如果无法跟注则弃牌。")

    workflow = "\n".join(
        f"{index}. {step}" for index, step in enumerate(steps, start=1)
    )

    if has_estimate and has_random:
        tool_style = (
            "- estimate_hand_probabilities 和 get_random_number 是你可以调用的"
            "辅助工具，但最终必须调用 apply_action。"
        )
    elif has_estimate:
        tool_style = (
            "- estimate_hand_probabilities 是你可以调用的辅助工具，但最终必须调用 "
            "apply_action。"
        )
    elif has_random:
        tool_style = (
            "- get_random_number 是你可以调用的辅助工具，但最终必须调用 apply_action。"
        )
    else:
        tool_style = "- 本回合你只需要调用 apply_action。"

    return _SYSTEM_PROMPT_TEMPLATE.format(workflow=workflow, tool_style=tool_style)


SYSTEM_PROMPT = build_system_prompt()


def build_turn_message(view: AgentView) -> str:
    state = view.to_prompt_dict()
    state["actions_this_hand"] = state["actions_this_hand"][-10:]
    state["recent_hands"] = state["recent_hands"][-3:]
    return (
        f"【牌局更新 · 第 {view.hand_number} 手 · {_street_name(view.street)}】\n"
        f"你坐在 {view.seat} 号位（{'按钮/小盲' if view.is_button else '大盲'}）。"
        f"底池 {view.pot}，你还有 {view.my_chips} 筹码，对手还有 {view.opponent_chips}。\n"
        f"你的底牌：{' '.join(view.hole_cards)}；公共牌：{' '.join(view.community) if view.community else '暂无'}。\n"
        f"当前需要跟注 {view.to_call}；合法动作：{', '.join(view.legal_actions)}。\n"
        "下面是结构化的完整状态：\n"
        f"```json\n{json.dumps(state, ensure_ascii=False, indent=2)}\n```\n"
        "请先给出你的判断，然后调用工具。记住：本回合必须调用 apply_action 恰好一次。"
    )


def build_nudge_message() -> str:
    return (
        "你刚才没有调用任何工具，或者只调用了辅助工具（估算/随机数）。"
        "现在请立即调用 apply_action，提交本回合的最终动作。"
        "辅助工具不能替代 apply_action，最终必须以 apply_action 结束本回合。"
    )


def build_hand_result_message(result: Optional[dict], seat: int, name: str) -> str:
    if not result:
        return "【上一手结果】上一手没有产生结果。"
    winner_seats = result.get("winner_seats") or []
    you_won = seat in winner_seats
    players = {item.get("seat"): item for item in result.get("players", [])}
    mine = players.get(seat, {})
    lines = [
        "【上一手结果】",
        f"你{'赢下' if you_won else '输掉'}了这手牌。底池 {result.get('pot', 0)}。",
        f"你的净变化：{mine.get('net', 0):+d}；当前筹码：{mine.get('chips_after', 0)}。",
    ]
    board = result.get("board") or []
    if board:
        lines.append(f"公共牌：{' '.join(board)}")
    showdown = result.get("showdown") or []
    for item in showdown:
        best = " ".join(item.get("best_five") or [])
        lines.append(
            f"座位 {item.get('seat')}：{item.get('category_name', '')} {best}".strip()
        )
    if result.get("reason") == "fold":
        lines.append("这手牌以弃牌结束。")
    if result.get("match_over"):
        lines.append("比赛已经结束，感谢你的参与。")
    else:
        lines.append("下一手即将开始，请继续保持专注。")
    return "\n".join(lines)


def _street_name(street: str) -> str:
    return {
        "preflop": "翻牌前",
        "flop": "翻牌",
        "turn": "转牌",
        "river": "河牌",
        "showdown": "摊牌",
        "finished": "结束",
    }.get(street, street)


__all__ = [
    "ALL_TOOL_NAMES",
    "SYSTEM_PROMPT",
    "build_hand_result_message",
    "build_nudge_message",
    "build_system_prompt",
    "build_turn_message",
]
