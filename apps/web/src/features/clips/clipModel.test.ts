import { expect, test } from "vitest";
import { exportItem } from "../../test/clipFixtures";
import { deriveStatus, formatScore, withState } from "./clipModel";
import { clipRow } from "../../test/clipFixtures";

test("posted comes first, then discarded, saved, exported, approved", () => {
  const base = { decision: null, discard_reason: null, saved: true, saved_at: null, posted: true, posted_at: null, notes: "", updated_at: "x" } as const;
  expect(deriveStatus(base, [exportItem()])).toBe("posted");
  expect(deriveStatus({ ...base, posted: false }, [exportItem()])).toBe("saved");
  expect(deriveStatus({ ...base, posted: false, saved: false }, [exportItem()])).toBe("exported");
  expect(withState(clipRow(1), { posted: true }).status).toBe("posted");
});

test("scores show a decimal only when they are not whole", () => {
  expect([9, 9.7, 10, 8.5].map(formatScore)).toEqual(["9", "9,7", "10", "8,5"]);
});
