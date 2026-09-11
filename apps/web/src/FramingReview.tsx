import { api, type CameraScene, type SceneFramingOverride, type SuggestedClip } from "./api";

export function FramingReview({ projectId, sourceId, scenes, clips, overrides, disabled, onChange }: {
  projectId: string; sourceId: string; scenes: CameraScene[]; clips: SuggestedClip[];
  overrides: SceneFramingOverride[]; disabled: boolean;
  onChange: (overrides: SceneFramingOverride[]) => void;
}) {
  const visible = scenes.filter((scene) => clips.some((clip) =>
    scene.start_us / 1e6 < clip.end_second && scene.end_us / 1e6 > clip.start_second));
  if (!visible.length) return null;
  return <details className="simple-card framing-review">
    <summary>Ajustar enquadramento por câmera · {visible.length} planos</summary>
    <p>Se o automático escolher o lado errado, corrija o plano e renderize novamente. A escolha vale para os cortes deste episódio; o áudio permanece igual.</p>
    <div className="framing-review-grid">{visible.map((scene) => {
      const selected = overrides.find((item) => item.scene_index === scene.scene_index)?.target || "auto";
      return <label key={scene.scene_index}>
        <img loading="lazy" src={api.sourcePreviewUrl(projectId, sourceId,
          (scene.start_us + Math.min(1e6, (scene.end_us - scene.start_us) / 2)) / 1e6)} alt={`Plano ${scene.scene_index + 1}`}/>
        <span>Plano {scene.scene_index + 1} · {Math.floor(scene.start_us / 1e6)}s</span>
        <select aria-label={`Enquadramento do plano ${scene.scene_index + 1}`} disabled={disabled} value={selected}
          onChange={(event) => {
            const rest = overrides.filter((item) => item.scene_index !== scene.scene_index);
            onChange(event.target.value === "auto" ? rest : [...rest, {
              scene_index: scene.scene_index, target: event.target.value as SceneFramingOverride["target"],
            }]);
          }}>
          <option value="auto">Automático</option><option value="left">Pessoa à esquerda</option>
          <option value="right">Pessoa à direita</option><option value="full">Quadro inteiro / ambos</option>
        </select>
      </label>;
    })}</div>
  </details>;
}
