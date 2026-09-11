import {CaptionLayer} from './CaptionLayer';
import {HeadlineLayer} from './HeadlineLayer';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import type {OverlayPayload} from './types';

export const Overlay = ({caption, headline, cues}: OverlayPayload) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const time = frame / fps;

  return (
    <AbsoluteFill style={{backgroundColor: 'transparent', color: 'white'}}>
      <HeadlineLayer headline={headline} time={time} width={width} height={height} />
      <CaptionLayer caption={caption} cues={cues} time={time} width={width} height={height} />
    </AbsoluteFill>
  );
};
