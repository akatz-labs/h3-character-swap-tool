/** Machine-readable bridge to the shared resolver for Python setup tools. */
import {parseArgs} from 'node:util';
import {ecosystemPaths,readInstallation,saveInstallation} from '@akatz/app-config/ecosystem';
const {values}=parseArgs({options:{action:{type:'string',default:'paths'}}});
const appId='h3-workbench';
if(values.action==='paths') console.log(JSON.stringify({...await ecosystemPaths(appId),installation:await readInstallation(appId)},null,2));
else if(values.action==='select'){
 let body='';for await(const chunk of process.stdin){body+=chunk;if(body.length>16384)throw Error('Installation record is too large');}
 const {installation,expected}=JSON.parse(body);
 if(installation?.appId!==appId)throw Error('Wrong app installation');
 console.log(JSON.stringify(await saveInstallation(installation,expected)));
}else throw Error('Use --action paths or --action select');
