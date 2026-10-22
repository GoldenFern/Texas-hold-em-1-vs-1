import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it, vi } from "vitest";

const storeMock = vi.hoisted(() => {
  function turn(id: string, handId: number) {
    return {
      id,
      header: {
        handId,
        handNumber: handId,
        street: "preflop",
        pot: 15,
        myChips: 995,
        opponentChips: 990,
        toCall: 5,
        legalActions: ["fold", "call", "raise"],
        position: "button",
      },
      messages: [],
      notes: [],
      stateText: "",
      action: null,
      auto: false,
      status: "done",
      usage: { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0 },
      latencyMs: 10,
      steps: 0,
      contextTokens: 0,
      startedAt: 1,
      endedAt: 11,
    };
  }
  return {
    game: {
      selectedSeat: 0,
      pinned: false,
      matchId: "match-1",
      connected: true,
      actionFeed: [] as unknown[],
      lastResult: null,
      snapshot: {
        has_match: true,
        players: [
          { seat: 0, name: "Agent A", kind: "llm" },
          { seat: 1, name: "Agent B", kind: "llm" },
        ],
        match: {
          seats: [
            { seat: 0, name: "Agent A", kind: "llm", model_name: "Model A", icon: "", agent: {} },
            { seat: 1, name: "Agent B", kind: "llm", model_name: "Model B", icon: "", agent: {} },
          ],
        },
      },
      turns: {
        0: [turn("t1", 1), turn("t2", 2)],
      } as Record<number, unknown[]>,
    },
  };
});

vi.mock("../store", async () => {
  const { ref } = await import("vue");
  return {
    game: storeMock.game,
    agentSeats: ref([
      { seat: 0, name: "Agent A", kind: "llm", model_name: "Model A", icon: "", agent: {} },
    ]),
    humanSeat: ref(null),
    followLive: vi.fn(),
    selectSeat: vi.fn(),
    openSetup: vi.fn(),
  };
});

import AgentConsole from "./AgentConsole.vue";

describe("AgentConsole stream", () => {
  it("renders a visual divider at the start of every hand", async () => {
    const wrapper = mount(AgentConsole);
    await nextTick();
    const dividers = wrapper.findAll(".hand-divider");
    expect(dividers).toHaveLength(2);
    expect(dividers[0].text()).toContain("HAND #1");
    expect(dividers[1].text()).toContain("HAND #2");
    wrapper.unmount();
  });
});
