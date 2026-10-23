import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it } from "vitest";
import LanguageSwitch from "./LanguageSwitch.vue";
import { LOCALE_STORAGE_KEY, locale, setLocale } from "../i18n";

describe("LanguageSwitch", () => {
  it("switches locale and persists the choice", async () => {
    setLocale("zh-CN", false);
    const wrapper = mount(LanguageSwitch);

    const buttons = wrapper.findAll("button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0].attributes("aria-pressed")).toBe("true");

    await buttons[1].trigger("click");
    await nextTick();

    expect(locale.value).toBe("en-US");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-US");
    expect(document.documentElement.lang).toBe("en-US");
    expect(wrapper.find("button.active").text()).toBe("EN");
  });
});
