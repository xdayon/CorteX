import type {CSSProperties} from 'react';
import type {OverlayPayload} from './types';
const cueAt = (cues: OverlayPayload['cues'], time: number) =>
  cues.find(c => time >= c.start && time < c.end) ?? {words: [], start: 0, end: 0};
const fadeInterpolate = (time: number, range: number[], output: number[], _options: unknown) =>
  range[1] <= range[0] ? (time < range[1] ? output[0] : output[1]) :
  output[0] + (output[1] - output[0]) * Math.max(0, Math.min(1, (time-range[0])/(range[1]-range[0])));
export function CaptionLayer({caption, cues, time, width, height}: {
  caption: OverlayPayload['caption']; cues: OverlayPayload['cues']; time: number; width: number; height: number;
}) {
  const scale = Math.min(width / 1080, height / 1920);
  const cue = cueAt(cues, time);
  const captionFade = caption.animation.style === 'fade'
    ? fadeInterpolate(
      time,
      [
        Math.max(0, cue.words[0]?.start ?? 0),
        Math.min(
          cue.words[cue.words.length - 1]?.end ?? 0,
          (cue.words[0]?.start ?? 0) + caption.animation.durationSeconds,
        ),
      ],
      [0.4, 1],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    )
    : 1;

  const captionStyle: CSSProperties = {
    fontFamily: caption.fontFamily,
    fontSize: caption.fontSize * scale,
    textShadow: caption.shadow
      ? `0 ${4 * scale}px ${14 * scale}px ${caption.shadowColor}`
      : undefined,
    WebkitTextStroke: caption.outline ? `${3 * scale}px ${caption.outlineColor}` : undefined,
    paintOrder: 'stroke fill',
  };

  return <>
      {caption.enabled && cue.words.length > 0 ? (
        <div
          style={{
            ...captionStyle,
            position: 'absolute',
            left: width * 0.075,
            right: width * 0.075,
            // Reserve space for the visible stroke and shadow inside the safe zone.
            top: height * caption.positionY,
            transform: 'translateY(-50%)',
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            gap: `${8 * scale}px ${14 * scale}px`,
            fontWeight: caption.fontWeight ?? 900,
            lineHeight: 1.08,
            textAlign: 'center',
            textTransform: caption.uppercase === false ? 'none' : 'uppercase',
          }}
        >
          {cue.words.map((word, index) => {
            const active = time >= word.start && time < word.end;
            return (
              <span
                key={`${index}-${word.start}-${word.end}`}
                style={{
              color: caption.karaoke && active ? caption.karaokeColor : caption.textColor,
              transform: caption.karaoke && active && caption.animation.style === 'pop' ? 'scale(1.08)' : 'scale(1)',
              opacity: caption.animation.style === 'fade' ? captionFade : 1,
            }}
          >
                {word.text}
              </span>
            );
          })}
        </div>
      ) : null}
  </>;
}
