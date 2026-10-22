import { describe, expect, it } from "vitest";
import { describeAction, lastActionForSeat } from "./actionMeta";
import type { ActionLog } from "./types";

function log(overrides: Partial<ActionLog> = {}): ActionLog {
  return {
    hand_id: 1,
    street: "preflop",
    seat: 0,
    type: "raise",
    label: "加注",
    amount: 30,
    pot_after: 45,
    chips_after: 970,
    source: "test",
    auto: false,
    ...overrides,
  };
}

describe("describeAction", () => {
  it("formats the raise-to amount explicitly", () => {
    expect(describeAction(log())).toBe("加注到 30");
  });

  it("formats bets, calls, all-ins and amountless actions", () => {
    expect(describeAction(log({ type: "bet", label: "下注", amount: 60 }))).toBe("下注 60");
    expect(describeAction(log({ type: "call", label: "跟注", amount: 1200 }))).toBe("跟注 1,200");
    expect(describeAction(log({ type: "all_in", label: "全下", amount: 2000 }))).toBe("全下 2,000");
    expect(describeAction(log({ type: "check", label: "过牌", amount: 0 }))).toBe("过牌");
    expect(describeAction(log({ type: "fold", label: "弃牌", amount: 0 }))).toBe("弃牌");
  });

  it("falls back to the action type when no label exists", () => {
    expect(describeAction({ type: "call", amount: 10 })).toBe("跟注 10");
    expect(describeAction(null)).toBe("");
  });
});

describe("lastActionForSeat", () => {
  const actions = [
    log({ seat: 0, street: "preflop", type: "raise", amount: 30 }),
    log({ seat: 1, street: "preflop", type: "call", amount: 30 }),
    log({ seat: 0, street: "flop", type: "bet", amount: 50 }),
    log({ seat: 1, street: "flop", type: "fold", amount: 0 }),
    log({ hand_id: 2, seat: 0, street: "preflop", type: "check", amount: 0 }),
  ];

  it("only returns actions from the requested hand and street", () => {
    expect(lastActionForSeat(actions, 1, "flop", 0)?.type).toBe("bet");
    expect(lastActionForSeat(actions, 1, "flop", 1)?.type).toBe("fold");
    expect(lastActionForSeat(actions, 1, "preflop", 0)?.type).toBe("raise");
    expect(lastActionForSeat(actions, 1, "flop", 1)?.hand_id).toBe(1);
  });

  it("can include earlier streets after the hand has finished", () => {
    expect(lastActionForSeat(actions, 1, "river", 0, true)?.type).toBe("bet");
    expect(lastActionForSeat(actions, 1, "river", 1, true)?.type).toBe("fold");
  });

  it("returns null when the hand changes", () => {
    expect(lastActionForSeat(actions, 3, "preflop", 0)).toBeNull();
  });
});
