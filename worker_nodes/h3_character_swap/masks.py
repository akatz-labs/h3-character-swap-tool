"""Import immutable person masks with explicit, adjustable pixel dilation."""
from pathlib import Path
import hashlib,json
import numpy as np
import torch
from PIL import Image
from scipy.ndimage import distance_transform_edt
import folder_paths

def expand_masks(raw, radius):
    if not 0 <= radius <= 256:raise ValueError('Expansion must be between 0 and 256 pixels')
    result=[]
    for mask in raw:
        binary=mask>0.5
        result.append((distance_transform_edt(~binary)<=radius) if radius and binary.any() else binary)
    return np.stack(result).astype(np.float32)

class SavedPersonMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'mask_file':('STRING',{'default':'person-mask.npz'}),'images':('IMAGE',),
            'source_frames':('INT',{'default':124,'min':1}),
            'expansion_px':('INT',{'default':64,'min':0,'max':256,'tooltip':'Outward Euclidean radius at the prepared video resolution. No automatic anchor-based expansion.'})}}
    RETURN_TYPES=('MASK','MASK','STRING')
    RETURN_NAMES=('expanded_person_mask','raw_person_mask','provenance')
    FUNCTION='load'
    CATEGORY='H3 Relay/saved masks'
    @classmethod
    def IS_CHANGED(cls,mask_file,**kwargs):
        return hashlib.sha256((Path(folder_paths.get_input_directory())/mask_file).read_bytes()).hexdigest()
    def load(self,mask_file,images,source_frames,expansion_px):
        root=Path(folder_paths.get_input_directory()).resolve();p=(root/mask_file).resolve()
        if not p.is_relative_to(root):raise ValueError('Mask must be inside ComfyUI input')
        with np.load(p,allow_pickle=False) as z:
            raw=z['mask']
            if 'present' in z and not z['present'].all():raise ValueError('Saved mask has unreviewed missing frames')
        if raw.ndim!=3 or len(raw)!=source_frames or len(images)<source_frames:raise ValueError('Mask and real source timeline must match exactly')
        if not np.isfinite(raw).all():raise ValueError('Mask contains non-finite values')
        h,w=images.shape[1:3]
        if raw.shape[1:]!=(h,w):
            raw=np.stack([np.asarray(Image.fromarray((x>.5).astype(np.uint8)*255).resize((w,h),Image.Resampling.NEAREST))>127 for x in raw])
        else:raw=raw>.5
        expanded=expand_masks(raw,expansion_px)
        raw=raw.astype(np.float32)
        if len(images)>source_frames:
            n=len(images)-source_frames
            expanded=np.concatenate([expanded,np.repeat(expanded[-1:],n,0)])
            raw=np.concatenate([raw,np.repeat(raw[-1:],n,0)])
        provenance=json.dumps({'file':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_frames':source_frames,'prepared_frames':len(images),'dimensions':[w,h],'expansion_px':expansion_px,'metric':'euclidean_outward_radius','anchor_auto_expansion':False})
        return(torch.from_numpy(expanded),torch.from_numpy(raw),provenance)

class SaveMaskNPZ:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'masks':('MASK',),'filename_prefix':('STRING',{'default':'person-tail'}),'frames':('INT',{'default':5,'min':1})}}
    RETURN_TYPES=()
    FUNCTION='save'
    OUTPUT_NODE=True
    CATEGORY='H3 Relay/saved masks'
    def save(self,masks,filename_prefix,frames):
        root=Path(folder_paths.get_output_directory()).resolve();p=(root/(filename_prefix+'.npz')).resolve()
        if not p.is_relative_to(root):raise ValueError('Output outside output directory')
        if len(masks)<frames:raise ValueError('Insufficient mask frames')
        p.parent.mkdir(parents=True,exist_ok=True);np.savez_compressed(p,mask=(masks[:frames].cpu().numpy()>.5))
        return {'ui':{'mask_files':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'frames':frames}]}}

NODE_CLASS_MAPPINGS={'H3CharacterSwapLoadMask':SavedPersonMask,'H3CharacterSwapSaveMask':SaveMaskNPZ}
NODE_DISPLAY_NAME_MAPPINGS={'H3CharacterSwapLoadMask':'H3 Import Saved Person Mask + Expansion','H3CharacterSwapSaveMask':'H3 Save Mask NPZ'}
