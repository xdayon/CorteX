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
  if (raw.schemaVersion !== 1) fail('schemaVersion', '1');
  if (!object(raw.caption)) fail('caption', 'an object');
  if (!object(raw.headline)) fail('headline', 'an object');
  if (!Array.isArray(raw.words) || raw.words.length > 10000) fail('words', 'an array with at most 10000 entries');

  const width = number(raw.width, 'width', 16, 7680, true);
  const height = number(raw.height, 'height', 16, 7680, true);
  if (width % 2 !== 0 || height % 2 !== 0) fail('width/height', 'even dimensions for yuva420p');
  const durationSeconds = number(raw.durationSeconds, 'durationSeconds', 0.034, 21600);
  const words = raw.words.map((/** @type {any} */ word, /** @type {number} */ index) => {
    if (!object(word)) fail(`words[${index}]`, 'an object');
    const start = number(word.start, `words[${index}].start`, 0, durationSeconds);
    const end = number(word.end, `words[${index}].end`, 0, durationSeconds);
    if (end <= start) fail(`words[${index}].end`, 'a value greater than start');
    if (index > 0 && start < raw.words[index - 1].start) fail(`words[${index}].start`, 'non-decreasing word order');
    return {text: string(word.text, `words[${index}].text`, 200), start, end};
  });
  const executable = raw.browserExecutable ?? browserFallback;

  return {
    schemaVersion: 1,
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
    words,
  };
};
