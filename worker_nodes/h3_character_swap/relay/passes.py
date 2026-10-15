"""Expand body generation into native ComfyUI nodes."""
from comfy_execution.graph_utils import GraphBuilder
from ..conditioning import conditioning_graph
class History:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'positive':('CONDITIONING',),'latent':('LATENT',),'vae':('VAE',),'audio_vae':('VAE',),
            'frames':('IMAGE',),'history_frames':('INT',),'use_audio':('BOOLEAN',)},'optional':{'audio':('AUDIO',)}}
    RETURN_TYPES=('CONDITIONING','LATENT')
    FUNCTION='apply'
    CATEGORY='H3 Relay/performance'
    def apply(self,positive,latent,vae,audio_vae,frames,history_frames,use_audio,audio=None):
        if history_frames>=18:
            from ..vendor.context_loop.sliding_context import apply_sliding_history
            if use_audio and audio is not None:
                # Match native H3 reference/guide preprocessing. The vendored
                # history helper predates comfy.audio and assumes torchaudio.
                import comfy.audio
                rate=int(getattr(audio_vae,'audio_sample_rate',32000))
                audio={'waveform':comfy.audio.resample(audio['waveform'],audio['sample_rate'],rate),
                       'sample_rate':rate}
            positive,latent,_=apply_sliding_history(positive,vae,latent,frames,history_frames,'disabled',
                audio_vae=audio_vae,previous_audio=audio if use_audio else None)
            # The native source-audio guide added after this node covers target
            # frame zero too. Keep past audio, remove the duplicate boundary audio.
            if use_audio:
                clean=[]
                for embedding,metadata in positive:
                    m=dict(metadata);m['minimax_keyframes']=[k for k in m.get('minimax_keyframes',[])
                        if not (k.get('anchor')=='first' and 'audio_latent' in k)]
                    clean.append([embedding,m])
                positive=clean
        else:
            from comfy_extras.nodes_minimax_h3 import MiniMaxH3AddGuide
            # For first window, frames is the prepared scene anchor. For a
            # boundary-only continuation, it is the previous generated frame.
            positive=MiniMaxH3AddGuide.execute(positive=positive,latent=latent,vae=vae,image=frames[-1:],frame_idx=0)[0]
        return positive,latent


def reference(graph,models,prompt,width,height,length,identity,character,source,audio,use_audio):
    kwargs={'ref_images.ref_image_0':identity,'ref_images.ref_image_1':character,'ref_videos.ref_video_0':source}
    if use_audio: kwargs['ref_audios.ref_audio_0']=audio
    return graph.node('H3CharacterSwapReference',clip=models['clip'],vae=models['video_vae'],audio_vae=models['audio_vae'],
        prompt=prompt,width=width,height=height,length=length,ref_image_size='match',**kwargs)


def sample(graph,models,settings,ref,frames,mask,history,audio,history_audio,history_frames):
    carry=graph.node('H3CharacterSwapHistory',positive=ref.out(0),latent=ref.out(1),
        vae=models['video_vae'],audio_vae=models['audio_vae'],frames=history,history_frames=history_frames,
        use_audio=settings['audio'],audio=history_audio)
    # History changes target dimensions and drops masks: always mask afterwards.
    latent=graph.node('H3CharacterSwapMaskedRegeneration',latent=carry.out(1),vae=models['video_vae'],image=frames,mask=mask)
    positive=carry.out(0)
    if settings['audio']:
        positive=graph.node('MiniMaxH3AddGuide',positive=positive,latent=latent.out(0),
            audio_vae=models['audio_vae'],audio=audio,frame_idx=0).out(0)
    model=graph.node('H3CharacterSwapWorkspace',model=models['model'],latent=latent.out(0),
        activation_memory_gib=settings['activation_memory_gib']).out(0)
    noise=graph.node('RandomNoise',noise_seed=settings['seed'])
    guider=graph.node('CFGGuider',model=model,positive=positive,negative=positive,cfg=settings['cfg'])
    sampler=graph.node('KSamplerSelect',sampler_name=settings['sampler_name'])
    sigmas=graph.node('BasicScheduler',model=model,scheduler=settings['scheduler'],steps=settings['steps'],denoise=1.)
    output=graph.node('SamplerCustomAdvanced',noise=noise.out(0),guider=guider.out(0),sampler=sampler.out(0),
        sigmas=sigmas.out(0),latent_image=latent.out(0))
    split=graph.node('LTXVSeparateAVLatent',av_latent=output.out(0))
    return graph.node('VAEDecode',samples=split.out(0),vae=models['video_vae']).out(0)


class BodyWindow:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'models':('H3_RELAY_MODELS',),'shot':('H3_RELAY_SHOT',),
            'entry':('H3_RELAY_ENTRY',),'state':('H3_RELAY_STATE',)}}
    RETURN_TYPES=('IMAGE',)
    FUNCTION='expand'
    CATEGORY='H3 Relay/performance'
    def expand(self,models,shot,entry,state):
        g=GraphBuilder();s=entry['body_settings'];hist=entry['history_frames'];size=entry['window_frames']
        part=g.node('H3CharacterSwapWindowInputs',shot=shot,entry=entry,state=state)
        # Keep the original body prompt's Picture 1 character / Picture 2 scene ordering.
        scene=shot['anchor'] if not hist else state['body'][entry['source_start']:entry['source_start']+1]
        ref=reference(g,models,s['prompt'],shot['source'].shape[2],shot['source'].shape[1],size+max(0,hist-1),
            shot['reference'],scene,part.out(5),part.out(6),s['audio'])
        frames=sample(g,models,s,ref,part.out(0),part.out(1),part.out(3),part.out(2),part.out(4),hist)
        trimmed=g.node('H3CharacterSwapTrim',images=frames,source_frames=size)
        # Dataset beauty output: decoded generation, never pasted onto source RGB.
        # Subsequent history and session cache receive these same raw frames.
        return conditioning_graph({'result':(trimmed.out(0),),'expand':g.finalize()},
            shot['generation']['use_anchor'], shot['generation']['use_mask'], hist)

