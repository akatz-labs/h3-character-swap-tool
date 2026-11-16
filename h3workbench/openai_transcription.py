"""Direct file transcription using the local user's write-only OpenAI credential.

Each bounded segment is transcribed separately because the current API returns
text, not word timestamps. Preserve real chunk ranges rather than inventing them.
"""
import json
import os
import wave

import httpx
from . import local_openai, media, segments, store

MODEL = 'gpt-transcribe'
MAX_SECONDS = 120


def transcribe(clip, audio, directory, *, progress, check_cancel):
    api_key = local_openai.secret()
    boundaries = {0, clip['frames']}
    for shot in clip['shots']:
        for segment in segments.current(shot):
            boundaries.update((shot['start'] + segment['start'], shot['start'] + segment['end']))
    ordered = sorted(boundaries)
    spans = [(a, min(a + MAX_SECONDS * 24, end)) for start, end in zip(ordered, ordered[1:])
             for a in range(start, end, MAX_SECONDS * 24)]
    rows = []
    with wave.open(str(audio), 'rb') as source, httpx.Client(timeout=httpx.Timeout(180, connect=20), follow_redirects=False) as client:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
            raise ValueError('Transcription requires 16 kHz mono PCM audio')
        for index, (start, end) in enumerate(spans):
            check_cancel()
            result_path = directory / f'transcript-{index}.json'
            receipt = directory / f'transcription-{index}-submitted.json'
            if result_path.exists():
                rows.append(json.loads(result_path.read_text()))
                continue
            if receipt.exists():
                raise ValueError('This transcription was already submitted but no result was saved. It will not be retried automatically; start a new transcription to try again.')
            chunk = directory / f'transcription-{index}.wav'
            source.setpos(min(round(start / 24 * 16000), source.getnframes()))
            samples = source.readframes(round((end - start) / 24 * 16000))
            with wave.open(str(chunk), 'wb') as output:
                output.setparams(source.getparams())
                output.writeframes(samples)
            progress(f'Transcribing audio · {index + 1}/{len(spans)}')
            with receipt.open('x') as file:
                json.dump({'model': MODEL, 'start': start, 'end': end, 'created': store.now()}, file)
                file.flush(); os.fsync(file.fileno())
            try:
                with chunk.open('rb') as file:
                    response = client.post('https://api.openai.com/v1/audio/transcriptions',
                        headers={'Authorization': 'Bearer ' + api_key}, data={'model': MODEL},
                        files={'file': ('audio.wav', file, 'audio/wav')})
                if response.status_code != 200:
                    hints = {401: 'Replace the key in Local settings → OpenAI.',
                             403: 'Check the key’s model permissions.',
                             404: 'This account does not have access to the transcription model.',
                             429: 'Check your OpenAI quota and rate limits.'}
                    raise ValueError(f'OpenAI transcription failed (HTTP {response.status_code}). ' + hints.get(response.status_code, 'Review the attempt before retrying.'))
                result = response.json()
                if not isinstance(result, dict) or not isinstance(result.get('text'), str):
                    raise ValueError('OpenAI returned an invalid transcript')
            except httpx.HTTPError:
                raise ValueError('OpenAI connection interrupted; this transcription may have been billed. It will not be retried automatically.') from None
            finally:
                chunk.unlink(missing_ok=True)
            row = {'start': start / 24, 'end': end / 24, 'text': result['text'].strip()}
            temporary = result_path.with_suffix('.partial')
            temporary.write_text(json.dumps(row)); temporary.replace(result_path)
            rows.append(row)
    return {'text': ' '.join(row['text'] for row in rows if row['text']), 'words': [],
            'segments': rows, 'model': MODEL, 'timing': 'source-segment-ranges'}
