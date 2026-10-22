import type { TurnView } from "./types";

const TOOL_LABELS: Record<string, string> = {
  estimate_hand_probabilities: "牌型概率",
  estimate_equity: "胜率估算",
  call_ev: "跟注 EV",
  get_random_number: "随机数",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] || name || "工具";
}

/**
 * Human-readable activity label for a running turn.
 *
 * The backend emits tool_call_start while the model streams tool arguments,
 * message_end once the model reply is complete, and tool_result after the
 * tool finishes. Combining the last message status with the tool status gives
 * the current phase without adding new server events.
 */
export function turnPendingLabel(turn: TurnView): string {
  if (turn.status !== "running") return "";

  const last = turn.messages[turn.messages.length - 1];
  const runningTool = last?.tools.find((tool) => tool.status === "running");

  if (runningTool) {
    if (last?.status === "streaming") return "正在准备工具调用…";
    if (runningTool.name === "apply_action") return "正在提交动作…";
    return `正在执行 ${toolLabel(runningTool.name)}…`;
  }

  if (!last || last.status === "streaming") return "模型思考中…";
  return "等待模型继续…";
}
