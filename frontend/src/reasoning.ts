import { ref } from "vue";

const STORAGE_KEY = "heads-up-lab.reasoning-collapsed.v1";

function loadDefaultCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Global default for reasoning blocks. When true, every block the user has not
 * toggled by hand stays folded, including blocks that are still streaming.
 */
export const reasoningDefaultCollapsed = ref(loadDefaultCollapsed());

export function setReasoningDefaultCollapsed(collapsed: boolean): void {
  reasoningDefaultCollapsed.value = collapsed;
  try {
    if (collapsed) localStorage.setItem(STORAGE_KEY, "1");
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage may be unavailable; keep the preference for this session.
  }
}
