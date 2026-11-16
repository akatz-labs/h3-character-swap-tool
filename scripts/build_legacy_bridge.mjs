/** Local development rebuilds the one shared editor package. */
import {execFileSync} from 'node:child_process';
execFileSync('npm',['run','build','--workspace','@akatz/h3-editor-core'],{stdio:'inherit'});
