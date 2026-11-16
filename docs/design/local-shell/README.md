# Local shell design

The shared editor stays slate/mint. The local header follows the website's
near-black/cyan header hierarchy, with the official bundled avatar, full-width
alignment and GitHub, Support, Settings actions. All project navigation and the
queue remain below it in the shared editor. Settings uses the existing app-kit
dialog and resource components, with execution preferences separate from projects.

`concept.png` is the GPT ImageGen design reference. Its illustrative avatar was
replaced by the official asset in the implementation. Desktop and narrow browser
screenshots, keyboard behavior and playback are checked in the consuming apps.

## Exact generation prompt

UI design concept, desktop wide screenshot of existing Akatz Labs dark creator video editor with a local execution settings modal open. Purpose: one editor shared with hosted version, local only top header provides GitHub, Support Akatz Labs, rightmost gear Settings button; no account or credit controls. Header near-black navy, official illustrated circular avatar represented abstractly next to Akatz Labs wordmark on far left, tiny Local badge, actions aligned far right, full width. Below header retain editor near-black surfaces, mint controls, left sidebar Project dropdown / Shots Assets / shot thumbnails / Project queue, middle video and timeline, right Segment inspector Characters Prompt & dialogue Render settings. No editing method, model or LoRa picker in inspector. Centered accessible modal title 'Local settings', close X, tabs 'Execution', 'Models', 'Worker setup'. Execution tab selected: labeled Generation worker dropdown 'Auto · local workers', checkbox 'Model offloading' unchecked with concise description 'Move model layers to system memory.', worker connection status 'Connected', Done button. Modal about 560px wide; calm flat navy surfaces, fine borders, white text, cyan focus, Inter/Geist typography, deliberate padding, compact desktop tool. Include a small inset narrow mobile header showing brand plus icon GitHub / heart / gear with no overflow. This is a design concept, no invented pricing or functionality.
