import type {CSSProperties} from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {OverlayPayload} from './types';

const safeInterpolate = (
  value: number,
  inputRange: [number, number],
  outputRange: [number, number],
  options?: Parameters<typeof interpolate>[3],
) => {
  if (inputRange[0] === inputRange[1]) {
    return outputRange[0];
  }
  return interpolate(value, inputRange, outputRange, options);
};

const cueAt = (words: OverlayPayload['words'], time: number, wordsPerCue: number) => {
  if (words.length === 0) return {words: [], startIndex: 0};
  if (time < words[0].start || time >= words[words.length - 1].end) {
    return {words: [], startIndex: 0};
  }
  const active = words.findIndex((word) => time >= word.start && time < word.end);
  let nearest = active;
  if (nearest < 0) {
    nearest = words.findIndex((word) => word.start > time) - 1;
  }
  const startIndex = Math.floor(nearest / wordsPerCue) * wordsPerCue;
  return {words: words.slice(startIndex, startIndex + wordsPerCue), startIndex};
};

export const Overlay = ({caption, headline, words}: OverlayPayload) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const time = frame / fps;
  const scale = Math.min(width / 1080, height / 1920);
  const headlineProgress = headline.animation.entrance === 'none'
    ? 1
    : headline.animation.entrance === 'slide'
      ? spring({frame, fps, config: {damping: 16, stiffness: 140}})
      : spring({frame, fps, config: {damping: 18, stiffness: 150}});
  const headlineExit = headline.animation.exit === 'none'
    ? 1
    : safeInterpolate(
      time,
      [Math.max(0, headline.durationSeconds - headline.animation.durationSeconds), headline.durationSeconds],
      [1, 0],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    );
  const cue = cueAt(words, time, caption.wordsPerCue);
  const captionFade = caption.animation.style === 'fade'
    ? safeInterpolate(
      time,
      [
        Math.max(0, cue.words[0]?.start ?? 0),
        Math.min(cue.words[cue.words.length - 1]?.end ?? 0, headline.durationSeconds),
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

  return (
    <AbsoluteFill style={{backgroundColor: 'transparent', color: 'white'}}>
      {headline.enabled && headline.text.trim() && time < headline.durationSeconds ? (
        <div
          style={{
            position: 'absolute',
            top: height * 0.105,
            left: width * 0.075,
            right: width * 0.075,
            fontFamily: headline.fontFamily,
            fontSize: headline.fontSize * scale,
            fontWeight: 800,
            lineHeight: 1.08,
            opacity: headlineProgress * headlineExit,
            transform: `translateY(${(1 - headlineProgress) * -28 * scale}px)`,
          }}
        >
          <div
            style={{
              width: 70 * scale,
              height: 50 * scale,
              marginBottom: 14 * scale,
              display: 'grid',
              placeItems: 'center',
              background: headline.burstColor,
              color: '#fff',
              fontFamily: 'Georgia, serif',
              fontSize: 52 * scale,
              lineHeight: 1,
            }}
          >
            “
          </div>
          <span
            style={{
              padding: `${7 * scale}px ${14 * scale}px`,
              background: headline.stripColor,
              color: headline.textColor,
              boxDecorationBreak: 'clone',
              WebkitBoxDecorationBreak: 'clone',
            }}
          >
            {headline.text}
          </span>
        </div>
      ) : null}

      {caption.enabled && cue.words.length > 0 ? (
        <div
          style={{
            ...captionStyle,
            position: 'absolute',
            left: width * 0.075,
            right: width * 0.075,
            bottom: height * 0.16,
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            gap: `${8 * scale}px ${14 * scale}px`,
            fontWeight: 900,
            lineHeight: 1.08,
            textAlign: 'center',
            textTransform: 'uppercase',
          }}
        >
          {cue.words.map((word, index) => {
            const active = time >= word.start && time < word.end;
            return (
              <span
                key={`${cue.startIndex + index}-${word.start}`}
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
    </AbsoluteFill>
  );
};
