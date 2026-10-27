"""A long-running speech-to-text worker for inphAI: one JSON request per line on stdin,
one JSON answer per line on stdout. The model loads once, at start.

    {"id": 1, "path": "/tmp/recording.webm", "language": null}
 -> {"id": 1, "text": "...", "language": "en"}   or   {"id": 1, "error": "..."}
"""
import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='small')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--compute-type', default='int8')
    args = parser.parse_args()
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    print(json.dumps({'ready': True, 'model': args.model}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        try:
            # vad_filter drops silence, which is what makes Whisper invent words.
            segments, info = model.transcribe(request['path'], language=request.get('language') or None,
                                              beam_size=5, vad_filter=True)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
            print(json.dumps({'id': request['id'], 'text': text, 'language': info.language}), flush=True)
        except Exception as error:
            print(json.dumps({'id': request['id'], 'error': str(error)}), flush=True)


if __name__ == '__main__':
    main()
