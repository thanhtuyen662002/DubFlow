# Dog bark sound ("Tiếng chó sủa")

The sound laid over a flagged word when Cài đặt › Dịch phụ đề › "Âm thay cho từ cấm"
is "Tiếng chó sủa" is a real recording, embedded in the app.

| | |
|---|---|
| Recording | "Barking Dog" by Suprasummun |
| Source | https://freesound.org/people/Suprasummun/sounds/180977/ |
| Licence | CC0 1.0 (public domain dedication): no attribution required, given anyway |
| Taken from | ESC-50 clip `3-180977-A-0.wav` (https://github.com/karolpiczak/ESC-50), whose LICENSE lists this clip's original as CC0 |

Processing: cut from 1.50 s to 2.24 s of the ESC-50 clip (two barks), room noise
between the barks softened by a gentle expander, 5 ms fade-in and 40 ms fade-out,
resampled to 22.05 kHz mono 16-bit, peak at 0.55, then 0.12 s of silence.

The samples live as the same base64 string in
`engine/dubflow/worker/dog_bark_data.py` (video export) and
`apps/desktop/src/features/ai/dog_bark_data.ts` (Nghe thử).
An offline audio classifier (BirdNET v2.4) labels the clip "Dog" at 0.93.
