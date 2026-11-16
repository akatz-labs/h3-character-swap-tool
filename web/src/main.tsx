import { createRoot } from "react-dom/client";
import { Workbench, configureEditor } from "@akatz/h3-editor-core";
import "@akatz/h3-editor-core/styles.css";
import "./local-shell.css";
import { localBackend } from "./local-backend";
import { WorkbenchHeader } from "./WorkbenchHeader";
configureEditor(localBackend);
const root = document.getElementById("root");
if (!root) throw Error("H3 mount is missing");
createRoot(root).render(
  <div className="h3-local-shell">
    <a className="ak-skip" href="#main">
      Skip to content
    </a>
    <WorkbenchHeader />
    <Workbench />
  </div>,
);
