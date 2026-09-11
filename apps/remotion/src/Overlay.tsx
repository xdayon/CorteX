import {CaptionLayer} from './CaptionLayer';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {OverlayPayload} from './types';

const safeInterpolate = (
  value: number,
  inputRange: [number, number],
  outputRange: [number, number],
  options?: Parameters<typeof interpolate>[3],
) => {
  if (inputRange[0] >= inputRange[1]) {
    return value < inputRange[1] ? outputRange[0] : outputRange[1];
  }
  return interpolate(value, inputRange, outputRange, options);
};

export const Overlay = ({caption, headline, cues}: OverlayPayload) => {
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

      <CaptionLayer caption={caption} cues={cues} time={time} width={width} height={height} />
    </AbsoluteFill>
  );
};
