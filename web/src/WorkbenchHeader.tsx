import { useState, useSyncExternalStore } from "react";
import { Icon } from "@akatz/app-kit/react";
import { localBackend } from "./local-backend";
import { LocalSettings } from "./LocalSettings";
import avatar from "./avatar.webp";

/** Local-only chrome; the editor below this header is the hosted editor too. */
export function WorkbenchHeader() {
  const [open, setOpen] = useState(false);
  const execution = useSyncExternalStore(
    localBackend.subscribeExecution,
    localBackend.execution,
  );
  return (
    <>
      <header className="h3-local-header">
        <a
          className="h3-local-brand"
          href="https://akatz.ai"
          target="_blank"
          rel="noopener noreferrer"
        >
          <img src={avatar} alt="" />
          <span>Akatz Labs</span>
        </a>
        <span className="h3-local-badge">Local</span>
        <nav aria-label="Local tool controls">
          <a
            href="https://github.com/akatz-labs/h3-character-swap-tool"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub repository"
          >
            <svg
              className="ak-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              aria-hidden="true"
            >
              <path d="M9 19c-4.3 1.3-4.3-2.5-6-3m12 6v-3.9a3.4 3.4 0 0 0-.9-2.7c3-.3 6.2-1.5 6.2-6.8a5.3 5.3 0 0 0-1.5-3.7 4.9 4.9 0 0 0-.1-3.7S17.5.8 15 2.6a13.3 13.3 0 0 0-6 0C6.5.8 5.3 1.2 5.3 1.2a4.9 4.9 0 0 0-.1 3.7 5.3 5.3 0 0 0-1.5 3.7c0 5.3 3.2 6.5 6.2 6.8a3.4 3.4 0 0 0-.9 2.6v4" />
            </svg>
            <span>GitHub</span>
          </a>
          <a
            href="https://patreon.com/Akatz"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Support Akatz Labs"
          >
            <Icon name="heart" />
            <span>Support Akatz Labs</span>
          </a>
          <button
            type="button"
            aria-label="Settings"
            aria-haspopup="dialog"
            onClick={() => setOpen(true)}
          >
            <Icon name="settings" />
            <span>Settings</span>
          </button>
        </nav>
      </header>
      {open && (
        <LocalSettings execution={execution} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
