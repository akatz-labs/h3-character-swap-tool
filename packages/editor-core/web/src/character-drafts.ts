import {z} from 'zod';

const fields = z.object({name:z.string().max(120),description:z.string().max(20000)});
const record = z.object({base:fields,values:fields});
type Fields = z.infer<typeof fields>;
type Storage = Pick<globalThis.Storage,'getItem'|'setItem'|'removeItem'>;

/** The caller scopes the key to the account, project and character. */
export function restoreCharacterDraft(storage:Storage,key:string,current:Fields):Fields {
  const base={name:current.name,description:current.description};
  let value:unknown;
  try { value=JSON.parse(storage.getItem(key)||'null'); }
  catch { return base; }
  const saved=record.safeParse(value);
  if(!saved.success)return base;
  return {
    name:saved.data.values.name!==saved.data.base.name?saved.data.values.name:current.name,
    description:saved.data.values.description!==saved.data.base.description?saved.data.values.description:current.description,
  };
}

export function retainCharacterDraft(storage:Storage,key:string,base:Fields,values:Fields):boolean {
  try {
    if(base.name===values.name&&base.description===values.description)storage.removeItem(key);
    else storage.setItem(key,JSON.stringify({base:{name:base.name,description:base.description},values}));
    return true;
  } catch { return false; }
}
