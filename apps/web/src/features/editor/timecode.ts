export function formatBlockTime(seconds: number) {
  const value = Math.max(0, seconds);
  const minutes = Math.floor(value / 60);
  const rest = (value - minutes * 60).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${rest}`;
}
