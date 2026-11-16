import {Icon} from '@akatz/app-kit/react';
export function WorkspaceTabs({items,value,onSelect,label,className='',navRef}:{items:{id:string;label:string;icon:React.ComponentProps<typeof Icon>['name'];indicator?:React.ReactNode}[];value:string;onSelect:(id:string)=>void;label:string;className?:string;navRef?:React.Ref<HTMLElement>}){
  return <nav ref={navRef} className={'h3-workspace-tabs '+className} aria-label={label}>{items.map(tab=><button key={tab.id} data-page={tab.id} aria-current={value===tab.id?'page':undefined} onClick={()=>onSelect(tab.id)}><Icon name={tab.icon}/>{tab.label}{tab.indicator}</button>)}</nav>;
}
