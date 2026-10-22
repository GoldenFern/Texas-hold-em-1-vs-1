import { chips } from "./format";
import type { ActionLog } from "./types";

export interface ActionLike {
  type: string;
  label?: string;
  amount?: number;
}

const ACTION_LABELS: Record<string, string> = {
  fold: "弃牌",
  check: "过牌",
  call: "跟注",
  bet: "下注",
  raise: "加注",
  all_in: "全下",
};

/** Human-friendly action text shared by the table and the action log. */
export function describeAction(action: ActionLike | null | undefined): string {
  if (!action) return "";
  const label = action.label || ACTION_LABELS[action.type] || action.type;
  const amount = Number(action.amount || 0);
  if (!amount) return label;
  if (action.type === "raise") return `${label}到 ${chips(amount)}`;
  if (action.type === "bet" || action.type === "call" || action.type === "all_in") {
    return `${label} ${chips(amount)}`;
  }
  return label;
}

/**
 * Latest action for one seat.
 *
 * ``includeEarlier`` is used after a hand ends, where each seat's final action
 * may belong to a different street (for example one player folds on the flop).
 */
export function lastActionForSeat(
  actions: ActionLog[],
  handId: number,
  street: string,
  seat: number,
  includeEarlier = false,
): ActionLog | null {
  let latest: ActionLog | null = null;
  for (const action of actions) {
    if (action.hand_id !== handId || action.seat !== seat) continue;
    if (!includeEarlier && action.street !== street) continue;
    latest = action;
  }
  return latest;
}
