"""Interactive SAM corrections conditioned on the existing unpadded person mask."""

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
import comfy.model_management
import comfy.utils
import folder_paths


class RefinePersonMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "image": ("IMAGE",),
                "positive_coords": ("STRING", {"default": "[]"}),
                "negative_coords": ("STRING", {"default": "[]"}),
                "prior_file": ("STRING", {"default": ""}),
                "frame": ("INT", {"default": 0, "min": 0}),
            }
        }

    RETURN_TYPES = ("MASK",)
    FUNCTION = "refine"
    CATEGORY = "H3 Workbench/masks"

    @classmethod
    def IS_CHANGED(cls, prior_file, **kwargs):
        if not prior_file:
            return "no-prior"
        return hashlib.sha256(cls.prior_path(prior_file).read_bytes()).hexdigest()

    @staticmethod
    def prior_path(name):
        root = Path(folder_paths.get_input_directory()).resolve()
        p = (root / name).resolve()
        if not p.is_relative_to(root):
            raise ValueError("Prior mask must be a staged input")
        return p

    def refine(self, model, image, positive_coords, negative_coords, prior_file, frame):
        if len(image) != 1:
            raise ValueError("Refine exactly one correction frame")
        _, h, w, _ = image.shape
        pos, neg = json.loads(positive_coords), json.loads(negative_coords)
        if not pos:
            raise ValueError(
                "An include point or derived prior-mask anchor is required"
            )
        comfy.model_management.load_model_gpu(model)
        device = comfy.model_management.get_torch_device()
        dtype = model.model.get_dtype()
        sam = model.model.diffusion_model
        pixels = comfy.utils.common_upscale(
            image[..., :3].movedim(-1, 1), 1008, 1008, "bilinear", crop="disabled"
        ).to(device=device, dtype=dtype)
        points = {
            "point_coords": torch.tensor(
                [[[p["x"] / w * 1008, p["y"] / h * 1008] for p in pos + neg]],
                dtype=dtype,
                device=device,
            ),
            "point_labels": torch.tensor(
                [[1] * len(pos) + [0] * len(neg)], dtype=torch.int32, device=device
            ),
        }
        logits = None
        if prior_file:
            with np.load(self.prior_path(prior_file), allow_pickle=False) as z:
                raw = z["mask"]
                if raw.ndim != 3 or frame >= len(raw):
                    raise ValueError("Prior mask frame is outside the saved timeline")
                prior = raw[frame] > 0.5
            if prior.any():
                prior = np.asarray(
                    Image.fromarray(prior).resize((w, h), Image.Resampling.NEAREST)
                ).copy()
                logits = (
                    torch.from_numpy(prior).to(device=device, dtype=dtype)[None, None]
                    * 12
                    - 6
                )
                logits = F.interpolate(
                    logits, size=(1008, 1008), mode="bilinear", align_corners=False
                )
        # Keep the user's positive/negative constraints in both refinement passes.
        for _ in range(2):
            logits = sam.forward_segment(
                pixels, point_inputs=points, mask_inputs=logits
            )
        mask = (
            F.interpolate(logits, size=(h, w), mode="bilinear", align_corners=False)[0]
            > 0
        )
        return (mask.float().cpu(),)


NODE_CLASS_MAPPINGS = {"H3WorkbenchRefineMask": RefinePersonMask}
NODE_DISPLAY_NAME_MAPPINGS = {"H3WorkbenchRefineMask": "H3 Refine Existing Person Mask"}


class TrackFace:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'images': ('IMAGE',), 'mask_file': ('STRING',),
                             'region': ('STRING', {'default': '{}'})}}
    RETURN_TYPES = ('STRING',)
    FUNCTION = 'track'
    CATEGORY = 'H3 Workbench/masks'
    OUTPUT_NODE = True

    def track(self, images, mask_file, region):
        import cv2
        rgb = images.cpu().numpy()[..., :3]
        n,h,w = rgb.shape[:3]
        with np.load(RefinePersonMask.prior_path(mask_file), allow_pickle=False) as z:
            masks = np.stack([np.asarray(Image.fromarray(a > .5).resize((w,h),Image.Resampling.NEAREST)) for a in z['mask']])
        if len(masks) != n: raise ValueError('Face tracking mask timeline differs')
        seed = json.loads(region)
        uncertain=[]
        if seed:
            start=int(seed['frame']); poly=np.asarray(seed['polygon'],np.float32)*[w,h]
            if not 0<=start<n or poly.shape!=(4,2): raise ValueError('Draw a four-corner face region')
            gray=[cv2.cvtColor(np.rint(im*255).astype(np.uint8),cv2.COLOR_RGB2GRAY) for im in rgb]
            polygons=[None]*n;polygons[start]=poly
            for direction in [-1,1]:
                previous=start;current_poly=poly.copy()
                for i in range(start+direction,n if direction==1 else -1,direction):
                    comfy.model_management.throw_exception_if_processing_interrupted()
                    roi=np.zeros((h,w),np.uint8);cv2.fillPoly(roi,[np.rint(current_poly).astype(np.int32)],255)
                    points=cv2.goodFeaturesToTrack(gray[previous],maxCorners=100,qualityLevel=.01,minDistance=3,mask=roi)
                    matrix=None
                    if points is not None and len(points)>=6:
                        moved,ok,_=cv2.calcOpticalFlowPyrLK(gray[previous],gray[i],points,None,winSize=(31,31),maxLevel=3)
                        back,okback,_=cv2.calcOpticalFlowPyrLK(gray[i],gray[previous],moved,None,winSize=(31,31),maxLevel=3)
                        good=(ok.ravel()>0)&(okback.ravel()>0)&(np.linalg.norm(back-points,axis=2).ravel()<1.5)
                        if good.sum()>=6:
                            matrix,inliers=cv2.estimateAffinePartial2D(points[good],moved[good],method=cv2.RANSAC,ransacReprojThreshold=2)
                            if matrix is not None and (inliers.sum()<6 or not .8<np.linalg.norm(matrix[0,:2])<1.25):matrix=None
                    if matrix is None: uncertain.append(i)
                    else:current_poly=cv2.transform(current_poly[None].astype(np.float32),matrix)[0]
                    current_poly=np.clip(current_poly,[0,0],[w-1,h-1]);polygons[i]=current_poly.copy();previous=i
            proof={'method':'bidirectional-region-optical-flow','seed_frame':start,'frames':n,'uncertain_frames':sorted(set(uncertain))}
        else:
            from nodes import NODE_CLASS_MAPPINGS as registered_nodes
            from .tracking_provider import load_tracking_provider
            tracking=load_tracking_provider(registered_nodes)
            try:
                track=tracking.infer_track(rgb,masks=masks,hands='off',provider='CUDA',crop_to_actor=True)
                for i,points in enumerate(track['points']):
                    box=tracking.face_box(points,.3)
                    if box is None or max((box[2:]-box[:2])/[w,h])>.8: uncertain.append(i)
                polygons=[]
                for x0,y0,x1,y1 in track['face_boxes']:
                    # Cover the full feature box with margin; feather stays outside.
                    dx=max(2,(x1-x0)*.10);dy=max(2,(y1-y0)*.20);bottom=max(2,(y1-y0)*.07)
                    polygons.append(np.clip([[x0-dx,y0-dy],[x1+dx,y0-dy],[x1+dx,y1+bottom],[x0-dx,y1+bottom]],[0,0],[w-1,h-1]))
                proof={**track['proof'],'method':'DWPose-face-landmarks','uncertain_frames':uncertain,'onnxruntime_version':tracking.ort.__version__,'model_sha256':hashlib.sha256(tracking.pose_model_path(track['proof']['model_name']).read_bytes()).hexdigest()}
            except ValueError as error:
                if 'No reliable face landmarks' not in str(error):raise
                polygons=[];proof={'method':'DWPose-face-landmarks','frames':n,'uncertain_frames':list(range(n)),'warning':str(error)}
        face={'enabled':bool(polygons),'keyframes':[{'frame':i,'polygon':(np.asarray(p)/[w-1,h-1]).tolist()} for i,p in enumerate(polygons)],'feather_px':4,'guard_px':3}
        result={'face':face,'face_track':proof}
        path=Path(folder_paths.get_output_directory())/'face-track.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result,indent=2))
        return {'ui':{'text':[json.dumps(proof)]},'result':(str(path),)}


NODE_CLASS_MAPPINGS['H3WorkbenchTrackFace'] = TrackFace
NODE_DISPLAY_NAME_MAPPINGS['H3WorkbenchTrackFace'] = 'H3 Track Face Protection'

from .generation import NODE_CLASS_MAPPINGS as GENERATION_NODES

NODE_CLASS_MAPPINGS.update(GENERATION_NODES)

# This module registers hooks only for an explicitly owned Workbench session.
from . import session as _session
