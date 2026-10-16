import { useEffect, useState } from "react";
import type { EpisodeEntry } from "../../api";
import { Icon } from "../../ui";

export function EpisodeThumb({ entry, className = "" }: { entry: Pick<EpisodeEntry, "thumbnail_url" | "title">; className?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [entry.thumbnail_url]);
  return <span className={`thumb ${className}`}>
    {entry.thumbnail_url && !failed
      ? <img src={entry.thumbnail_url} alt="" loading="lazy" decoding="async" width={480} height={270} onError={() => setFailed(true)} />
      : <Icon name="film" />}
  </span>;
}
