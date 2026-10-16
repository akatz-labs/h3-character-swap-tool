import { createComfyControl } from '@akatz/app-config/managed-comfy-driver';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir, copyFile, stat, readdir, lstat, rename } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve, dirname } from 'node:path';
import { compatibility, hash, identifier, integer, record, sessionStatus, text } from '@akatz/app-config/session-contracts';
import type { RuntimeCompatibility } from '@akatz/app-config/session-contracts';
import type { RuntimeDriverContext, SessionRecord } from '@akatz/app-config/runtime-sessions';

interface H3Configuration {
  python: string;
  controller: string;
  controllerRevision: string;
  contract: string;
  contractRevision: string;
  cgExecutable: string;
  environmentPath: string;
  mediaRoot: string;
  ownerStateDirectory: string;
  port: number;
  allowedNodes: string[];
  modelNames?: string[];
  operationModels?: Partial<Record<H3Operation, string[]>>;
}
export type H3Operation = 'sam-frame' | 'sam-track' | 'sam-recover' | 'h3-render' | 'h3-render-segments-v1';
interface H3Input {
  version: 1;
  operation: H3Operation;
  operationRevision: string;
  graphFile: string;
  graphDigest: string;
  runtimeCompatibility: RuntimeCompatibility;
  family: { label: string; key: string; loras: string[] };
  profile: string;
  frames: number;
  dimensions: [number, number];
}
interface FileIdentity {name: string; hash: string; size: number; mime?: string}
interface AdapterContext {
  job: {id: string; sourceJobId: string; feature: string; inputs: unknown; files: FileIdentity[]};
  installation: {revision: string};
  directory: string;
  runtimeSession: SessionRecord | null;
  checkpoint(phase: 'preparing' | 'rendering' | 'finalizing', details?: Record<string, unknown>): Promise<void>;
  isCancellationRequested(): boolean;
  inputFile(name: string): string;
  outputFile(name: string): string;
}
const pause = (ms: number): Promise<void> => new Promise(done => setTimeout(done, ms));
const terminal = (message: string, cancelled = false): Error => Object.assign(new Error(message), {terminal: true, cancelled});
const operations: H3Operation[] = ['sam-frame','sam-track','sam-recover','h3-render','h3-render-segments-v1'];
const roots: Record<H3Operation, string[]> = {
  'sam-frame': ['H3CharacterSwapRefineMask','H3CharacterSwapSaveMask'],
  'sam-track': ['SAM3_VideoTrack','H3CharacterSwapSaveMask'],
  'sam-recover': ['H3CharacterSwapSaveMask'],
  'h3-render': ['H3CharacterSwapAssemble','H3CharacterSwapWindowPlan'],
  'h3-render-segments-v1': ['H3CharacterSwapAssemble','H3CharacterSwapWindowPlan'],
};
function absolute(value: unknown, label: string): string {
  const path = text(value, label);
  if (!isAbsolute(path)) throw new Error(label + ' requires a worker-local absolute path');
  return path;
}
function confined(root: string, path: string): string {
  const result = resolve(root, path), rel = relative(root, result);
  if (!rel || rel === '..' || rel.startsWith('../') || rel.startsWith('..\\') || isAbsolute(rel)) throw new Error('Path escapes the operation root');
  return result;
}
export function h3Configuration(value: unknown): H3Configuration {
  const v = record(value);
  if (Object.keys(v).some(k => !['python','controller','controllerRevision','contract','contractRevision','cgExecutable','environmentPath','mediaRoot','ownerStateDirectory','port','allowedNodes','modelNames','operationModels'].includes(k))) throw new Error('Unknown H3 installation configuration');
  const port = integer(v.port, 'runtime port', 1);
  if (port > 65535 || !Array.isArray(v.allowedNodes) || !v.allowedNodes.length) throw new Error('Invalid H3 runtime configuration');
  const modelNames = v.modelNames === undefined ? undefined : modelList(v.modelNames);
  const operationModels: Partial<Record<H3Operation, string[]>> = {};
  if (v.operationModels !== undefined) for (const [op, names] of Object.entries(record(v.operationModels))) {
    if (!operations.includes(op as H3Operation)) throw new Error('Unknown operation model requirement');
    operationModels[op as H3Operation] = modelList(names);
  }
  return {python: absolute(v.python, 'Python'), controller: absolute(v.controller, 'Controller'), controllerRevision: hash(v.controllerRevision),
    contract: absolute(v.contract, 'Contract'), contractRevision: hash(v.contractRevision), cgExecutable: absolute(v.cgExecutable, 'ComfyGit'), environmentPath: absolute(v.environmentPath, 'Environment'),
    mediaRoot: absolute(v.mediaRoot, 'Media root'), ownerStateDirectory: absolute(v.ownerStateDirectory, 'Ownership guard'), port,
    allowedNodes: v.allowedNodes.map(n => identifier(n)), ...(modelNames ? {modelNames, operationModels} : {})};
}
function modelList(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('Declared model names require an array');
  return value.map(n => {const name = text(n, 'model name');
    if (isAbsolute(name) || name.includes('\\') || name.split('/').some(p => !p || p === '.' || p === '..')) throw new Error('Unsafe model name');
    return name;
  });
}
export function requiredGraphModels(graph: Record<string, unknown>, catalog: string[], implicit: string[] = []): string[] {
  const selected = new Set(implicit), aliases = new Map<string, string[]>();
  for (const name of catalog) for (const key of [name, name.slice(name.indexOf('/') + 1), name.split('/').at(-1)!]) aliases.set(key, [...(aliases.get(key) || []), name]);
  function inspect(v: unknown): void {
    if (typeof v === 'string' && /\.(?:safetensors|onnx|gguf|pt|pth)$/i.test(v)) {
      const matches = [...new Set(aliases.get(v) || [])];
      if (matches.length !== 1) throw terminal('Operation requires an undeclared or ambiguous model: ' + v);
      selected.add(matches[0]!);
    } else if (Array.isArray(v)) v.forEach(inspect);
    else if (v && typeof v === 'object') Object.values(record(v)).forEach(inspect);
  }
  Object.values(graph).forEach(n => inspect(record(n).inputs));
  if ([...selected].some(n => !catalog.includes(n))) throw terminal('Implicit operation model is undeclared');
  return [...selected].sort();
}
export function h3Input(value: unknown): H3Input {
  const v = record(value);
  if (v.version !== 1 || !operations.includes(v.operation as H3Operation) || Object.keys(v).some(k => !['version','operation','operationRevision','graphFile','graphDigest','runtimeCompatibility','family','profile','frames','dimensions'].includes(k))) throw new Error('Unsupported H3 operation');
  const family = record(v.family);
  if (!Array.isArray(family.loras) || !Array.isArray(v.dimensions) || v.dimensions.length !== 2) throw new Error('Invalid H3 model/canvas metadata');
  const dimensions: [number, number] = [integer(v.dimensions[0], 'width', 32), integer(v.dimensions[1], 'height', 32)];
  if (dimensions.some(n => n > 16384 || n % 32)) throw new Error('H3 canvas must be aligned to 32 pixels');
  const profile = text(v.profile, 'profile');
  if (!['full','full-offload','full-stream','full-layer-stream'].includes(profile)) throw new Error('Unknown H3 residency profile');
  const runtimeCompatibility = compatibility(v.runtimeCompatibility);
  if (runtimeCompatibility.modelConfigurationDigest !== hash(family.key) || runtimeCompatibility.adapterRevision !== hash(v.operationRevision)) throw new Error('Model chain differs from its frozen operation compatibility');
  return {version: 1, operation: v.operation as H3Operation, operationRevision: hash(v.operationRevision), graphFile: identifier(v.graphFile), graphDigest: hash(v.graphDigest),
    runtimeCompatibility, family: {label: text(family.label, 'model family'), key: hash(family.key), loras: family.loras.map(n => text(n, 'LoRA'))},
    profile, frames: integer(v.frames, 'frame count', 1), dimensions};
}
async function fileHash(path: string): Promise<{hash: string; size: number}> {
  const stream = createReadStream(path), digest = createHash('sha256'); let size = 0;
  for await (const chunk of stream) {digest.update(chunk); size += chunk.length;}
  return {hash: digest.digest('hex'), size};
}
async function atomic(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), {recursive: true});
  const temporary = path + '.' + process.pid + '.pending';
  await writeFile(temporary, JSON.stringify(value, null, 2) + '\n', {mode: 0o600});
  await rename(temporary, path);
}
const comfyControl = createComfyControl(h3Configuration);
const {specification, control} = comfyControl;
export const {inspectRuntime, setPendingAdmissions, prepareRuntime, releaseRuntime} = comfyControl;

export function validateGraph(value: unknown, input: H3Input, allowedNodes: string[]): Record<string, unknown> {
  const graph = record(value), classes: string[] = [];
  if (!Object.keys(graph).length || Object.keys(graph).length > 2000) throw new Error('Invalid captured H3 graph');
  for (const [key, node] of Object.entries(graph)) {
    identifier(key); const n = record(node), type = text(n.class_type, 'node class'); record(n.inputs);
    if (!allowedNodes.includes(type)) throw new Error('Captured operation requires an undeclared node: ' + type);
    classes.push(type);
  }
  for (const required of roots[input.operation]) if (!classes.includes(required)) throw new Error('Graph differs from the named operation contract: ' + required);
  if (input.operation.startsWith('h3-render') && !classes.includes('H3CharacterSwapController')) throw new Error('H3 operation has no declared generation controller');
  if(input.operation === 'h3-render-segments-v1' && !Object.values(graph).some(n => record(n).class_type === 'H3CharacterSwapShot' && record(record(n).inputs).segment_mode === true)) throw new Error('Segment operation requires an explicit single-pass shot');
  return graph;
}
/** Give each accepted SDK stage its own input names, including multi-stage jobs.
 * Keep the original graph separately; only complete declared file-name values
 * change in the transport graph. Text prompts and model paths remain intact. */
export function transportGraph(graph: Record<string, unknown>, jobId: string, names: string[]): Record<string, unknown> {
  identifier(jobId); names.forEach(identifier);
  const mapping = new Map(names.map(name => [name, jobId + '-' + name]));
  const rewrite = (value: unknown): unknown => {
    if (typeof value === 'string') return mapping.get(value) || value;
    if (Array.isArray(value)) return value.map(rewrite);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(record(value)).map(([k,v]) => [k,rewrite(v)]));
    return value;
  };
  const fileFields = new Set(['file','image','video','audio','path','directory','source_video','mask_file','prior_file']);
  return Object.fromEntries(Object.entries(graph).map(([id, value]) => {
    const node = record(value), inputs = record(node.inputs);
    return [id, {...node, inputs: Object.fromEntries(Object.entries(inputs).map(([key,input]) =>
      [key, fileFields.has(key) || /_(?:file|path)s?$/.test(key) ? rewrite(input) : input]))}];
  }));
}
async function retainedFiles(root: string, prefix = ''): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(join(root, prefix), {withFileTypes: true})) {
    const name = prefix ? prefix + '/' + entry.name : entry.name;
    if (entry.isSymbolicLink()) throw new Error('Symlink in retained H3 outputs');
    if (entry.isDirectory()) result.push(...await retainedFiles(root, name));
    else if (entry.isFile()) result.push(name);
  }
  return result;
}
async function terminalDiagnostics(context: AdapterContext, driverContext: RuntimeDriverContext, stage: string,
  outcome: 'cancelled' | 'failed', observation: Record<string, unknown>): Promise<Error> {
  const {spec} = await specification(driverContext), files: FileIdentity[] = [];
  for (const name of ['api.json','attempt.json','submission.json','history.json','history.original.json','preflight.json','failure.json','cancelled.json','host-capacity.json']) {
    const source = join(stage, name);
    if (!await stat(source).catch(() => null)) continue;
    const destination = context.outputFile(name); await copyFile(source, destination); files.push({name, ...await fileHash(destination)});
  }
  const release = join(String(spec.root), 'release.json');
  if (await stat(release).catch(() => null)) {const destination = context.outputFile('release.json'); await copyFile(release, destination); files.push({name: 'release.json', ...await fileHash(destination)});}
  const receipts = record(observation.stage), failure = receipts.failure ? record(receipts.failure) : null;
  const evidence = {version: 1, outcome, jobId: context.job.id, runtimeSessionId: driverContext.session.id,
    stage: receipts, session: observation.session, graphDigest: h3Input(context.job.inputs).graphDigest};
  const evidenceDigest = createHash('sha256').update(JSON.stringify(evidence)).digest('hex');
  return Object.assign(terminal(outcome === 'cancelled' ? 'Owned H3 prompt stopped and GPU release verified' : String(failure?.error || 'Owned H3 runtime failed'), outcome === 'cancelled'),
    {files, evidence: {...evidence, terminalEvidenceDigest: evidenceDigest, gpuReleased: true}});
}
interface ObservationContext {
  job: AdapterContext['job'];
  installation: AdapterContext['installation'];
  directory: string;
  runtimeSession: SessionRecord | null;
}
export async function inspectAcceptedAttempt(context: ObservationContext): Promise<unknown> {
  const input = h3Input(context.job.inputs), session = context.runtimeSession;
  const attemptId = context.job.id + '-stage-1';
  const uncertain = (reason: string): unknown => ({version: 1, outcome: 'uncertain', jobId: context.job.id, attemptId,
    reason, evidenceDigest: createHash('sha256').update(reason).digest('hex')});
  if (!session || context.installation.revision !== input.operationRevision) return uncertain('Accepted runtime/operation binding is missing or changed');
  const packetBytes = await readFile(join(context.directory, 'runtime-admission.json'), 'utf8').catch(error => {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error;});
  if (!packetBytes) return uncertain('No durable runtime admission exists; non-submission is not inferred');
  const packet = record(JSON.parse(packetBytes));
  if (packet.job !== context.job.id || packet.attempt !== attemptId || packet.captured_graph_sha256 !== input.graphDigest) return uncertain('Runtime admission identity differs');
  const stage = text(packet.directory, 'stage directory');
  const graphPath = text(packet.graph, 'captured graph');
  if ((await fileHash(graphPath)).hash !== packet.graph_sha256 || (await fileHash(join(stage, 'captured-api.json'))).hash !== input.graphDigest) return uncertain('Accepted graph bytes changed');
  const driverContext: RuntimeDriverContext = {session, descriptor: session.descriptor, compatibility: input.runtimeCompatibility};
  const observation = record(await control(driverContext, 'observe', {directory: stage, attempt: attemptId}));
  const accepted = observation.accepted;
  if (!accepted || JSON.stringify(accepted) !== JSON.stringify(packet)) return uncertain('The same runtime inbox acceptance cannot be verified');
  const status = sessionStatus(observation.session), receipts = record(observation.stage);
  if (status.id !== session.id || status.owner !== session.owner || status.recipeRevision !== input.runtimeCompatibility.recipeRevision) return uncertain('Runtime ownership differs from the saved binding');
  const evidenceDigest = createHash('sha256').update(JSON.stringify({packet, status, receipts})).digest('hex');
  if (receipts.complete) {
    const complete = record(receipts.complete);
    if (complete.attempt !== attemptId || complete.queue_empty !== true) return uncertain('Completion has no matching terminal queue proof');
    const manifest = record(JSON.parse(await readFile(join(stage, 'output-receipt.json'), 'utf8')));
    if (!Array.isArray(manifest.files) || !manifest.files.length) return uncertain('Completed artifact manifest is missing');
    const files: Array<{name: string; hash: string; size: number; mime: string; role: string}> = [];
    for (const value of manifest.files) {
      const f = record(value), name = text(f.path, 'artifact name'), path = confined(join(stage, 'output'), name);
      const actual = await fileHash(path);
      if ((await lstat(path)).isSymbolicLink() || actual.hash !== hash(f.sha256) || actual.size !== f.bytes) return uncertain('Retained completion artifact differs');
      files.push({name: 'output/' + name, ...actual, mime: 'application/octet-stream', role: 'original'});
    }
    return {version: 1, outcome: 'terminal', jobId: context.job.id, attemptId, evidenceDigest,
      manifest: {version: 1, jobId: context.job.id, attemptId, files}};
  }
  if ((receipts.failure || receipts.cancelled) && status.phase === 'stopped' && status.releaseProof) {
    if (receipts.cancelled && record(receipts.cancelled).job !== context.job.id) return uncertain('Cancellation belongs to another accepted job');
    const files: Array<{name: string; hash: string; size: number; mime: string; role: string}> = [];
    for (const name of ['failure.json','cancelled.json']) {
      if (!receipts[name.split('.')[0]!]) continue;
      const path = confined(stage, name);
      if ((await lstat(path)).isSymbolicLink()) return uncertain('Terminal diagnostic is a symlink');
      const actual = await fileHash(path);
      if (JSON.stringify(JSON.parse(await readFile(path, 'utf8'))) !== JSON.stringify(receipts[name.split('.')[0]!])) return uncertain('Terminal diagnostic changed after observation');
      files.push({name, ...actual, mime: 'application/json', role: 'terminal-evidence'});
    }
    if (files.length) return {version: 1, outcome: 'terminal', jobId: context.job.id, attemptId, evidenceDigest,
      manifest: {version: 1, jobId: context.job.id, attemptId, files}};
  }
  if (receipts.failure || receipts.cancelled || ['needs_inspection','stopped','stopping'].includes(status.phase)) return uncertain('Accepted stage lacks verifiable active/retained completion evidence');
  const backendId = receipts.submission ? text(record(receipts.submission).prompt_id, 'prompt ID') : attemptId;
  return {version: 1, outcome: 'active', jobId: context.job.id, attemptId, runtimeSessionId: session.id, backendId, evidenceDigest};
}
export async function resumeAccepted(context: AdapterContext): Promise<Record<string, unknown>> {
  const proof = record(await inspectAcceptedAttempt(context));
  if (!['active','terminal'].includes(String(proof.outcome))) throw new Error('The saved accepted attempt cannot be observed safely: ' + String(proof.reason));
  return run(context);
}
export async function run(context: AdapterContext): Promise<Record<string, unknown>> {
  const input = h3Input(context.job.inputs);
  if (!context.runtimeSession || input.operation !== context.job.feature || input.operationRevision !== context.installation.revision) throw terminal('H3 operation binding differs from the accepted installation');
  const session = context.runtimeSession;
  const driverContext: RuntimeDriverContext = {session, descriptor: session.descriptor, compatibility: input.runtimeCompatibility};
  const {config, spec} = await specification(driverContext);
  const graphFile = context.inputFile(input.graphFile);
  if ((await fileHash(graphFile)).hash !== input.graphDigest) throw terminal('Captured H3 graph bytes changed');
  let originalGraph:Record<string,unknown>;
  try{originalGraph = validateGraph(JSON.parse(await readFile(graphFile, 'utf8')), input, config.allowedNodes);}
  catch(error){throw terminal(error instanceof Error?error.message:'Invalid named operation graph');}
  const graph = transportGraph(originalGraph, context.job.id, context.job.files.filter(f => f.name !== input.graphFile).map(f => f.name));
  const stage = join(config.mediaRoot, 'jobs', context.job.id), attempt = context.job.id + '-stage-1';
  await mkdir(stage, {recursive: true});
  const stageGraph = join(stage, 'api.json');
  const frozen = await readFile(stageGraph, 'utf8').catch(error => {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error;});
  if (frozen && JSON.stringify(JSON.parse(frozen)) !== JSON.stringify(graph)) throw new Error('Accepted stage graph differs; no automatic replay');
  if (!frozen) await writeFile(stageGraph, JSON.stringify(graph, null, 2));
  const capturedGraph = join(stage, 'captured-api.json');
  if (!await stat(capturedGraph).catch(() => null)) await copyFile(graphFile, capturedGraph);
  if ((await fileHash(capturedGraph)).hash !== input.graphDigest) throw new Error('Original captured graph changed');
  const packetFile = join(context.directory, 'runtime-admission.json');
  const prior = await readFile(packetFile, 'utf8').catch(error => {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error;});
  const inputRoot = String(spec.input_directory); await mkdir(inputRoot, {recursive: true});
  const staging: Array<{name: string; source: string; target: string; sha256: string; created: boolean}> = [];
  for (const file of prior ? [] : context.job.files.filter(f => f.name !== input.graphFile)) {
    identifier(file.name); hash(file.hash);
    const source = context.inputFile(file.name), target = confined(inputRoot, context.job.id + '-' + file.name), actual = await fileHash(source);
    if (actual.hash !== file.hash || actual.size !== file.size || (await lstat(source)).isSymbolicLink()) throw terminal('Uploaded H3 input identity differs');
    const exists = await stat(target).catch(error => {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error;});
    if (!exists) await copyFile(source, target);
    if ((await fileHash(target)).hash !== file.hash || (await lstat(target)).isSymbolicLink()) throw terminal('H3 input staging collision');
    staging.push({name: file.name, source, target, sha256: file.hash, created: !exists});
  }
  const packet = {job: context.job.id, stage: input.operation, attempt, directory: stage, graph: stageGraph,
    graph_sha256: (await fileHash(stageGraph)).hash, captured_graph_sha256: input.graphDigest,
    profile: input.profile, placement: input.runtimeCompatibility.residency, family: input.family, compatibility: input.runtimeCompatibility, staged_inputs: staging,
    ...(config.modelNames ? {required_models: requiredGraphModels(originalGraph, config.modelNames, config.operationModels?.[input.operation])} : {})};
  // Retain immutable acceptance input before any enqueue may take effect.
  const acceptedPacket = prior ? record(JSON.parse(prior)) : packet;
  if (!prior) await atomic(packetFile, packet);
  if (prior && (acceptedPacket.graph_sha256 !== packet.graph_sha256 || acceptedPacket.captured_graph_sha256 !== input.graphDigest || acceptedPacket.attempt !== attempt || acceptedPacket.directory !== stage)) throw new Error('Saved H3 admission identity differs');
  if (context.isCancellationRequested() && !prior) throw terminal('Cancelled before runtime admission', true);
  const admission = record(await control(driverContext, 'enqueue', {packet: acceptedPacket}));
  if (admission.accepted !== true) throw new Error('Owned session did not accept this stage; inspect before retry');
  await context.checkpoint('preparing', {attemptId: attempt, runtimeSessionId: session.id, stageDirectory: stage, graphDigest: input.graphDigest, admissionAccepted: true});
  let cancellationSent = false;
  while (true) {
    if (context.isCancellationRequested() && !cancellationSent) {await control(driverContext, 'cancel', {job: context.job.id}); cancellationSent = true;}
    const observation = record(await control(driverContext, 'observe', {directory: stage}));
    const status = sessionStatus(observation.session), receipts = record(observation.stage);
    if (receipts.complete) {
      const complete = record(receipts.complete);
      if (complete.attempt !== attempt || complete.queue_empty !== true) throw new Error('H3 terminal receipt identity differs');
      // Completion won a cancellation race: retain the valid output and report
      // completed rather than claiming that generation was cancelled.
      await context.checkpoint('finalizing', {attemptId: attempt, runtimeSessionId: session.id, backendId: text(complete.prompt_id, 'prompt ID'), graphDigest: input.graphDigest, gpuTerminal: true});
      const manifest = record(JSON.parse(await readFile(join(stage, 'output-receipt.json'), 'utf8')));
      if (!Array.isArray(manifest.files) || !manifest.files.length) throw new Error('H3 completed without retained artifacts');
      for (const item of manifest.files) {
        const f = record(item), name = text(f.path, 'output name'), path = confined(join(stage, 'output'), name);
        if ((await lstat(path)).isSymbolicLink() || (await fileHash(path)).hash !== hash(f.sha256)) throw new Error('Retained H3 output changed');
      }
      const names = [...await retainedFiles(join(stage, 'output')).then(items => items.map(n => 'output/' + n)),
        'api.json','captured-api.json','attempt.json','submission.json','history.json','history.original.json','preflight.json','complete.json','output-receipt.json'];
      const files: FileIdentity[] = [];
      for (const name of names) {const destination = context.outputFile(name); await mkdir(dirname(destination), {recursive: true}); await copyFile(confined(stage, name), destination); files.push({name, ...await fileHash(destination)});}
      await control(driverContext, 'finish', {job: context.job.id, state: 'success'});
      return {version: 1, operation: input.operation, domainJobId: context.job.sourceJobId, attemptId: attempt, runtimeSessionId: session.id,
        backendId: complete.prompt_id, graphDigest: input.graphDigest, runtimeStage: stage, gpuTerminal: true, completionWonCancellation: cancellationSent, files};
    }
    if (receipts.cancelled && status.phase === 'stopped' && status.releaseProof) {
      await control(driverContext, 'finish', {job: context.job.id, state: 'cancelled'});
      throw await terminalDiagnostics(context, driverContext, stage, 'cancelled', observation);
    }
    if (receipts.failure && status.phase === 'stopped' && status.releaseProof) {
      await control(driverContext, 'finish', {job: context.job.id, state: 'failed'});
      throw await terminalDiagnostics(context, driverContext, stage, 'failed', observation);
    }
    if (status.phase === 'needs_inspection' || status.phase === 'stopped') throw new Error('H3 accepted attempt lost runtime evidence; no automatic replay');
    if (receipts.submission) {
      const submission = record(receipts.submission);
      await context.checkpoint('rendering', {attemptId: attempt, runtimeSessionId: session.id, backendId: text(submission.prompt_id, 'prompt ID'), graphDigest: input.graphDigest, stageDirectory: stage, gpuTerminal: false});
    }
    await pause(500);
  }
}
