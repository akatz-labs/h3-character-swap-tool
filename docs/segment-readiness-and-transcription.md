# Segment readiness and local transcription

The timeline uses the same source of truth as continuation admission. A completed
take is current only when its source range, canvas, generation signature and exact
parent take IDs still match. The newest matching take is used when there is no
explicit compatible selection. Earlier input changes invalidate the dependent
chain, including descendants whose own settings have not changed. Saved videos
remain in Outputs; this is not deletion. Restoring the exact inputs can restore a
matching chain. Mask names/colors do not affect readiness; selected-mask pixels,
expansion and pending point corrections do. Unselected masks do not affect output.

Green dots mean generated with current inputs; amber means not generated, stale,
or waiting for a predecessor. Hover/focus exposes the specific reason. Active
segment jobs use the shared activity treatment. The timeline reports the number
of current generated segments, not the number of retained takes.

Local Settings → OpenAI stores a write-only credential under the configured
backend app-data directory, outside projects and the browser. Direct Image Edits
and file transcription use that same credential. The source launcher supplies
`local-openai`; it does not require an Akatz account, broker, GPU host or private
provider endpoint for either operation. GPU execution remains separately configured.

Local Settings → Transcription saves an independent provider preference. Auto
uses installed local Whisper first, then the saved OpenAI key. An explicit Local
or OpenAI choice requires that provider; it never silently falls back. With
neither available, submission returns an actionable error directing the user to
Models or OpenAI settings. The provider is frozen when each job is queued.

Whisper Medium.en is an optional, pinned English model running on CPU. It reuses
the shared Hugging Face cache and retains real word timestamps. It is not part
of the six required GPU model downloads.

When OpenAI is selected, Transcribe clip queues a network task with `gpt-transcribe` using
`/v1/audio/transcriptions`. Audio is extracted at 16 kHz mono and split at saved
segment boundaries, with a 120-second cap per upload. The API returns text, not
word timestamps: records retain the actual source chunk intervals and never claim
word-level alignment. Editing segment boundaries later can include a whole chunk
that overlaps the new boundary; retranscribe after repartitioning for exact
segment-level dialogue. Existing manually edited dialogue/prompts are preserved.

Each paid submission gets a receipt before POST. Successful chunks are retained
for recovery; uncertain attempts are never automatically posted again. Transport
errors are sanitized, and keys are never written into receipts or jobs. Hosted
transcription needs its own registered processing/billing operation; this change
does not pretend that a local credential can power a hosted request.

OpenAI documentation checked for this implementation:
- https://developers.openai.com/api/docs/guides/speech-to-text
- https://developers.openai.com/api/docs/pricing

The current model is listed at $0.0045/minute. Legacy Whisper and GPT-4o
transcription models are deprecated for February 26, 2027; they are not the new
integration default. Pricing remains provider-controlled.
