import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ToolCard from "./ToolCard.vue";
import type { ToolView } from "../types";

function talkTool(overrides: Partial<ToolView> = {}): ToolView {
  return {
    index: 0,
    callId: "call-1",
    name: "send_table_message",
    argsText: '{"text":"我这次不会再让你便宜看牌。"}',
    parsed: { text: "我这次不会再让你便宜看牌。" },
    status: "done",
    result: {
      ok: true,
      entry: { text: "我这次不会再让你便宜看牌。" },
      remaining_turn: 0,
    },
    summary: "已喊话",
    durationMs: 2,
    ...overrides,
  };
}

describe("ToolCard table talk", () => {
  it("renders an accepted message with remaining quotas", () => {
    const wrapper = mount(ToolCard, { props: { tool: talkTool() } });
    expect(wrapper.text()).toContain("桌边喊话");
    expect(wrapper.text()).toContain("我这次不会再让你便宜看牌。");
    expect(wrapper.text()).toContain("本回合喊话完成");
    wrapper.unmount();
  });

  it("renders a rejected message reason", () => {
    const wrapper = mount(ToolCard, {
      props: {
        tool: talkTool({
          status: "error",
          result: { ok: false, error: "喊话内容不能为空" },
          summary: "喊话内容不能为空",
        }),
      },
    });
    expect(wrapper.text()).toContain("桌边喊话");
    expect(wrapper.text()).toContain("喊话内容不能为空");
    expect(wrapper.text()).not.toContain("对手下一次决策时看到");
    wrapper.unmount();
  });
});
