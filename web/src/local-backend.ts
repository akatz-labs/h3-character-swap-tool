import type { EditorAdapter } from "@akatz/h3-editor-core";
import { version } from "../../package.json";

const key = "h3-local-execution-v1";
const listeners = new Set<() => void>();
function readExecution() {
  const saved = localStorage.getItem(key);
  if (!saved) return { worker: "auto", modelOffloading: true };
  try {
    const value = JSON.parse(saved);
    return {
      worker: typeof value.worker === "string" ? value.worker : "auto",
      modelOffloading: value.modelOffloading !== false,
    };
  } catch {
    return { worker: "auto", modelOffloading: true };
  }
}
let execution = readExecution();
export function setExecution(changes: Partial<typeof execution>) {
  execution = { ...execution, ...changes };
  localStorage.setItem(key, JSON.stringify(execution));
  listeners.forEach((listener) => listener());
}
window.addEventListener("storage", (event) => {
  if (event.key === key) {
    execution = readExecution();
    listeners.forEach((listener) => listener());
  }
});
/** Native local HTTP transport. No account, wallet, or Platform client is imported. */
export const localBackend: EditorAdapter = {
  mode: "local",
  version,
  fetch: (input, init) => {
    // Root-relative editor API paths also work when served below a private preview path.
    const target =
      typeof input === "string" &&
      /^\/(?:api|__akatz|kit-api|kit-media)\//.test(input)
        ? input.slice(1)
        : input;
    return fetch(target, init);
  },
  health: async () => {
    const r = await fetch("api/health");
    if (!r.ok) throw Error("Local backend is unavailable");
    return r.json();
  },
  execution: () => execution,
  subscribeExecution: (listener) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  asset: (id, size) =>
    "api/assets/" +
    encodeURIComponent(id) +
    (size ? "/preview?size=" + size : ""),
  selectProject: () => false,
  identity: () => "local",
  jobsChanged: () => {},
  registerDraftSave: () => () => {},
};
