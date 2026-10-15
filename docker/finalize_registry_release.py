"""Verify the public alpha pair and write digest pins after authorized publishing.

Uses anonymous pull access only. Account credentials are never read or accepted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


def public_manifest(repository, tag):
    query = urllib.parse.urlencode({
        'service': 'registry.docker.io', 'scope': f'repository:{repository}:pull'})
    with urllib.request.urlopen('https://auth.docker.io/token?' + query, timeout=30) as response:
        token = json.load(response)['token']
    request = urllib.request.Request(
        f'https://registry-1.docker.io/v2/{repository}/manifests/{tag}',
        headers={'Authorization': 'Bearer ' + token,
                 'Accept': 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        digest = 'sha256:' + hashlib.sha256(body).hexdigest()
        if response.headers.get('Docker-Content-Digest') != digest:
            raise ValueError('Registry manifest digest differs from returned bytes')
    manifest = json.loads(body)
    if manifest.get('schemaVersion') != 2 or not manifest.get('config', {}).get('digest'):
        raise ValueError('Expected the reviewed single-platform image manifest')
    return digest, manifest['config']['digest']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-pair', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    pair = json.loads(args.local_pair.read_text())
    if set(pair['images']) != {'app', 'worker'}:
        raise ValueError('Expected a complete reviewed pair')
    pins = []
    for name in ['app', 'worker']:
        image = pair['images'][name]
        repository, tag = image['tag'].rsplit(':', 1)
        if repository != f'akatzai/h3-workbench-{name}' or tag != pair['version']:
            raise ValueError('Image target differs from reviewed release')
        digest, config = public_manifest(repository, tag)
        if config != image['localImageId']:
            raise ValueError(f'Public {name} configuration differs from reviewed image')
        image.update(registryManifestDigest=digest, publication='public-manifest-verified')
        pins.append(f'H3_{name.upper()}_IMAGE={repository}@{digest}')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'images.env').write_text('\n'.join(pins + ['H3_PORT=8898', '']) )
    (args.output / 'image-pair.json').write_text(json.dumps(pair, indent=2) + '\n')
    print(json.dumps({'version': pair['version'], 'publicManifestsVerified': 2,
                      'output': str(args.output)}))


if __name__ == '__main__':
    main()
