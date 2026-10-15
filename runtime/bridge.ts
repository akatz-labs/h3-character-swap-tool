/** Narrow stdin/stdout bridge. H3 keeps its domain ledger and collection state. */
import { readFile, writeFile, rename, mkdir, stat, unlink } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import {fileURLToPath} from 'node:url';
import { ExecutorClient, closeExecutorConnections } from '@akatz/app-config/executor-client';
import type {ExecutorTarget, ExecutorJob, ExecutorSubmission, ExecutorInstallation, ExecutorFile} from '@akatz/app-config/executor-client';
import { compatibility, executionBinding, hash, identifier, record, text } from '@akatz/app-config/session-contracts';
import { h3Input } from './h3-adapter.js';
import {placementSnapshot,rankPlacement} from '@akatz/app-config/auto-placement';

interface Binding {version: 1; target: ExecutorTarget; executorId: string; installationId: string; revision: string; recipeRevision: string; feature: string}
function target(value: unknown): ExecutorTarget {
  const v = record(value), port = v.port;
  if (!['local','ssh'].includes(String(v.kind)) || typeof port !== 'number' || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid executor target');
  const result: ExecutorTarget = {kind: v.kind as ExecutorTarget['kind'], port};
  if (v.kind === 'ssh') {
    if (typeof v.host !== 'string' || !/^(?:[a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(v.host)) throw new Error('Invalid executor SSH alias');
    result.host = v.host;
    if (v.sshPort !== undefined) {
      if (typeof v.sshPort !== 'number' || !Number.isInteger(v.sshPort) || v.sshPort < 1 || v.sshPort > 65535) throw new Error('Invalid SSH port');
      result.sshPort = v.sshPort;
    }
  }
  return result;
}
function binding(value: unknown): Binding {
  const v = record(value);
  if (v.version !== 1) throw new Error('Unsupported bridge binding');
  return {version: 1, target: target(v.target), executorId: identifier(v.executorId), installationId: identifier(v.installationId),
    revision: hash(v.revision), recipeRevision: hash(v.recipeRevision), feature: identifier(v.feature)};
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object') {
    const v = record(value); return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
  }
  return JSON.stringify(value);
}
const digest = (v: unknown): string => createHash('sha256').update(canonical(v)).digest('hex');
const emit = (type: string, value: unknown): void => {process.stdout.write(JSON.stringify({type, value}) + '\n');};
const requiredCapabilities = ['managed-runtime-v1','accepted-attempt-v1','cancel-admission-v1'];
async function atomic(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), {recursive: true});
  const temporary = path + '.' + process.pid + '.pending';
  await writeFile(temporary, JSON.stringify(value, null, 2) + '\n', {mode: 0o600}); await rename(temporary, path);
}
async function stored(path: string): Promise<unknown | null> {
  try {return JSON.parse(await readFile(path, 'utf8'));} catch (error) {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error;}
}
async function boundClient(b: Binding): Promise<ExecutorClient> {
  const client = new ExecutorClient(b.target), health = await client.health();
  if (health.executorId !== b.executorId) throw new Error('Accepted executor identity changed; binding cannot be redirected');
  for (const capability of requiredCapabilities) if (!health.capabilities?.includes(capability)) throw new Error('This executor lacks ' + capability);
  return client;
}
export async function bind(targetValue: unknown, featureValue: unknown, installationValue?: unknown): Promise<Binding> {
  const t = target(targetValue), feature = identifier(featureValue), client = new ExecutorClient(t), health = await client.health();
  for (const capability of requiredCapabilities) if (!health.capabilities?.includes(capability)) throw new Error('The selected executor needs ' + capability + ' before H3 can use it');
  const readiness = await client.readiness('h3-workbench');
  const installationId = installationValue === undefined ? null : identifier(installationValue);
  const selected = readiness.find(r => r.features.includes(feature) && r.installation.runtime && record(r.installation.runtime).managed && (!installationId || r.installation.id === installationId));
  if (!selected) throw new Error('The selected worker has no ready H3 operation: ' + feature);
  const managed = record(record(selected.installation.runtime).managed);
  return {version: 1, target: t, executorId: health.executorId, installationId: selected.installation.id, revision: hash(selected.installation.revision), recipeRevision: hash(managed.recipeRevision), feature};
}
async function bindAuto(v:Record<string,unknown>):Promise<unknown>{
  if(!Array.isArray(v.candidates)||!v.candidates.length||v.candidates.length>16)throw new Error('Auto requires explicitly allowed worker targets');
  const desired=record(v.compatibility),feature=identifier(v.feature);
  if(Object.keys(desired).some(k=>!['modelConfigurationDigest','precision','residency','allocator'].includes(k)))throw new Error('Unknown Auto compatibility field');
  const checked=compatibility({recipeRevision:'a'.repeat(64),adapterRevision:'a'.repeat(64),modelDigest:'a'.repeat(64),...desired});
  const names=new Set<string>();
  const candidates=v.candidates.map((value,index)=>{const candidate=record(value),worker=identifier(candidate.worker);if(names.has(worker))throw new Error('Duplicate Auto worker');names.add(worker);return {candidate,worker,index};});
  const observed=await Promise.all(candidates.map(async({candidate,worker,index})=>{
    try{
      const b=await bind(candidate.target,feature,candidate.installationId),client=await boundClient(b),health=await client.health();
      if(!health.capabilities?.includes('auto-placement-v1'))return null;
      const snapshot=placementSnapshot(await client.json('/v1/status'),b.executorId);if(snapshot.fenced)return null;
      const chain={...checked,recipeRevision:b.recipeRevision,adapterRevision:b.revision,modelDigest:b.recipeRevision};
      return {worker,binding:b,index,...rankPlacement(snapshot,b.installationId,chain)};
    }catch{return null;}
  }));
  const selected=observed.filter(r=>r!==null).sort((a,b)=>a.score-b.score||a.index-b.index)[0];
  if(!selected)throw Object.assign(new Error('Allowed workers are busy, offline, or lack verified Auto ownership support'),{status:409});
  return {worker:selected.worker,binding:selected.binding,placement:selected.decision};
}
async function execute(v: Record<string, unknown>): Promise<unknown> {
  const directory = text(v.directory, 'operation directory');
  if (!isAbsolute(directory)) throw new Error('Operation directory must be absolute');
  const b = binding(v.binding), client = await boundClient(b);
  const requestPath = join(directory, 'shared-request.json');
  const acceptedPath = join(directory, 'shared-accepted.json');
  const savedRequest = await stored(requestPath);
  let request: ExecutorSubmission;
  if (savedRequest) {
    const r = record(savedRequest);
    if (r.executorId !== b.executorId || r.installationId !== b.installationId || r.sourceJobId !== identifier(v.sourceJobId) || r.instanceId !== identifier(v.instanceId)) throw new Error('Saved shared admission binding changed');
    h3Input(r.inputs);
    if (canonical(h3Input(v.inputs)) !== canonical(h3Input(r.inputs))) throw new Error('Operation inputs changed under the saved admission identity');
    request = r as unknown as ExecutorSubmission;
  } else {
    const input = h3Input(v.inputs);
    if (input.runtimeCompatibility.recipeRevision !== b.recipeRevision || input.runtimeCompatibility.adapterRevision !== b.revision || input.operationRevision !== b.revision || input.operation !== b.feature) throw new Error('H3 request differs from its frozen installation');
    const mapping = record(v.files), files: ExecutorFile[] = [];
    for (const [name, location] of Object.entries(mapping)) {
      identifier(name); const path = text(location, 'source file');
      if (!isAbsolute(path) || !(await stat(path)).isFile()) throw new Error('Input file is unavailable');
      if (await stat(join(directory, 'bridge-cancel')).catch(() => null)) throw Object.assign(new Error('Cancelled before shared admission'), {cancelled: true, terminal: true});
      const abort = new AbortController();
      const timer = setInterval(() => {void stat(join(directory, 'bridge-cancel')).then(() => abort.abort(), () => {});}, 200);
      try {files.push(await client.upload(path, name, {signal: abort.signal}));}
      catch (error) {if (abort.signal.aborted) throw Object.assign(new Error('Cancelled during upload before shared admission'), {cancelled: true, terminal: true}); throw error;}
      finally {clearInterval(timer);}
    }
    if (!files.some(f => f.name === input.graphFile && f.hash === input.graphDigest)) throw new Error('Frozen graph upload differs');
    request = {appId: 'h3-workbench', instanceId: identifier(v.instanceId), sourceJobId: identifier(v.sourceJobId), executorId: b.executorId,
      installationId: b.installationId, feature: input.operation, inputs: input, files};
    const frozen = executionBinding({version: 1, backend: b.target.kind === 'ssh' ? 'shared-ssh' : 'shared-local', executorId: b.executorId, installationId: b.installationId,
      installationRevision: b.revision, operation: b.feature, operationRevision: b.revision, requestDigest: digest(request), targetId: identifier(v.targetId), requiredCapabilities}, requiredCapabilities);
    await atomic(join(directory, 'execution-binding.json'), frozen);
    await atomic(requestPath, request); // acceptance attempt marker before POST
  }
  const savedAcceptance = await stored(acceptedPath);
  let job: ExecutorJob;
  if (savedAcceptance) {
    const accepted = record(savedAcceptance);
    if (accepted.executorId !== b.executorId || accepted.requestDigest !== digest(request)) throw new Error('Saved acceptance receipt differs');
    job = await client.job(identifier(accepted.jobId));
  } else {
    // A lost response reconciles this identical admission/source identity.
    job = await stat(join(directory, 'bridge-cancel')).catch(() => null)
      ? await client.cancelAdmission(request) : await client.submit(request);
    await atomic(acceptedPath, {version: 1, executorId: b.executorId, jobId: job.id, requestDigest: digest(request), admittedAt: job.createdAt});
  }
  emit('accepted', {jobId: job.id, executorId: b.executorId, phase: job.phase});
  let sentCancel = false, lastPhase = '';
  while (true) {
    if (!sentCancel && await stat(join(directory, 'bridge-cancel')).catch(() => null)) {
      // Resolve acceptance first; never cancel by redirecting or guessing a job.
      try {await client.cancel(job.id);} catch (error) {
        if ((error as {status?: number}).status !== 409) throw error;
        const latest = await client.job(job.id);
        if (!['finalizing','completed','failed','cancelled'].includes(latest.phase)) throw error;
      }
      sentCancel = true;
    }
    job = await client.job(job.id);
    if (job.phase !== lastPhase) {lastPhase = job.phase; emit('phase', {jobId: job.id, phase: job.phase, error: job.error});}
    if (['completed','failed','cancelled','uncertain'].includes(job.phase)) break;
    await new Promise(done => setTimeout(done, 500));
  }
  if (job.phase === 'uncertain') return {version: 1, jobId: job.id, executorId: b.executorId, phase: job.phase, error: job.error || null};
  const result = job.phase === 'completed' ? record(job.result) : null;
  if (result && (result.version !== 1 || result.operation !== b.feature || result.domainJobId !== request.sourceJobId || result.graphDigest !== h3Input(request.inputs).graphDigest || result.gpuTerminal !== true || !Array.isArray(result.files))) throw new Error('Retained H3 result identity differs');
  const files = job.phase === 'completed' ? job.result?.files || [] : job.diagnostics?.files || [];
  const runtime = join(directory, 'runtime'); await mkdir(runtime, {recursive: true});
  for (const file of files) {
    const destination = resolve(runtime, file.name), rel = relative(runtime, destination);
    if (!rel || rel.startsWith('../') || rel.startsWith('..\\') || isAbsolute(rel)) throw new Error('Output escapes H3 collection root');
    await mkdir(dirname(destination), {recursive: true});
    // Publish only after the client's streamed SHA/size verification completes.
    const pending = destination + '.collecting';
    try {await client.download(job.id, file, pending); await rename(pending, destination);}
    finally {await unlink(pending).catch(error => {if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;});}
  }
  const receipt = {version: 1, jobId: job.id, executorId: b.executorId, phase: job.phase, directory: runtime,
    requestDigest: digest(request), result, diagnostics: job.diagnostics || null, error: job.error || null};
  await atomic(join(directory, 'shared-collected.json'), receipt);
  return receipt;
}
export async function bridge(value: unknown): Promise<unknown> {
  const v = record(value);
  if (v.action === 'bind') return bind(v.target, v.feature, v.installationId);
  if (v.action === 'bind-auto') return bindAuto(v);
  if (v.action === 'execute') return execute(v);
  if (v.action === 'status') {
    const client = new ExecutorClient(target(v.target));
    return {health: await client.health(), status: await client.json('/v1/status'), installations: await client.readiness('h3-workbench')};
  }
  if (v.action === 'release-idle') {
    const client = new ExecutorClient(target(v.target)), health = await client.health();
    if (!health.capabilities?.includes('managed-runtime-v1')) throw new Error('Worker has no verified managed-session release');
    return client.releaseMemory(health.executorId);
  }
  if (v.action === 'install') {
    const client = new ExecutorClient(target(v.target));
    const installation = record(v.installation);
    const plan = record(await client.json('/v1/plans', installation as unknown as ExecutorInstallation));
    return client.json('/v1/install', {planId: identifier(plan.id), digest: hash(plan.digest)});
  }
  if (v.action === 'job') {const b = binding(v.binding); return (await boundClient(b)).job(identifier(v.jobId));}
  if (v.action === 'cancel') {const b = binding(v.binding); return (await boundClient(b)).json('/v1/jobs/' + encodeURIComponent(identifier(v.jobId)) + '/cancel', {});}
  throw new Error('Unknown H3 bridge operation');
}
async function main(): Promise<void> {
  let input = ''; for await (const chunk of process.stdin) {input += String(chunk); if (input.length > 8 * 1024 ** 2) throw new Error('Bridge request exceeds its bound');}
  emit('result', await bridge(JSON.parse(input)));
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {await main();} catch (error) {
    const e = error as Error & {status?: number; cancelled?: boolean; terminal?: boolean};
    emit('error', {message: e.message, status: e.status || null, cancelled: e.cancelled === true, terminal: e.terminal === true}); process.exitCode = 1;
  } finally {await closeExecutorConnections();}
}
