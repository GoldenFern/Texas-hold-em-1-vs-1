import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";
import { nextTick } from "vue";
import ReasoningBlock from "./ReasoningBlock.vue";
import { reasoningDefaultCollapsed, setReasoningDefaultCollapsed } from "../reasoning";

function mountBlock(props: { text?: string; streaming?: boolean; collapse?: boolean } = {}) {
  return mount(ReasoningBlock, {
    props: { text: "思考内容", streaming: false, collapse: false, ...props },
  });
}

function isOpen(wrapper: ReturnType<typeof mountBlock>): boolean {
  return wrapper.find(".reasoning").classes().includes("open");
}

beforeEach(() => {
  localStorage.clear();
  setReasoningDefaultCollapsed(false);
});

describe("ReasoningBlock", () => {
  it("opens while streaming and folds once content arrives", async () => {
    const wrapper = mountBlock({ streaming: true });
    expect(isOpen(wrapper)).toBe(true);

    await wrapper.setProps({ streaming: false, collapse: true });
    expect(isOpen(wrapper)).toBe(false);
  });

  it("remembers a manual collapse for later blocks", async () => {
    const first = mountBlock({ streaming: true });
    await first.find(".reasoning-head").trigger("click");

    expect(isOpen(first)).toBe(false);
    expect(reasoningDefaultCollapsed.value).toBe(true);
    expect(localStorage.getItem("heads-up-lab.reasoning-collapsed.v1")).toBe("1");

    const next = mountBlock({ streaming: true });
    expect(isOpen(next)).toBe(false);
  });

  it("keeps the preference when one folded block is reopened by hand", async () => {
    const first = mountBlock({ streaming: true });
    await first.find(".reasoning-head").trigger("click");
    first.unmount();

    const reopened = mountBlock({ streaming: true });
    await reopened.find(".reasoning-head").trigger("click");

    expect(isOpen(reopened)).toBe(true);
    expect(reasoningDefaultCollapsed.value).toBe(true);

    const third = mountBlock({ streaming: true });
    expect(isOpen(third)).toBe(false);
  });

  it("returns to the automatic behaviour once the preference is cleared", async () => {
    setReasoningDefaultCollapsed(true);
    const wrapper = mountBlock({ streaming: true });
    expect(isOpen(wrapper)).toBe(false);

    setReasoningDefaultCollapsed(false);
    await nextTick();
    expect(isOpen(wrapper)).toBe(true);
  });
});
