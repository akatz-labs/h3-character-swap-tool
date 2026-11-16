import { useEffect, useState } from "react";
import { z } from "zod";
import { localBackend } from "./local-backend";

const credentialStatus = z.object({ configured: z.boolean() });

/** This component never receives a saved credential, only its configured state. */
export function LocalGuideSettings() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void localBackend.fetch("/api/local-settings/openai", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw Error("Could not load local credential settings.");
        const status = credentialStatus.parse(await response.json());
        setConfigured(status.configured);
      })
      .catch(() => { if (!controller.signal.aborted) setError("Could not load local credential settings. Reopen this tab to try again."); });
    return () => controller.abort();
  }, []);

  async function update(method: "PUT" | "DELETE") {
    setBusy(true); setError(""); setMessage("");
    const body = method === "PUT" ? JSON.stringify({ api_key: key.trim() }) : undefined;
    setKey("");
    try {
      const response = await localBackend.fetch("/api/local-settings/openai", {
        method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body }),
      });
      if (!response.ok) throw Error("Credential update failed.");
      const status = credentialStatus.parse(await response.json());
      setConfigured(status.configured); setEditing(false); setDeleting(false);
      setMessage(method === "PUT" ? "API key saved. You can now generate guides and transcribe clips." : "Saved API key deleted.");
    } catch {
      setError("Could not update the saved key. Reopen this tab to check its status before trying again.");
    } finally { setBusy(false); }
  }

  return (
    <section className="h3-local-guide-settings" aria-label="OpenAI settings">
      <h3>OpenAI</h3>
      <p>Use your own OpenAI API key to generate guide images and transcribe clip dialogue. Requests are billed to your OpenAI account.</p>
      <p className="ak-muted">Stored privately on the machine running this local backend. The saved key is never sent back to your browser or included in projects.</p>
      <div className="h3-local-status">
        <span>API key</span>
        <strong>{configured === null ? "Checking…" : configured ? "Saved" : "Not configured"}</strong>
      </div>
      {(configured === false || editing) && (
        <form onSubmit={(event) => { event.preventDefault(); void update("PUT"); }}>
          <label htmlFor="local-openai-key">{configured ? "Replacement API key" : "OpenAI API key"}</label>
          <input id="local-openai-key" type="password" autoComplete="off" autoCapitalize="none"
            spellCheck={false} value={key} onChange={(event) => setKey(event.target.value)}
            placeholder="Enter your API key" maxLength={4096} disabled={busy} />
          <div className="ak-inline">
            <button className="ak-primary" type="submit" disabled={busy || key.trim().length < 10}>{busy ? "Saving…" : "Save API key"}</button>
            {configured && <button type="button" disabled={busy} onClick={() => { setKey(""); setEditing(false); }}>Cancel</button>}
          </div>
        </form>
      )}
      {configured && !editing && (
        <div className="ak-inline">
          {deleting ? <>
            <span>Delete the saved key?</span>
            <button className="ak-danger" disabled={busy} onClick={() => void update("DELETE")}>Delete API key</button>
            <button disabled={busy} onClick={() => setDeleting(false)}>Cancel</button>
          </> : <>
            <button onClick={() => { setEditing(true); setMessage(""); }}>Replace API key</button>
            <button onClick={() => setDeleting(true)}>Delete saved key</button>
          </>}
        </div>
      )}
      <p>GPT Image 2.6 Flare · Medium quality by default. Choose Low, Medium, or High in each guide editor.</p>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
