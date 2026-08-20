/** @param {unknown} value */
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
/** @param {string} path @param {string} expectation @returns {never} */
const fail = (path, expectation) => {
  throw new Error(`Invalid overlay payload at ${path}: expected ${expectation}`);
};
/** @param {unknown} value @param {string} path @param {number} min @param {number} max @param {boolean} [integer] @returns {number} */
const number = (value, path, min, max, integer = false) => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    fail(path, `${integer ? 'an integer' : 'a number'} between ${min} and ${max}`);
  }
  return /** @type {number} */ (value);
};
/** @param {unknown} value @param {string} path @param {number} max @param {boolean} [allowEmpty] @returns {string} */
const string = (value, path, max, allowEmpty = false) => {
  if (typeof value !== 'string' || value.length > max || (!allowEmpty && value.trim().length === 0)) {
    fail(path, `${allowEmpty ? 'a' : 'a non-empty'} string up to ${max} characters`);
  }
  return /** @type {string} */ (value);
};
/** @param {unknown} value @param {string} path @returns {boolean} */
const boolean = (value, path) => {
  if (typeof value !== 'boolean') fail(path, 'a boolean');
  return /** @type {boolean} */ (value);
};
/** @param {unknown} value @param {string} path @returns {string} */
const hexColor = (value, path) => {
  if (typeof value !== 'string' || !/^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(value)) {
    fail(path, 'a hex color #RRGGBB or #RRGGBBAA');
  }
  return value.toUpperCase();
};
/** @template {string} T @param {unknown} value @param {string} path @param {readonly T[]} allowed @returns {T} */
const oneOf = (value, path, allowed) => {
  if (typeof value !== 'string' || !/** @type {readonly string[]} */ (allowed).includes(value)) fail(path, `one of ${allowed.join(', ')}`);
  return /** @type {T} */ (value);
};

/** @param {any} raw @param {string | undefined} browserFallback @returns {import('./src/types.js').OverlayPayload} */
export const parsePayload = (raw, browserFallback) => {
  if (!object(raw)) fail('$', 'an object');
  if (raw.schemaVersion !== 2) fail('schemaVersion', '2');
  if (!object(raw.caption)) fail('caption', 'an object');
  if (!object(raw.headline)) fail('headline', 'an object');
  if (!Array.isArray(raw.cues) || raw.cues.length > 5000) fail('cues', 'an array with at most 5000 entries');

  const width = number(raw.width, 'width', 16, 7680, true);
  const height = number(raw.height, 'height', 16, 7680, true);
  if (width % 2 !== 0 || height % 2 !== 0) fail('width/height', 'even dimensions for yuva420p');
  const durationSeconds = number(raw.durationSeconds, 'durationSeconds', 0.034, 21600);
  let wordCount = 0;
  const cues = raw.cues.map((/** @type {any} */ cue, /** @type {number} */ cueIndex) => {
    if (!object(cue)) fail(`cues[${cueIndex}]`, 'an object');
    const start = number(cue.start, `cues[${cueIndex}].start`, 0, durationSeconds);
    const end = number(cue.end, `cues[${cueIndex}].end`, 0, durationSeconds);
    if (end <= start) fail(`cues[${cueIndex}].end`, 'a value greater than start');
    if (cueIndex > 0 && start < raw.cues[cueIndex - 1].end) fail(`cues[${cueIndex}].start`, 'a non-overlapping cue boundary');
    if (!Array.isArray(cue.words) || cue.words.length > 20) fail(`cues[${cueIndex}].words`, 'an array with at most 20 entries');
    const words = cue.words.map((/** @type {any} */ word, /** @type {number} */ wordIndex) => {
      const path = `cues[${cueIndex}].words[${wordIndex}]`;
      if (!object(word)) fail(path, 'an object');
      const wordStart = number(word.start, `${path}.start`, start, end);
      const wordEnd = number(word.end, `${path}.end`, start, end);
      if (wordEnd <= wordStart) fail(`${path}.end`, 'a value greater than start');
      return {text: string(word.text, `${path}.text`, 200), start: wordStart, end: wordEnd};
    });
    wordCount += words.length;
    if (wordCount > 10000) fail('cues', 'at most 10000 words in total');
    return {start, end, words};
  });
  const executable = raw.browserExecutable ?? browserFallback;

  return {
    schemaVersion: 2,
    width,
    height,
    fps: number(raw.fps, 'fps', 1, 120, true),
    durationSeconds,
    browserExecutable: string(executable, 'browserExecutable', 4096),
    caption: {
      enabled: boolean(raw.caption.enabled, 'caption.enabled'),
      fontFamily: string(raw.caption.fontFamily, 'caption.fontFamily', 200),
      fontSize: number(raw.caption.fontSize, 'caption.fontSize', 8, 320),
      outline: boolean(raw.caption.outline, 'caption.outline'),
      shadow: boolean(raw.caption.shadow, 'caption.shadow'),
      karaoke: boolean(raw.caption.karaoke, 'caption.karaoke'),
      wordsPerCue: number(raw.caption.wordsPerCue, 'caption.wordsPerCue', 1, 20, true),
      positionY: number(raw.caption.positionY, 'caption.positionY', 0.1, 0.9),
      textColor: hexColor(raw.caption.textColor, 'caption.textColor'),
      karaokeColor: hexColor(raw.caption.karaokeColor, 'caption.karaokeColor'),
      outlineColor: hexColor(raw.caption.outlineColor, 'caption.outlineColor'),
      shadowColor: hexColor(raw.caption.shadowColor, 'caption.shadowColor'),
      animation: {
        style: oneOf(raw.caption.animation?.style, 'caption.animation.style', ['none', 'fade', 'pop']),
        durationSeconds: number(raw.caption.animation?.durationSeconds, 'caption.animation.durationSeconds', 0.01, 2),
      },
    },
    headline: {
      enabled: boolean(raw.headline.enabled, 'headline.enabled'),
      text: string(raw.headline.text, 'headline.text', 500, true),
      fontFamily: string(raw.headline.fontFamily, 'headline.fontFamily', 200),
      fontSize: number(raw.headline.fontSize, 'headline.fontSize', 8, 320),
      durationSeconds: number(raw.headline.durationSeconds, 'headline.durationSeconds', 0.034, 60),
      burstColor: hexColor(raw.headline.burstColor, 'headline.burstColor'),
      stripColor: hexColor(raw.headline.stripColor, 'headline.stripColor'),
      textColor: hexColor(raw.headline.textColor, 'headline.textColor'),
      animation: {
        entrance: oneOf(raw.headline.animation?.entrance, 'headline.animation.entrance', /** @type {const} */ (['none', 'fade', 'slide'])),
        exit: oneOf(raw.headline.animation?.exit, 'headline.animation.exit', /** @type {const} */ (['none', 'fade', 'slide'])),
        durationSeconds: number(raw.headline.animation?.durationSeconds, 'headline.animation.durationSeconds', 0.01, 2),
      },
    },
    cues,
  };
};
