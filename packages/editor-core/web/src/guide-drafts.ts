import { z } from 'zod';

const settings = z.object({
  prompt: z.string().max(12000),
  edit_model: z.enum(['gpt-image', 'h3-image']).optional(),
  quality: z.enum(['low', 'medium', 'high']),
  references: z.array(z.string()).max(15),
  helpers: z.array(z.object({id: z.string(), text: z.string()})),
  edit_source_asset: z.string(),
});
type Settings = z.infer<typeof settings>;
const record = z.object({base: settings, values: settings});
type Draft = z.infer<typeof record>;
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;

/** Restore only locally changed fields; untouched fields follow current server state. */
export function restoreGuideDraft(storage: Storage, key: string, current: Settings): Draft {
  const draft = {base: structuredClone(current), values: structuredClone(current)};
  let saved: unknown;
  try { saved = JSON.parse(storage.getItem(key) || 'null'); }
  catch { return draft; } // Malformed or unavailable browser storage must not block editing.
  const parsed = record.safeParse(saved);
  if (!parsed.success) return draft;
  for (const field of Object.keys(current) as (keyof Settings)[]) {
    if (JSON.stringify(parsed.data.values[field]) !== JSON.stringify(parsed.data.base[field])) {
      Object.assign(draft.values, {[field]: parsed.data.values[field]});
    }
  }
  return draft;
}

export function retainGuideDraft(storage: Storage, key: string, draft: Draft): boolean {
  try {
    if (JSON.stringify(draft.values) === JSON.stringify(draft.base)) storage.removeItem(key);
    else storage.setItem(key, JSON.stringify(draft));
    return true;
  } catch { return false; } // Surface storage failure in the editor; keep the in-memory draft.
}
