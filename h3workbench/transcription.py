"""CPU transcription with a pinned model in the shared model cache."""
import os
import wave


MODEL = "Systran/faster-whisper-medium.en"
REVISION = "a29b04bd15381511a9af671baec01072039215e3"
FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]


def transcribe(audio, *, progress, check_cancel):
    from .transcription_resources import model_use
    with model_use():
        return _transcribe(audio, progress=progress, check_cancel=check_cancel)


def _transcribe(audio, *, progress, check_cancel):
    from faster_whisper import WhisperModel
    from .transcription_resources import installed, snapshot_path
    check_cancel()
    if not installed():
        raise ValueError("Install Whisper Medium.en (about 1.5 GB) from Resources → Optional before transcribing.")
    model_path = str(snapshot_path())
    check_cancel()
    progress("Loading transcription model on CPU")
    model = WhisperModel(model_path, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 1, local_files_only=True)
    check_cancel()
    progress("Transcribing audio on CPU")
    # The pipeline already extracts 16 kHz mono PCM. Pass samples directly,
    # avoiding a second decoder and PyAV-version-dependent open() arguments.
    import numpy as np
    with wave.open(str(audio), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
            raise ValueError("Transcription requires 16 kHz mono PCM audio")
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
    segments, info = model.transcribe(samples, language="en", beam_size=5,
                                     word_timestamps=True, vad_filter=False,
                                     condition_on_previous_text=False)
    text, words = [], []
    for segment in segments:
        check_cancel()
        text.append(segment.text.strip())
        words.extend({"word": word.word, "start": word.start, "end": word.end,
                      "probability": word.probability} for word in segment.words or [])
        progress(f"Transcribing audio · {segment.end:.0f}s processed")
    return {"text": " ".join(text), "words": words, "language": info.language,
            "model": "medium.en CPU int8", "model_revision": REVISION}
