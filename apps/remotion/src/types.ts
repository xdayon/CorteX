export type TimedWord = {
  text: string;
  start: number;
  end: number;
};

export type OverlayPayload = Record<string, unknown> & {
  schemaVersion: 1;
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
  words: TimedWord[];
};
