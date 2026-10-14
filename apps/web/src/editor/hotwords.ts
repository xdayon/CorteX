export function parseHotwords(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[,\n;]/)) {
    const word = raw.split(/\s+/).filter(Boolean).join(" ").slice(0, 40);
    const key = word.toLowerCase();
    if (!word || seen.has(key)) continue;
    seen.add(key);
    out.push(word);
    if (out.length === 20) break;
  }
  return out;
}
