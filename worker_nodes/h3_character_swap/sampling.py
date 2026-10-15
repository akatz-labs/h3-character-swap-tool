"""Qualified native H3 reference encoding and sampling memory estimate."""
import math, logging, torch
import comfy.model_management as mm
import node_helpers
from comfy_api.latest import io
from comfy_extras.nodes_minimax_h3 import (MiniMaxH3ReferenceToVideo, _empty_av_latent,
    _resize, _encode_ref_audio, adapt_canvas, CANVAS_MULTIPLE, REF_IMAGE_SHORT_EDGE, FPS)
def stage_handoff(label):
    mm.unload_all_models()
    mm.soft_empty_cache()
    logging.info("H3_STAGE_HANDOFF %s: inactive models moved to RAM; CUDA allocated %.1f MiB", label, torch.cuda.memory_allocated()/2**20)


class StagedReference(MiniMaxH3ReferenceToVideo):
    @classmethod
    def define_schema(cls):
        schema=super().define_schema()
        schema.node_id="H3CharacterSwapReference"
        schema.display_name="MiniMax H3 Reference to Video (GPU Stage Handoffs)"
        return schema
    @classmethod
    def execute(cls, clip, prompt, width, height, length, ref_image_size="match", vae=None, audio_vae=None,
                ref_images=None, ref_videos=None, ref_video_audios=None, ref_audios=None) -> io.NodeOutput:
        stage_handoff("Start reference VAE encoding; release prior-job models")
        latent, frame_count = _empty_av_latent(width, height, length)

        ref_items = []   # for the tokenizer presentation, in request order
        ref_blocks = []  # for the DiT payload, same order

        for img in (ref_images or {}).values():
            if img is None:
                continue
            h, w = img.shape[1], img.shape[2]
            if ref_image_size == "match":
                # aspect-preserving scale (down only) to the generation's pixel area
                scale = min(1.0, math.sqrt((width * height) / (w * h)))
            else:
                scale = min(1.0, REF_IMAGE_SHORT_EDGE / min(w, h))
            tw = max(CANVAS_MULTIPLE, round(w * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
            th = max(CANVAS_MULTIPLE, round(h * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
            resized = _resize(img[:1], tw, th, "disabled")
            ref_items.append({"type": "image", "data": resized})
            if vae is not None:
                z = vae.encode(resized)
                ref_blocks.append({"kind": "image", "latent_h": th // 16, "latent_w": tw // 16, "latent": z})

        ref_video_audios = ref_video_audios or {}
        for name, video_frames in (ref_videos or {}).items():
            if video_frames is None:
                continue
            # index-paired soundtrack: ref_video_audio_N belongs to ref_video_N
            soundtrack = ref_video_audios.get("ref_video_audio_" + name.rsplit("_", 1)[-1])
            vh, vw = video_frames.shape[1], video_frames.shape[2]
            cw, ch = adapt_canvas(vw, vh)
            if vw * vh < cw * ch:
                cw = max(CANVAS_MULTIPLE, round(vw / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
                ch = max(CANVAS_MULTIPLE, round(vh / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
            frames = _resize(video_frames, cw, ch, "disabled")
            if frames.shape[0] > frame_count:
                frames = frames[:frame_count]
            n = frames.shape[0]
            if n < 5:
                raise ValueError("MiniMax H3 reference videos need at least 5 frames (~0.2s at 24 fps)")
            while n % 17 != 5:
                n -= 1
            frames = frames[:n]
            if soundtrack is not None:
                # the soundtrack gets its own <Audio j> label, emitted before <Video k>
                ref_items.append({"type": "audio"})
            # Qwen sees the video at 2 fps with timestamps
            sample_idx = list(range(0, frames.shape[0], FPS // 2))
            qwen_frames = frames[sample_idx]
            ref_items.append({"type": "video", "data": qwen_frames,
                              "timestamps": [i / 2.0 for i in range(len(sample_idx))]})
            if vae is None:
                continue
            z = vae.encode(frames)
            audio_latent, ref_audio_t = (None, 0)
            if soundtrack is not None and audio_vae is not None:
                audio_latent, ref_audio_t = _encode_ref_audio(audio_vae, soundtrack)
            ref_blocks.append({"kind": "video_audio" if ref_audio_t else "video",
                               "latent_t": z.shape[2], "latent_h": ch // 16, "latent_w": cw // 16,
                               "ref_audio_t": ref_audio_t, "latent": z, "audio_latent": audio_latent})

        for audio in (ref_audios or {}).values():
            if audio is None:
                continue
            ref_items.append({"type": "audio"})
            if audio_vae is not None:
                audio_latent, ref_audio_t = _encode_ref_audio(audio_vae, audio)
                ref_blocks.append({"kind": "audio", "ref_audio_t": ref_audio_t, "audio_latent": audio_latent})

        tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)
        stage_handoff("VAE to Qwen GPU encoding")
        cond = clip.encode_from_tokens_scheduled(tokens)
        stage_handoff("Qwen encoding complete")
        if ref_blocks:
            cond = node_helpers.conditioning_set_values(cond, {"minimax_refs": ref_blocks})
        return io.NodeOutput(cond, latent)


class SamplingWorkspace(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="H3CharacterSwapWorkspace", display_name="MiniMax H3 Sampling Workspace (No ControlNet)", category="MiniMax H3", inputs=[io.Model.Input("model"),io.Latent.Input("latent"),io.Float.Input("activation_memory_gib",default=14.0,min=0.0,max=24.0,step=0.5)],outputs=[io.Model.Output()])
    @classmethod
    def execute(cls,model,latent,activation_memory_gib=14.0):
        stage_handoff("Masked VAE complete, before diffusion weights; no ControlNet")
        patched=model.clone()
        native=model.get_model_object("memory_required")
        def memory_required(input_shape,cond_shapes={}):
            return max(native(input_shape,cond_shapes),activation_memory_gib*2**30)
        def scoped(executor,*args,**kwargs):
            base=patched.model; previous=base.memory_required
            base.memory_required=memory_required
            try:return executor(*args,**kwargs)
            finally:base.memory_required=previous
        import comfy.patcher_extension
        patched.add_wrapper(comfy.patcher_extension.WrappersMP.OUTER_SAMPLE,scoped)
        return io.NodeOutput(patched)

