import { describe, expect, it } from "vitest";
import { bboxToH3Cells } from "./ws-client";
import type { Bbox } from "./api-client";

// Mirrors MAX_SUBSCRIBED_CELLS in ws-client.ts / services/api/ws/live.py.
const MAX_SUBSCRIBED_CELLS = 300;

describe("bboxToH3Cells", () => {
  it("returns a deduplicated, non-empty list of H3 cell ids for a known small bbox", () => {
    const sfBayArea: Bbox = { minLat: 37.3, maxLat: 37.9, minLon: -122.6, maxLon: -122.0 };
    const cells = bboxToH3Cells(sfBayArea);

    expect(cells.length).toBeGreaterThan(0);
    expect(new Set(cells).size).toBe(cells.length);
    for (const cell of cells) {
      expect(typeof cell).toBe("string");
      expect(cell.length).toBeGreaterThan(0);
    }
  });

  it("caps the result at MAX_SUBSCRIBED_CELLS for a bbox covering the whole CONUS", () => {
    const conus: Bbox = { minLat: 24.5, maxLat: 49.5, minLon: -125.0, maxLon: -66.5 };
    const cells = bboxToH3Cells(conus, 5);

    expect(cells.length).toBeLessThanOrEqual(MAX_SUBSCRIBED_CELLS);
  });

  it("handles a degenerate zero-size bbox without throwing", () => {
    const zeroSize: Bbox = { minLat: 10, maxLat: 10, minLon: 10, maxLon: 10 };

    expect(() => bboxToH3Cells(zeroSize)).not.toThrow();
    expect(Array.isArray(bboxToH3Cells(zeroSize))).toBe(true);
  });
});
