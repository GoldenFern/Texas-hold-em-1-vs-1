import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  board,
  ingestSnapshot,
  resetBoardReveal,
  streetForCommunity,
} from "./boardReveal";
import type { CardInfo, GameSnapshot } from "./types";

function card(code: string): CardInfo {
  return {
    code,
    rank: code[0],
    suit: code[1],
    symbol: code[1],
    red: code[1] === "h" || code[1] === "d",
    label: code,
  };
}

function snapshot(overrides: Partial<GameSnapshot> = {}): GameSnapshot {
  return {
    has_match: true,
    match: {
      id: "m1",
      hands_played: 0,
      max_hands: 0,
      match_over: false,
      god_view: false,
      can_next_hand: false,
      started_at: 1,
      error: "",
      rules: {
        starting_chips: 1000,
        small_blind: 5,
        big_blind: 10,
        auto_rebuy: false,
        rebuy_amount: 1000,
        max_hands: 0,
        seed: 1,
      },
      seats: [],
      history: [],
      last_result: null,
    },
    hand_id: 1,
    street: "preflop",
    community: [],
    players: [],
    ...overrides,
  } as GameSnapshot;
}

const flop = [card("2c"), card("7d"), card("Th")];
const turn = card("Jh");
const river = card("As");

beforeEach(() => {
  vi.useFakeTimers();
  resetBoardReveal();
  board.dealNonce = 0;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetBoardReveal();
});

describe("board reveal timing", () => {
  it("shows the current board immediately on first load", () => {
    ingestSnapshot(snapshot({ community: flop }), null, false);

    expect(board.community).toHaveLength(3);
    expect(board.busy).toBe(false);
    expect(board.holeHold).toBe(false);
    expect(board.dealNonce).toBe(0);
    expect(board.hydrated).toBe(true);
  });

  it("reveals a flop one card at a time and waits for the flip to settle", () => {
    const previous = snapshot({ seq: 1 });
    ingestSnapshot(previous, null, false);
    ingestSnapshot(snapshot({ seq: 2, street: "flop", community: flop }), previous, true);

    expect(board.busy).toBe(true);
    expect(board.community).toHaveLength(0);
    expect(board.baseSnapshot?.hand_id).toBe(previous.hand_id);
    expect(board.baseSnapshot?.street).toBe("preflop");

    vi.advanceTimersByTime(119);
    expect(board.community).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(board.community).toHaveLength(1);
    vi.advanceTimersByTime(125);
    expect(board.community).toHaveLength(2);
    vi.advanceTimersByTime(125);
    expect(board.community).toHaveLength(3);

    vi.advanceTimersByTime(619);
    expect(board.busy).toBe(true);
    vi.advanceTimersByTime(1);
    expect(board.busy).toBe(false);
    expect(board.baseSnapshot).toBeNull();
  });

  it("splits an all-in runout into flop, turn and river", () => {
    const previous = snapshot({ seq: 1 });
    ingestSnapshot(previous, null, false);
    ingestSnapshot(
      snapshot({
        seq: 2,
        street: "finished",
        community: [...flop, turn, river],
      }),
      previous,
      true,
    );

    expect(board.busy).toBe(true);
    vi.advanceTimersByTime(370);
    expect(board.community).toHaveLength(3);
    expect(board.banner?.label).toBe("FLOP");

    vi.advanceTimersByTime(581);
    expect(board.community).toHaveLength(4);
    expect(board.banner?.label).toBe("TURN");

    vi.advanceTimersByTime(581);
    expect(board.community).toHaveLength(5);
    expect(board.banner?.label).toBe("RIVER");

    vi.advanceTimersByTime(619);
    expect(board.busy).toBe(true);
    vi.advanceTimersByTime(1);
    expect(board.busy).toBe(false);
  });

  it("starts a fresh deal on the next hand", () => {
    const previous = snapshot({ seq: 5, street: "finished", community: [...flop, turn, river] });
    ingestSnapshot(previous, null, false);
    ingestSnapshot(snapshot({ seq: 6, hand_id: 2 }), previous, true);

    expect(board.community).toHaveLength(0);
    expect(board.dealNonce).toBe(1);
    expect(board.holeHold).toBe(true);
    expect(board.hydrated).toBe(false);

    vi.advanceTimersByTime(759);
    expect(board.holeHold).toBe(true);
    vi.advanceTimersByTime(1);
    expect(board.holeHold).toBe(false);
  });

  it("cancels pending reveals on reset", () => {
    const previous = snapshot({ seq: 1 });
    ingestSnapshot(previous, null, false);
    ingestSnapshot(snapshot({ seq: 2, street: "flop", community: flop }), previous, true);
    vi.advanceTimersByTime(120);
    expect(board.community).toHaveLength(1);

    resetBoardReveal();
    vi.advanceTimersByTime(3000);

    expect(board.community).toHaveLength(0);
    expect(board.busy).toBe(false);
    expect(board.baseSnapshot).toBeNull();
    expect(board.hydrated).toBe(false);
  });

  it("drains the queue immediately when reduced motion is requested", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
    }));

    const previous = snapshot({ seq: 1 });
    ingestSnapshot(previous, null, false);
    ingestSnapshot(
      snapshot({
        seq: 2,
        street: "finished",
        community: [...flop, turn, river],
      }),
      previous,
      true,
    );

    expect(board.community).toHaveLength(5);
    expect(board.busy).toBe(false);
  });
});

describe("streetForCommunity", () => {
  it("maps board counts to streets", () => {
    expect(streetForCommunity(0)).toBe("preflop");
    expect(streetForCommunity(3)).toBe("flop");
    expect(streetForCommunity(4)).toBe("turn");
    expect(streetForCommunity(5)).toBe("river");
  });
});
