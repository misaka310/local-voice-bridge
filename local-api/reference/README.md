# reference

Local reference data belongs here, but the actual audio/text files are not included in the public repository.

## Reference voice layout

`/v1/reference-voices` scans this structure:

```text
local-api/reference/voices/
  <voiceId>/
    voice.wav
    voice.txt
```

- `voice.wav`: reference audio file
- `voice.txt`: transcript of the reference audio

`voice.txt` should contain what is actually spoken in `voice.wav`.

## Public repository rule

Only documentation and placeholder files are tracked here. Real reference audio, transcripts, rejected cuts, and temporary workflow files are ignored by Git.
