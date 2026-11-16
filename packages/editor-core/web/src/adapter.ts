/** Host-owned I/O. The editor never imports account SDKs or provider settings. */
export interface EditorAdapter {
  mode: "local" | "hosted";
  health(): Promise<{instanceId: string}>;
  execution(): {worker: string; modelOffloading: boolean};
  subscribeExecution(listener: () => void): () => void;
  generationPrice?(input: {operation: string; parameters?: Record<string, string>; seconds?: number}): Promise<number>;
  version: string;
  fetch: typeof fetch;
  asset(id: string, size?: number): string;
  selectProject(id: string | undefined): boolean;
  identity(): string;
  jobsChanged(): void;
  registerDraftSave(save: () => Promise<void>): () => void;
  importVideo?(
    file: File,
    project: string,
    progress: (phase: string, loaded?: number, total?: number) => void,
    signal: AbortSignal,
  ): Promise<unknown>;
}
export let adapter: EditorAdapter;
export let connected = false;
export function configureEditor(value: EditorAdapter) {
  if (adapter) throw Error("Configure the editor once, before mounting");
  adapter = value;
  connected = value.mode === "hosted";
}
export const editorFetch: typeof fetch = (input, init) =>
  adapter.fetch(input, init);
export const editorAsset = (id: string, size?: number) =>
  adapter.asset(id, size);
export const selectEditorProject = (id: string | undefined) =>
  adapter.selectProject(id);
export const registerDraftSave = (save: () => Promise<void>) =>
  adapter.registerDraftSave(save);
export function connectedImportVideo(
  file: File,
  project: string,
  progress: (phase: string, loaded?: number, total?: number) => void,
  signal: AbortSignal,
) {
  if (!adapter.importVideo) throw Error("Prepared video import is unavailable");
  return adapter.importVideo(file, project, progress, signal);
}
