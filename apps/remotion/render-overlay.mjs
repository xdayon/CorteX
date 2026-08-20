#!/usr/bin/env node
import {createHash, randomBytes} from 'node:crypto';
import {access, mkdir, readFile, rename, stat, writeFile} from 'node:fs/promises';
import {constants as fsConstants, createReadStream, writeSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {dirname, isAbsolute, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';
import {parsePayload} from './payload.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const rejectBrowserDownload = () => ({
  version: null,
  onProgress: () => {
    throw new Error('Implicit browser downloads are disabled; configure browserExecutable');
  },
});

/** @param {string[]} argv */
const parseArgs = (argv) => {
  /** @type {Record<string, string>} */
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!['--input', '--output', '--manifest'].includes(flag) || !value) {
      throw new Error('Usage: render-overlay.mjs --input <json> --output <webm> --manifest <json>');
    }
    result[flag.slice(2)] = value;
  }
  if (!result.input || !result.output || !result.manifest || Object.keys(result).length !== 3) {
    throw new Error('Usage: render-overlay.mjs --input <json> --output <webm> --manifest <json>');
  }
  return result;
};

/** @param {string} path */
const sha256 = async (path) => {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
};

/** @param {string} command @param {string[]} args @returns {Promise<string>} */
const run = (command, args) => new Promise((accept, reject) => {
  const child = spawn(command, args, {shell: false, stdio: ['ignore', 'pipe', 'pipe']});
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', (/** @type {string} */ chunk) => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', (/** @type {string} */ chunk) => { stderr += chunk; });
  child.once('error', reject);
  child.once('close', (code) => code === 0 ? accept(stdout) : reject(new Error(`${command} exited ${code}: ${stderr.slice(-2000)}`)));
});

/** @param {string} path @param {unknown} value */
const writeJsonAtomic = async (path, value) => {
  await mkdir(dirname(path), {recursive: true});
  const temporary = `${path}.${process.pid}.${randomBytes(8).toString('hex')}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, {encoding: 'utf8', flag: 'wx'});
  await rename(temporary, path);
};

const main = async () => {
  const args = parseArgs(process.argv.slice(2));
  const inputPath = resolve(args.input);
  const outputPath = resolve(args.output);
  const manifestPath = resolve(args.manifest);
  if (outputPath === manifestPath || inputPath === outputPath || inputPath === manifestPath) {
    throw new Error('Input, output, and manifest paths must be distinct');
  }
  if (!outputPath.toLowerCase().endsWith('.webm')) throw new Error('--output must have a .webm extension');

  const raw = JSON.parse(await readFile(inputPath, 'utf8'));
  const payload = parsePayload(raw, process.env.CORTEX_REMOTION_BROWSER_EXECUTABLE);
  const browserExecutable = isAbsolute(payload.browserExecutable)
    ? payload.browserExecutable
    : resolve(payload.browserExecutable);
  await access(browserExecutable, fsConstants.X_OK);
  await mkdir(dirname(outputPath), {recursive: true});

  const serveUrl = await bundle({
    entryPoint: resolve(here, 'src/index.tsx'),
    outDir: resolve(here, '.cache/bundle'),
    enableCaching: true,
  });
  const composition = await selectComposition({
    serveUrl,
    id: 'CortexOverlay',
    inputProps: payload,
    browserExecutable,
    onBrowserDownload: rejectBrowserDownload,
    logLevel: 'warn',
  });
  await renderMedia({
    serveUrl,
    composition,
    inputProps: payload,
    browserExecutable,
    onBrowserDownload: rejectBrowserDownload,
    outputLocation: outputPath,
    codec: 'vp9',
    pixelFormat: 'yuva420p',
    imageFormat: 'png',
    overwrite: true,
    logLevel: 'warn',
  });

  const probeRaw = await run('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=codec_name,pix_fmt,width,height:stream_tags=alpha_mode',
    '-of', 'json', outputPath,
  ]);
  const probe = JSON.parse(probeRaw);
  const stream = probe.streams?.[0];
  if (stream?.codec_name !== 'vp9' || stream?.tags?.alpha_mode !== '1') {
    throw new Error('Rendered WebM does not advertise VP9 alpha_mode=1');
  }

  const outputStats = await stat(outputPath);
  if (!outputStats.isFile() || outputStats.size <= 0) throw new Error('Remotion produced an empty output');
  const remotionVersion = require('remotion/package.json').version;
  await writeJsonAtomic(manifestPath, {
    schemaVersion: 2,
    renderer: 'remotion',
    outputPath,
    outputSha256: await sha256(outputPath),
    outputSizeBytes: outputStats.size,
    codec: 'vp9',
    pixelFormat: 'yuva420p',
    width: payload.width,
    height: payload.height,
    fps: payload.fps,
    durationSeconds: payload.durationSeconds,
    wordCount: payload.cues.reduce((total, cue) => total + cue.words.length, 0),
    nodeExecutable: process.execPath,
    nodeVersion: process.version,
    remotionVersion,
    captionTextColor: payload.caption.textColor,
    captionKaraokeColor: payload.caption.karaokeColor,
    captionPositionY: payload.caption.positionY,
    headlineBurstColor: payload.headline.burstColor,
    headlineStripColor: payload.headline.stripColor,
  });
};

main().catch((error) => {
  writeSync(2, `${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
  // Remotion may have started a long-running compositor before failing. A
  // passive exitCode leaves that handle alive until the Python 30-minute
  // timeout; explicit exit keeps failures bounded and auditable.
  process.exit(1);
});
