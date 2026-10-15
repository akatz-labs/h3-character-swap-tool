"""Equivalence and cache regressions for native-matte playback optimizations."""
import copy
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt, label, binary_fill_holes
from h3workbench import refinement as r, refine_preview as p
from h3workbench.refinement import polygon_at, sam_support
from test_refinement import scene

# Reference is the pre-optimization full-frame algorithm. Cropping must preserve
# its global silhouette/hole rules even when the subject touches a canvas edge.
def _full_frame_face_reference(alpha, face, frame):
    if not face["enabled"]:
        return alpha
    h, w = alpha.shape
    roi = Image.new("L", (w, h))
    ImageDraw.Draw(roi).polygon(
        [(round(x * (w - 1)), round(y * (h - 1))) for x, y in polygon_at(face, frame)],
        fill=255,
    )
    inside = np.asarray(roi) > 0
    feather, guard_width = face["feather_px"], face["guard_px"]
    region = np.where(
        inside,
        1.0,
        np.clip(1 - distance_transform_edt(~inside) / max(feather, 0.001), 0, 1),
    )
    components, count = label(alpha >= 16 / 255)
    if not count:
        return alpha
    sizes = np.bincount(components.ravel())
    sizes[0] = 0
    foreground = components == sizes.argmax()
    holes, count = label(binary_fill_holes(foreground) & ~foreground)
    safe = distance_transform_edt(inside) >= max(1, guard_width * 2)
    # Reject a whole hole if any of its pixels fall outside the safe face core.
    # One label lookup avoids scanning a full frame once per tiny alpha hole.
    allowed = np.ones(count + 1, dtype=bool)
    allowed[0] = False
    allowed[np.unique(holes[~safe])] = False
    accepted = allowed[holes]
    silhouette = foreground | accepted
    depth = distance_transform_edt(silhouette)
    guard = np.clip((depth - guard_width) / max(guard_width, 1), 0, 1) * silhouette
    return np.maximum(alpha, np.minimum(region, guard)).astype(np.float32)


def _full_frame_matte_reference(raw, sam, f, frame):
    alpha = np.clip(raw.astype(np.float32), 0, 1)
    sam = sam.astype(bool)
    if not sam.any():
        return np.zeros_like(alpha)
    distance = distance_transform_edt(sam)
    core = np.clip((distance - 0.5) / max(f["core_px"] - 0.5, 0.5), 0, 1)
    support = sam_support(sam, f["gate_px"])
    alpha = np.maximum(alpha, core) * support
    alpha = _full_frame_face_reference(alpha, f["face"], frame)
    alpha[alpha < 1 / 255] = 0
    alpha[alpha > 254 / 255] = 1
    return alpha


def test_cropped_matte_matches_full_frame_at_edges_and_holes():
    rng = np.random.default_rng(73)
    polygons = [[[.2,.2],[.8,.2],[.8,.8],[.2,.8]],
                [[0,0],[1,0],[1,1],[0,1]],
                [[0,.1],[.5,.2],[.3,1],[0,1]],
                [[.9,.9],[1,.9],[1,1],[.9,1]]]
    for i in range(80):
        raw = rng.random((80,96), dtype=np.float32)
        sam = np.zeros(raw.shape, bool)
        y0,x0 = rng.integers(0,30,2); y1,x1 = rng.integers(50,80,2)
        if i%3 == 0: y0=x0=0
        if i%5 == 0: y1,x1=sam.shape
        sam[y0:y1,x0:x1]=True
        sam[30:36,30:36]=False
        if i%4 == 0: sam[:35,30:34]=False
        if i%11 == 0: sam[:]=True
        if i%13 == 0: sam[:]=False
        f={'gate_px': [0,3,8,31][i%4], 'core_px': [0,2,9,64][i%4],
           'face': {'enabled':i%7!=0,'feather_px':[0,4,20][i%3],
                    'guard_px':[0,3,15][i%3],
                    'keyframes':[{'frame':0,'polygon':polygons[i%4]}]}}
        np.testing.assert_array_equal(r.matte_frame(raw,sam,f,0),
                                      _full_frame_matte_reference(raw,sam,f,0))


def test_native_matte_cache_reuses_unchanged_keyframes_and_is_lossless(scene,monkeypatch):
    c,s,d=scene;monkeypatch.setattr(p,'MEDIA',d)
    f=copy.deepcopy(s['refine']['source']);f['face'].update(enabled=True,keyframes=[
        {'frame':i,'polygon':[[.2,.2],[.8,.2],[.8,.8],[.2,.8]]} for i in range(3)])
    raw=r.raw_alpha(f['alpha'],3,c['dimensions']);sam=np.ones(raw.shape,bool)
    calls=[]
    def load(frame): calls.append(frame);return raw[frame],sam[frame]
    first,hit=p.cached_matte(f,0,3,c['dimensions'],load)
    assert not hit
    np.testing.assert_array_equal(first,r.matte_frame(raw[0],sam[0],f,0))
    second,hit=p.cached_matte(f,0,3,c['dimensions'],load)
    assert hit and calls==[0]
    np.testing.assert_array_equal(first,second)
    key=p.matte_key(f,0,3,c['dimensions'])
    f['face']['keyframes'][1]['polygon'][0][0]+=.01
    assert p.matte_key(f,0,3,c['dimensions'])==key
    f['face']['keyframes'][0]['polygon'][0][0]+=.01
    assert p.matte_key(f,0,3,c['dimensions'])!=key
    p.cached_matte(f,0,3,c['dimensions'],load)
    assert calls==[0,0]


def test_warm_alpha_view_needs_no_rgb_or_raw_decode(scene,monkeypatch):
    from h3workbench import media
    c,s,d=scene;monkeypatch.setattr(p,'MEDIA',d)
    original=media.run
    monkeypatch.setattr(media,'run',lambda args,**kw: '' if args[0]=='hub-storage' else original(args,**kw))
    recipe=r.draft(c,s)
    cold=p.render(c,s,recipe,'character','checker',d/'checker')
    assert cold['matte_cache_hits']==0
    def unexpected(*args,**kwargs): raise AssertionError('Warm matte must not decode source inputs')
    monkeypatch.setattr(r,'decode',unexpected);monkeypatch.setattr(r,'raw_alpha',unexpected)
    monkeypatch.setattr(p.masks,'read',unexpected)
    warm=p.render(c,s,recipe,'character','alpha',d/'alpha')
    assert warm['matte_cache_hits']==3 and warm['frames']==3


def test_single_frame_sam_correction_reuses_other_frames(scene,monkeypatch):
    from h3workbench import media,masks,store
    c,s,d=scene;monkeypatch.setattr(p,'MEDIA',d)
    original=media.run
    monkeypatch.setattr(media,'run',lambda args,**kw: '' if args[0]=='hub-storage' else original(args,**kw))
    recipe=r.draft(c,s)
    p.render(c,s,recipe,'source','checker',d/'before-correction')
    mask=masks.read(recipe['source']['mask'],3,c['dimensions'])
    mask[1,8:12,10:14]=False
    path=d/'corrected.npz';np.savez_compressed(path,mask=mask)
    recipe['source']['mask']=store.asset_add(path)
    result=p.render(c,s,recipe,'source','checker',d/'after-correction')
    assert result['matte_cache_hits']==2
