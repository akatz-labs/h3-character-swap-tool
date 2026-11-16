import { useEffect, useState } from "react";
import { z } from "zod";
import { SelectMenu } from "@akatz/app-kit/react";
import { localBackend } from "./local-backend";

const statusSchema = z.object({
  preference: z.enum(["auto", "local", "openai"]),
  localAvailable: z.boolean(), openaiAvailable: z.boolean(),
  selected: z.enum(["local", "openai"]).nullable(), ready: z.boolean(),
});
type Status = z.infer<typeof statusSchema>;
const options = [
  { value: "auto", label: "Auto · prefer local Whisper" },
  { value: "local", label: "Local Whisper" },
  { value: "openai", label: "OpenAI API" },
];

export function LocalTranscriptionSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void localBackend.fetch("/api/local-settings/transcription", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw Error("Could not load transcription settings.");
        setStatus(statusSchema.parse(await response.json()));
      }).catch(() => {
        if (!controller.signal.aborted) setError("Could not load transcription settings. Reopen this tab to try again.");
      });
    return () => controller.abort();
  }, []);
  async function save(preference: string) {
    setBusy(true); setError("");
    try {
      const response = await localBackend.fetch("/api/local-settings/transcription", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preference }),
      });
      if (!response.ok) throw Error("Could not save transcription settings.");
      setStatus(statusSchema.parse(await response.json()));
    } catch {
      setError("Could not save transcription settings. Reopen this tab to check before trying again.");
    } finally { setBusy(false); }
  }
  return <section className="h3-local-guide-settings" aria-label="Transcription settings">
    <h3>Transcription</h3>
    <p>Auto uses installed local Whisper first, then your saved OpenAI key. The choice is saved on this machine.</p>
    {status ? <>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <label>Transcription provider</label>
        <SelectMenu placeholder="Choose provider" disabled={busy} label="Transcription provider" value={status.preference} options={options}
          displayValue={options.find((option) => option.value === status.preference)?.label ?? "Auto · prefer local Whisper"}
          onChange={(value) => void save(value)} />
      </fieldset>
      <div className="h3-local-status"><span>Local Whisper · English · CPU</span><strong>{status.localAvailable ? "Installed" : "Not installed"}</strong></div>
      <div className="h3-local-status"><span>OpenAI API key</span><strong>{status.openaiAvailable ? "Saved" : "Not configured"}</strong></div>
      <p role="status">{busy ? "Saving…" : status.ready ? `Ready · ${status.selected === "local" ? "Local Whisper" : "OpenAI API"}` : "Download Whisper under Models or add your key under OpenAI, then choose an available provider."}</p>
    </> : !error && <p role="status">Loading transcription settings…</p>}
    <p className="ak-muted">Whisper is an optional download (about 1.5 GB). Local transcription keeps audio on this machine. OpenAI sends audio to your OpenAI account and is billed there. An explicit choice never silently falls back to another provider.</p>
    {error && <p role="alert">{error}</p>}
  </section>;
}
