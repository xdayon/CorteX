import {Composition, registerRoot} from 'remotion';
import {Overlay} from './Overlay';
import type {OverlayPayload} from './types';

const defaults: OverlayPayload = {
  schemaVersion: 1,
  width: 1080,
  height: 1920,
  fps: 30,
  durationSeconds: 1,
  browserExecutable: '/browser-must-be-supplied-by-renderer',
  caption: {
    enabled: false,
    fontFamily: 'sans-serif',
    fontSize: 54,
    outline: true,
    shadow: true,
    karaoke: true,
    wordsPerCue: 5,
    textColor: '#FFFFFF',
    karaokeColor: '#2CE4D7',
    outlineColor: '#071012',
    shadowColor: '#000000B8',
    animation: {style: 'fade', durationSeconds: 0.18},
  },
  headline: {
    enabled: false,
    text: '',
    fontFamily: 'sans-serif',
    fontSize: 48,
    durationSeconds: 1,
    burstColor: '#11B9AD',
    stripColor: '#FFFFFFF5',
    textColor: '#071012',
    animation: {entrance: 'fade', exit: 'fade', durationSeconds: 0.24},
  },
  words: [],
};

const Root = () => (
  <Composition
    id="CortexOverlay"
    component={Overlay}
    defaultProps={defaults}
    calculateMetadata={({props}) => ({
      width: props.width,
      height: props.height,
      fps: props.fps,
      durationInFrames: Math.max(1, Math.ceil(props.durationSeconds * props.fps)),
    })}
  />
);

registerRoot(Root);
