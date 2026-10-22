import { computed, markRaw, reactive } from "vue";
import type { CardInfo, GameSnapshot } from "./types";

const FIRST_CARD_DELAY = 120;
const CARD_GAP = 125;
const STREET_GAP = 580;
const FINISH_DELAY = 620;
const HOLE_HOLD_MS = 760;
const BANNER_MS = 1000;
const RIVER_INDEX = 4;

type StreetKey = "flop" | "turn" | "river";

interface RevealGroup {
  street: StreetKey;
  cards: CardInfo[];
}

export interface BoardBanner {
  label: string;
  key: number;
}

interface BoardRevealState {
  community: CardInfo[];
  busy: boolean;
  holeHold: boolean;
  dealNonce: number;
  banner: BoardBanner | null;
  baseSnapshot: GameSnapshot | null;
  hydrated: boolean;
}

export const board = reactive<BoardRevealState>({
  community: [],
  busy: false,
  holeHold: false,
  dealNonce: 0,
  banner: null,
  baseSnapshot: null,
  hydrated: false,
});

export const boardLocked = computed(() => board.busy || board.holeHold);

const STREET_LABELS: Record<StreetKey, string> = {
  flop: "FLOP",
  turn: "TURN",
  river: "RIVER",
};

let queue: RevealGroup[] = [];
let queuedCount = 0;
let queueTimer: ReturnType<typeof setTimeout> | null = null;
let holdTimer: ReturnType<typeof setTimeout> | null = null;
let bannerTimer: ReturnType<typeof setTimeout> | null = null;
let queueToken = 0;
let bannerKey = 0;

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function streetForCommunity(count: number): string {
  if (count >= 5) return "river";
  if (count === 4) return "turn";
  if (count >= 3) return "flop";
  return "preflop";
}

function streetForIndex(index: number): StreetKey {
  if (index >= RIVER_INDEX) return "river";
  if (index === 3) return "turn";
  return "flop";
}

function clearQueueTimer(): void {
  if (queueTimer !== null) {
    clearTimeout(queueTimer);
    queueTimer = null;
  }
}

function clearHoldTimer(): void {
  if (holdTimer !== null) {
    clearTimeout(holdTimer);
    holdTimer = null;
  }
}

function clearBannerTimer(): void {
  if (bannerTimer !== null) {
    clearTimeout(bannerTimer);
    bannerTimer = null;
  }
}

function cancelQueue(): void {
  queueToken += 1;
  clearQueueTimer();
  clearBannerTimer();
  queue = [];
  board.busy = false;
  board.baseSnapshot = null;
  board.banner = null;
}

export function resetBoardReveal(): void {
  cancelQueue();
  clearHoldTimer();
  queuedCount = 0;
  board.community = [];
  board.holeHold = false;
  board.banner = null;
  board.baseSnapshot = null;
  board.hydrated = false;
}

function startHoleHold(): void {
  clearHoldTimer();
  if (prefersReducedMotion()) {
    board.holeHold = false;
    if (queue.length) startQueue();
    return;
  }
  board.holeHold = true;
  holdTimer = setTimeout(() => {
    holdTimer = null;
    board.holeHold = false;
    // An instant all-in can deliver the whole board in the same snapshot that
    // starts the hand. Keep the board queued until the hole cards have landed
    // and flipped, otherwise the flop appears before the players' cards.
    if (queue.length) {
      startQueue();
    } else if (!board.busy) {
      board.baseSnapshot = null;
    }
  }, HOLE_HOLD_MS);
}

function showBanner(street: StreetKey): void {
  clearBannerTimer();
  bannerKey += 1;
  board.banner = { label: STREET_LABELS[street], key: bannerKey };
  bannerTimer = setTimeout(() => {
    bannerTimer = null;
    board.banner = null;
  }, BANNER_MS);
}

function finishQueue(token: number): void {
  if (token !== queueToken) return;
  if (queue.length) {
    scheduleNextGroup(token, 0);
    return;
  }
  board.busy = false;
  if (!board.holeHold) board.baseSnapshot = null;
}

function showCard(token: number, group: RevealGroup, index: number): void {
  if (token !== queueToken) return;
  const card = group.cards[index];
  if (!card) {
    finishQueue(token);
    return;
  }
  board.community = [...board.community, card];
  if (index + 1 < group.cards.length) {
    queueTimer = setTimeout(() => {
      queueTimer = null;
      showCard(token, group, index + 1);
    }, CARD_GAP);
    return;
  }
  if (queue.length) {
    queueTimer = setTimeout(() => {
      queueTimer = null;
      scheduleNextGroup(token, 0);
    }, STREET_GAP);
    return;
  }
  queueTimer = setTimeout(() => {
    queueTimer = null;
    finishQueue(token);
  }, FINISH_DELAY);
}

function scheduleNextGroup(token: number, delay: number): void {
  if (token !== queueToken) return;
  clearQueueTimer();
  queueTimer = setTimeout(() => {
    queueTimer = null;
    if (token !== queueToken) return;
    const group = queue.shift();
    if (!group) {
      finishQueue(token);
      return;
    }
    showBanner(group.street);
    showCard(token, group, 0);
  }, delay);
}

function startQueue(): void {
  if (board.busy || board.holeHold || !queue.length) return;
  if (prefersReducedMotion()) {
    for (const group of queue) {
      board.community = [...board.community, ...group.cards];
    }
    queue = [];
    board.busy = false;
    if (!board.holeHold) board.baseSnapshot = null;
    return;
  }
  queueToken += 1;
  board.busy = true;
  scheduleNextGroup(queueToken, FIRST_CARD_DELAY);
}

function splitNewCards(cards: CardInfo[], fromIndex: number): RevealGroup[] {
  const groups: RevealGroup[] = [];
  for (let index = fromIndex; index < cards.length; index += 1) {
    const street = streetForIndex(index);
    const last = groups[groups.length - 1];
    if (last && last.street === street) {
      last.cards.push(cards[index]);
    } else {
      groups.push({ street, cards: [cards[index]] });
    }
  }
  return groups;
}

function presentationBase(snapshot: GameSnapshot): GameSnapshot {
  const result = snapshot.match?.last_result;
  const hasResult = Boolean(result && result.hand_id === snapshot.hand_id);
  const finished = snapshot.street === "finished" || snapshot.street === "showdown";
  const players = (snapshot.players || []).map((player) => {
    const awarded = hasResult ? Number(result?.awarded?.[String(player.seat)] || 0) : 0;
    const refund = hasResult ? Number(result?.refunds?.[String(player.seat)] || 0) : 0;
    return {
      ...player,
      chips: hasResult ? Math.max(0, Number(player.chips || 0) - awarded - refund) : player.chips,
      status: player.status === "folded" ? "active" : player.status,
      last_action: null,
      assessment: null,
    };
  });
  const match = snapshot.match
    ? {
        ...snapshot.match,
        last_result: null,
        can_next_hand: false,
        match_over: false,
      }
    : null;
  return markRaw({
    ...snapshot,
    street: finished ? "preflop" : snapshot.street,
    community: [],
    pot: hasResult ? Number(result?.pot || 0) : snapshot.pot,
    current_bet: finished ? 0 : snapshot.current_bet,
    to_call: finished ? 0 : snapshot.to_call,
    acting_seat: finished ? null : snapshot.acting_seat,
    acting_kind: finished ? null : snapshot.acting_kind,
    legal_actions: finished ? [] : snapshot.legal_actions,
    result: null,
    players,
    match,
  });
}

export function ingestSnapshot(
  snapshot: GameSnapshot,
  previous: GameSnapshot | null,
  sameMatch: boolean,
): void {
  if (!snapshot.has_match) {
    resetBoardReveal();
    return;
  }

  const incoming = snapshot.community || [];

  // First load or reconnect: show the current board immediately and never
  // replay a deal the user did not see happen.
  if (!previous) {
    cancelQueue();
    clearHoldTimer();
    board.community = incoming.slice();
    queuedCount = incoming.length;
    board.holeHold = false;
    board.baseSnapshot = null;
    board.hydrated = true;
    return;
  }

  const previousHand = Number(previous.hand_id || 0);
  const incomingHand = Number(snapshot.hand_id || 0);
  const newHand = !sameMatch || incomingHand !== previousHand;

  if (newHand) {
    cancelQueue();
    clearHoldTimer();
    queuedCount = 0;
    board.community = [];
    board.hydrated = false;
    board.dealNonce += 1;
    board.baseSnapshot = presentationBase(snapshot);
    startHoleHold();
    if (!incoming.length) return;
    queue = splitNewCards(incoming, 0);
    queuedCount = incoming.length;
    startQueue();
    return;
  }

  if (incoming.length < queuedCount) {
    // A snapshot moved backwards (for example after a view reset). Snap to it
    // rather than leaving cards from the future on screen.
    cancelQueue();
    board.community = incoming.slice();
    queuedCount = incoming.length;
    board.hydrated = false;
    return;
  }

  if (incoming.length === queuedCount) return;

  board.hydrated = false;

  if (!board.busy) {
    const sameHandPrevious = Boolean(
      previous && previous.has_match && Number(previous.hand_id || 0) === incomingHand,
    );
    board.baseSnapshot = presentationBase(sameHandPrevious && previous ? previous : snapshot);
  }
  queue.push(...splitNewCards(incoming, queuedCount));
  queuedCount = incoming.length;
  startQueue();
}
