import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type DriveState, type ExportBatch } from "../../api";
import { ExportBatchPanel } from "./ExportBatchPanel";

vi.mock("../../api", async importOriginal => {
  const real = await importOriginal<typeof import("../../api")>();
  return { ...real, api: { ...real.api, driveStates: vi.fn(), retryDrive: vi.fn() } };
});

const batch = {
  id: "b1", project_id: "p1", workflow_run_id: "r1", revision_id: "v1", status: "succeeded",
  items: [{ id: "i1", position: 0, clip_key: "k1", title: "Corte um", status: "succeeded", render_artifact_id: "a1" }],
} as unknown as ExportBatch;

const show = () => render(<ExportBatchPanel batches={[batch]} busy={false} progress={100} onCancel={() => undefined} onRetry={() => undefined} />);
beforeEach(() => vi.mocked(api.driveStates).mockReset());
afterEach(cleanup);

test("a finished clip shows where it landed on Google Drive", async () => {
  vi.mocked(api.driveStates).mockResolvedValue({ i1: { state: "done", path: "CorteX/Episódio/2026-11-10/01 - Corte um.mp4" } });
  show();
  expect(await screen.findByText("No Drive")).toBeTruthy();
  expect(screen.getByText(/CorteX\/Episódio\/2026-11-10\/01 - Corte um\.mp4/)).toBeTruthy();
});

test("a failed upload shows the reason and can be sent again", async () => {
  const failed: DriveState = { state: "failed", error: "Login do Google Drive expirou: rode `rclone config reconnect gdrive:`." };
  vi.mocked(api.driveStates).mockResolvedValue({ i1: failed });
  vi.mocked(api.retryDrive).mockResolvedValue({ i1: { state: "uploading" } });
  show();
  expect(await screen.findByText("Drive falhou")).toBeTruthy();
  expect(screen.getByText(/Login do Google Drive expirou/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reenviar ao Drive" }));
  await waitFor(() => expect(api.retryDrive).toHaveBeenCalledWith("p1", "b1"));
  expect(await screen.findByText("Enviando ao Drive…")).toBeTruthy();
});

test("with the Drive off nothing about it is shown", async () => {
  vi.mocked(api.driveStates).mockResolvedValue({ i1: { state: "off" } });
  show();
  await waitFor(() => expect(api.driveStates).toHaveBeenCalled());
  expect(screen.queryByText(/Drive/)).toBeNull();
});
