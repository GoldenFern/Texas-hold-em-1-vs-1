import { chips } from "./format";
import { actionLabel } from "./i18n/labels";
import { t } from "./i18n";
import type { ActionLog } from "./types";

export interface ActionLike {
  type: string;
  label?: string;
  amount?: number;
}

/** Human-friendly action text shared by the table and the action log. */
export function describeAction(action: ActionLike | null | undefined): string {
  if (!action) return "";
  const localized = actionLabel(action.type);
  const label = localized && localized !== action.type
    ? localized
    : action.label || localized || action.type;
  const amount = Number(action.amount || 0);
  if (!amount) return label;
  if (action.type === "raise") {
    return t("action.raiseToLabel", { action: label, amount: chips(amount) });
  }
  if (action.type === "bet" || action.type === "call" || action.type === "all_in") {
    return t("action.withAmount", { action: label, amount: chips(amount) });
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
