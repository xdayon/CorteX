import type {OverlayPayload} from './types';

export type HeadlineLayerProps = {
  headline: OverlayPayload['headline'];
  time: number;
  width: number;
  height: number;
};

const clamp = (value: number) => Math.max(0, Math.min(1, value));

/** Shared by the browser preview and export; all animation follows media time. */
export function HeadlineLayer({headline, time, width, height}: HeadlineLayerProps) {
  if (!headline.enabled || !headline.text.trim() || time < 0 || time >= headline.durationSeconds) {
    return null;
  }
  const scale = Math.min(width / 1080, height / 1920);
  const animationDuration = Math.min(headline.animation.durationSeconds, headline.durationSeconds);
  const entrance = headline.animation.entrance === 'none'
    ? 1 : 1 - Math.pow(1 - clamp(time / animationDuration), 3);
  const exit = headline.animation.exit === 'none'
    ? 1 : clamp((headline.durationSeconds - time) / animationDuration);
  const slide = (headline.animation.entrance === 'slide' ? 1 - entrance : 0)
    + (headline.animation.exit === 'slide' ? 1 - exit : 0);

  return (
    <div data-cortex-headline style={{
      position: 'absolute',
      top: height * 0.105,
      left: width * 0.075,
      right: width * 0.075,
      fontFamily: headline.fontFamily,
      fontSize: headline.fontSize * scale,
      fontWeight: 800,
      // Inline backgrounds include padding but do not increase a line box.
      // Reserve that space explicitly, in em, so the next strip cannot paint
      // over accents or descenders when the user changes the font size.
      lineHeight: 1.65,
      opacity: entrance * exit,
      transform: `translateY(${-slide * 28 * scale}px)`,
    }}>
      <div aria-hidden="true" style={{
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
      }}>“</div>
      <span data-cortex-headline-text style={{
        padding: '0.10em 0.22em',
        background: headline.stripColor,
        color: headline.textColor,
        whiteSpace: 'pre-wrap',
        overflowWrap: 'anywhere',
        boxDecorationBreak: 'clone',
        WebkitBoxDecorationBreak: 'clone',
      }}>{headline.text}</span>
    </div>
  );
}
