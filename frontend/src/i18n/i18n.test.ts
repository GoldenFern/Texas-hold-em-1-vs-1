import { nextTick } from "vue";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  detectLocale,
  locale,
  localizedText,
  setLocale,
  t,
} from "./index";
import { handCategoryLabel, streetLabel } from "./labels";

describe("i18n", () => {
  it("defaults to English when no preference is saved", () => {
    window.localStorage.clear();
    expect(detectLocale()).toBe(DEFAULT_LOCALE);
    expect(DEFAULT_LOCALE).toBe("en-US");
  });

  it("reads a saved locale and persists changes", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "zh-CN");
    expect(detectLocale()).toBe("zh-CN");

    setLocale("en-US");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-US");
  });

  it("switches strings, interpolation, and the document language", async () => {
    setLocale("en-US", false);
    expect(locale.value).toBe("en-US");
    expect(t("topbar.newMatch")).toBe("New match");
    expect(t("setup.validation.range", { label: "Max hands", min: 0, max: 10 }))
      .toBe("Max hands must be between 0 and 10.");
    expect(streetLabel("flop")).toBe("Flop");
    expect(handCategoryLabel("three_of_a_kind", "三条")).toBe("Three of a kind");

    setLocale("zh-CN", false);
    expect(t("topbar.newMatch")).toBe("新比赛");
    expect(t("setup.validation.range", { label: "最大手数", min: 0, max: 10 }))
      .toBe("最大手数必须在 0 到 10 之间。");
    expect(streetLabel("flop")).toBe("翻牌");
    expect(handCategoryLabel("three_of_a_kind", "三条")).toBe("三条");

    await nextTick();
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(document.title).toBe(t("app.title"));
  });

  it("selects bilingual metadata by the active locale", () => {
    setLocale("en-US", false);
    expect(localizedText("火山引擎", "Volcengine")).toBe("Volcengine");
    setLocale("zh-CN", false);
    expect(localizedText("火山引擎", "Volcengine")).toBe("火山引擎");
  });
});
