"""Finalize a portable image contract without acquiring any model bytes."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent))
from configure_installation import configure

root=Path('/opt/h3-worker')
catalog=json.loads((root/'recipe/model-identities.json').read_text())['models']
profile=json.loads((root/'resource-catalog.json').read_text())
model_names=[m['relativePath'] for m in profile['models']]
catalog={name:catalog[name] for name in model_names}
contract=json.loads((root/'runtime/runtime-contract.json').read_text())
contract.update(models=catalog,stage_model_validation=True,startup_models=[])
(root/'runtime/runtime-contract.json').write_text(json.dumps(contract,indent=2))
configuration=json.loads((root/'configuration.json').read_text())
configure(root/'runtime',root/'binding',root/'dist/worker/h3-adapter.mjs',configuration,model_names=model_names,features=[f['id'] for f in profile['features']])
# configure preserves machine-verified identities when present. The image has no
# models, so the full content catalog is retained for acquisition after build.
bound=json.loads((root/'binding/runtime-contract.json').read_text());bound['models']=catalog
(root/'binding/runtime-contract.json').write_text(json.dumps(bound,indent=2))
manifest=json.loads((root/'binding/installation.json').read_text())
# Code installation must succeed before models are acquired. This internal
# capability has only the runtime probes; operation capabilities retain all
# their model probes and cannot be admitted until those files are present.
manifest['features'] = ['runtime-installed', *manifest['features']]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest['runtime']['managed']['configuration']['contractRevision']=sha(root/'binding/runtime-contract.json')
for probe in manifest['probes']:
    if probe['path']==str(root/'binding/runtime-contract.json'):probe['sha256']=sha(root/'binding/runtime-contract.json')
# Contract changes participate in installation identity; no old binding is rewritten.
identity=hashlib.sha256(json.dumps({'revision':manifest['revision'],'configuration':manifest['runtime']['managed']['configuration'],'features':manifest['features'],'probes':manifest['probes']},sort_keys=True).encode()).hexdigest()
manifest['id']='h3-'+identity[:20]
(root/'binding/installation.json').write_text(json.dumps(manifest,indent=2))
(root/'image-runtime.json').write_text(json.dumps({'schemaVersion':1,'recipeRevision':bound['recipe_revision'],'installationId':manifest['id'],'modelsIncluded':False,'dependencyResolutionAtStartup':False},indent=2))
