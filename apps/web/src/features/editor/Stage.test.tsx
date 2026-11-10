import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { defaultRenderSettings } from "../../editor/settings";
import { clipRow } from "../../test/clipFixtures";
import { Stage } from "./Stage";
import { EMPTY_PREVIEW, type PreviewView } from "./previewQueue";

vi.mock("./PreviewPlayer", () => ({ PreviewPlayer: () => <div /> }));
vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: { ...actual.api, editPlan: vi.fn().mockResolvedValue(undefined) } };
});

afterEach(cleanup);

const view = (phase: PreviewView["phase"], extra: Partial<PreviewView> = {}) => ({ ...EMPTY_PREVIEW, phase, progress: 0, ...extra }) as PreviewView;
const show = (preview: PreviewView) => render(<Stage row={clipRow(1)} preview={preview} overlay={null} overlayError="" settings={defaultRenderSettings}
  scenes={null} corrections={[]} time={0} onTime={() => undefined} onRetry={() => undefined} />);

test("the parity chip appears only once the preview is ready", () => {
  show(view("planning"));
  expect(screen.queryByText("Prévia igual ao export")).toBeNull();
  cleanup();
  show(view("ready", { videoUrl: "/v.mp4" }));
  expect(screen.getByText("Prévia igual ao export")).toBeTruthy();
});
