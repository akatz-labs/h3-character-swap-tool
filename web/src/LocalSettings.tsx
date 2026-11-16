import { useState } from "react";
import {
  JsonClient,
  ResourceController,
  createSetupResourceAdapter,
  setupClient,
} from "@akatz/app-kit";
import {
  Dialog,
  ResourcesPage,
  WorkerSetup,
  SelectMenu,
  useResources,
} from "@akatz/app-kit/react";
import { LocalGuideSettings } from "./LocalGuideSettings";
import { LocalTranscriptionSettings } from "./LocalTranscriptionSettings";
import { localBackend, setExecution } from "./local-backend";

export function LocalSettings({
  execution,
  onClose,
}: {
  execution: ReturnType<typeof localBackend.execution>;
  onClose: () => void;
}) {
  const [tab, setTab] = useState("execution");
  const [http] = useState(() => new JsonClient("", localBackend.fetch));
  const [controller] = useState(
    () => new ResourceController(createSetupResourceAdapter(http)),
  );
  const [setup] = useState(() => setupClient(http));
  const resources = useResources(controller);
  const workers = resources.registry?.executors ?? [];
  const selected = workers.find(
    (worker) =>
      worker.id ===
      (execution.worker === "auto" ? resources.workerId : execution.worker),
  );
  return (
    <Dialog title="Local settings" onClose={onClose}>
      <div className="h3-local-settings">
        <nav className="h3-local-settings-tabs" aria-label="Settings sections">
          {(
            [
              ["execution", "Execution"],
              ["guides", "OpenAI"],
              ["transcription", "Transcription"],
              ["models", "Models"],
              ["setup", "Worker setup"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              aria-pressed={tab === id}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="h3-local-settings-content" key={tab}>
          {tab === "execution" ? (
            <section className="h3-local-execution">
              <label>Generation worker</label>
              <SelectMenu
                placeholder="Choose worker"
                label="Generation worker"
                value={execution.worker}
                displayValue={
                  execution.worker === "auto"
                    ? "Auto · local workers"
                    : (selected?.name ?? execution.worker)
                }
                options={[
                  { value: "auto", label: "Auto · local workers" },
                  ...workers.map((worker) => ({
                    value: worker.id,
                    label: `${worker.name} · ${worker.online ? "Online" : "Offline"}`,
                  })),
                ]}
                onChange={(worker) => {
                  setExecution({ worker });
                  if (worker !== "auto") controller.select(worker);
                }}
              />
              <label className="h3-local-offloading">
                <input
                  type="checkbox"
                  checked={execution.modelOffloading}
                  onChange={(event) =>
                    setExecution({ modelOffloading: event.target.checked })
                  }
                />
                <span>
                  Model offloading
                  <small>Move model layers to system memory.</small>
                </span>
              </label>
              <div className="h3-local-status">
                <span>Worker connection</span>
                <strong>
                  {!resources.registry && !resources.error
                    ? "Checking…"
                    : selected?.online
                      ? selected.ready
                        ? "Connected"
                        : "Needs setup"
                      : "Offline"}
                </strong>
              </div>
              {resources.error && <p role="alert">{resources.error}</p>}
            </section>
          ) : tab === "guides" ? (
            <LocalGuideSettings />
          ) : tab === "transcription" ? (
            <LocalTranscriptionSettings />
          ) : tab === "models" ? (
            <ResourcesPage state={resources} controller={controller} />
          ) : (
            <WorkerSetup
              state={resources}
              controller={controller}
              api={setup}
              onContinue={() => setTab("models")}
              onBrowse={() => setTab("models")}
            />
          )}
        </div>
        <div className="h3-local-settings-footer">
          <button className="ak-primary" type="button" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Dialog>
  );
}
