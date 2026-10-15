# Friend alpha release

Historical friend checkpoint: `0.1.0-alpha.2`, with a matched app/worker pair. This release is
prepared as a Docker image archive and Compose installation bundle. Docker Hub
publication, a public GitHub source repository and CI are subsequent steps.
The independent Cloud branch is outside this release.

## Accepted scope

The operator completed the import, mask, render and review flow on 2026-10-15
and accepted this as an experimental MVP. One take lost character identity;
a different seed worked. This is acceptance of an alpha, not evidence that
character swaps are consistent across inputs or seeds. V2 remains outside the
seven-model release profile.

The first page is Resources. Installed code is distinguished from missing
models, Install required acquires the missing required models, and Scan verifies
existing files in the mounted model folder. Projects become available through
the setup continuation when the required capabilities are ready. Existing
projects remain accessible during setup or a worker outage.

## Installation contract

Supply the complete installation bundle, matching image archive, notices and
source information. `docker load` imports the offline pair; `images.env` pins
versioned tags from that checksum-verified archive. This works across classic
and containerd image stores, which can report different local image IDs. Use
`README.md` for Compose commands, model reuse, first result and persistence.

A later Docker Hub release should publish the reviewed pair to `akatzai`, record
registry manifest digests and verify an actual pull before replacing the offline
pins. Publication requires the supported protected publishing path or a separately
authorized exception. An account name or local tag is not evidence of publication.

The executed host uses Linux x86-64, RTX 4090, 128 GB RAM, driver 610.43.02 and
NVIDIA CDI. The standard NVIDIA runtime override is syntax checked. WSL2 and
other GPU/driver combinations need actual qualification. The bundled CUDA
extension records a CUDA 13.0 RUNPATH while Torch is `2.11.0+cu128`; this does
not establish a minimum driver. Run the supplied native preflight on a new host.
A 1 MP full-residency test previously exhausted 24 GB VRAM; its explicit offload
retry completed. Do not infer a universal duration or VRAM guarantee.

## Distribution materials

First-party H3/App Kit MIT licensing is confirmed; App Config is MIT. The report
player is traced to Akatz-authored source. The bundle retains dependency notices,
model license sources and the corresponding source/build inputs for FFmpeg 7.1.5
and other collected native components. Model weights and user footage are excluded.
The publisher and friend confirmed their model authorization.

The context builder copies an exact verified payload allowlist and rejects
unlisted files, links and changed bytes. Build inventories and verification
receipts identify the packaged inputs. This is not a comprehensive vulnerability
audit or a promise about all transitive dependency rights.

## Source publication and CI later

Commit this release in the existing integration checkout and preserve its history.
A public export should use the portable installation guide as its landing page,
review lab-specific examples, and include accessible reviewed SDK/App Kit packages
or source. The source release now includes the pinned `vendor/*.tgz` artifacts and their
corresponding source. Follow `SOURCE_INSTALL.md` and the reviewed source exporter
for the reproducible source installation path.
Do not merge the separate Cloud feature as part of that export.

CI can later build and test the matched images from reviewed source revisions,
publish explicit versions and produce a digest-pinned installation bundle.
No CI or public repository is claimed by this local release checkpoint.
