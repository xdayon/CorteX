export type TimedWord = {
  text: string;
  start: number;
  end: number;
};

export type CaptionCue = {
  start: number;
  end: number;
  words: TimedWord[];
};

export type OverlayPayload = Record<string, unknown> & {
  schemaVersion: 2;
  width: number;
  height: number;
  fps: number;
  durationSeconds: number;
  browserExecutable: string;
  caption: {
    enabled: boolean;
    fontFamily: string;
    fontSize: number;
    outline: boolean;
    shadow: boolean;
    karaoke: boolean;
    wordsPerCue: number;
    positionY: number;
    textColor: string;
    karaokeColor: string;
    outlineColor: string;
    shadowColor: string;
    animation: { style: "none" | "fade" | "pop"; durationSeconds: number };
  };
  headline: {
    enabled: boolean;
    text: string;
    fontFamily: string;
    fontSize: number;
    durationSeconds: number;
    burstColor: string;
    stripColor: string;
    textColor: string;
    animation: { entrance: "none" | "fade" | "slide"; exit: "none" | "fade" | "slide"; durationSeconds: number };
  };
  cues: CaptionCue[];
};
