import { describe, expect, it } from "vitest";
import { decodeFrame, FRAME_TYPE_DELTA, FRAME_TYPE_FULL, PROTOCOL_VERSION } from "./ws-protocol";

/**
 * decodeFrame has no corresponding encode helper in this file (that lives in
 * services/common/ws_protocol.py on the backend) so these tests build raw
 * frame buffers by hand, matching the documented little-endian layout:
 *
 *   Header:  u8 version | u8 frame_type | u32 ts_delta_ms | u16 record_count
 *   Record:  u32 icao24_int | i32 lat_e7 | i32 lon_e7 | u16 alt_ft_div8
 *            | u16 heading_deci | i16 vert_rate_div8 | u16 velocity_kt | u8 flags
 */

const HEADER_SIZE = 8;
const RECORD_SIZE = 21;
const FLAG_ON_GROUND = 0b0000_0001;

interface TestRecord {
  icao24Hex: string;
  lat: number;
  lon: number;
  altFt: number;
  headingDeg: number;
  vertRateFpm: number;
  velocityKt: number;
  onGround: boolean;
}

function buildFrame(frameType: number, tsDeltaMs: number, records: TestRecord[]): ArrayBuffer {
  const buffer = new ArrayBuffer(HEADER_SIZE + records.length * RECORD_SIZE);
  const view = new DataView(buffer);
  let offset = 0;

  view.setUint8(offset, PROTOCOL_VERSION);
  offset += 1;
  view.setUint8(offset, frameType);
  offset += 1;
  view.setUint32(offset, tsDeltaMs, true);
  offset += 4;
  view.setUint16(offset, records.length, true);
  offset += 2;

  for (const r of records) {
    view.setUint32(offset, parseInt(r.icao24Hex, 16), true);
    offset += 4;
    view.setInt32(offset, Math.round(r.lat * 1e7), true);
    offset += 4;
    view.setInt32(offset, Math.round(r.lon * 1e7), true);
    offset += 4;
    view.setUint16(offset, Math.round(r.altFt / 8), true);
    offset += 2;
    view.setUint16(offset, Math.round(r.headingDeg * 10), true);
    offset += 2;
    view.setInt16(offset, Math.round(r.vertRateFpm / 8), true);
    offset += 2;
    view.setUint16(offset, Math.round(r.velocityKt), true);
    offset += 2;
    view.setUint8(offset, r.onGround ? FLAG_ON_GROUND : 0);
    offset += 1;
  }

  return buffer;
}

describe("decodeFrame", () => {
  it("round-trips a FRAME_TYPE_FULL frame", () => {
    const records: TestRecord[] = [
      {
        icao24Hex: "a1b2c3",
        lat: 37.6188,
        lon: -122.375,
        altFt: 35000, // exact multiple of 8 so div8 round-trips exactly
        headingDeg: 180,
        vertRateFpm: -800, // exact multiple of 8
        velocityKt: 450,
        onGround: false,
      },
    ];
    const buffer = buildFrame(FRAME_TYPE_FULL, 1234, records);
    const decoded = decodeFrame(buffer);

    expect(decoded.version).toBe(PROTOCOL_VERSION);
    expect(decoded.frameType).toBe(FRAME_TYPE_FULL);
    expect(decoded.tsDeltaMs).toBe(1234);
    expect(decoded.records).toHaveLength(1);

    const r = decoded.records[0];
    expect(r.icao24).toBe("a1b2c3");
    expect(r.lat).toBeCloseTo(37.6188, 6);
    expect(r.lon).toBeCloseTo(-122.375, 6);
    expect(r.altFt).toBe(35000);
    expect(r.headingDeg).toBe(180);
    expect(r.vertRateFpm).toBe(-800);
    expect(r.velocityKt).toBe(450);
    expect(r.onGround).toBe(false);
  });

  it("round-trips a FRAME_TYPE_DELTA frame with an on-ground record", () => {
    const records: TestRecord[] = [
      {
        icao24Hex: "000001",
        lat: 0,
        lon: 0,
        altFt: 0,
        headingDeg: 0,
        vertRateFpm: 0,
        velocityKt: 0,
        onGround: true,
      },
    ];
    const buffer = buildFrame(FRAME_TYPE_DELTA, 500, records);
    const decoded = decodeFrame(buffer);

    expect(decoded.frameType).toBe(FRAME_TYPE_DELTA);
    expect(decoded.tsDeltaMs).toBe(500);
    expect(decoded.records).toHaveLength(1);
    expect(decoded.records[0].icao24).toBe("000001");
    expect(decoded.records[0].onGround).toBe(true);
  });

  it("decodes multiple records in one frame", () => {
    const records: TestRecord[] = [
      {
        icao24Hex: "aaaaaa",
        lat: 10,
        lon: 20,
        altFt: 8000,
        headingDeg: 90,
        vertRateFpm: 0,
        velocityKt: 200,
        onGround: false,
      },
      {
        icao24Hex: "bbbbbb",
        lat: -10,
        lon: -20,
        altFt: 16000,
        headingDeg: 270,
        vertRateFpm: 800,
        velocityKt: 300,
        onGround: false,
      },
    ];
    const decoded = decodeFrame(buildFrame(FRAME_TYPE_FULL, 0, records));
    expect(decoded.records).toHaveLength(2);
    expect(decoded.records.map((r) => r.icao24)).toEqual(["aaaaaa", "bbbbbb"]);
  });

  it("throws on a buffer shorter than the header", () => {
    const buffer = new ArrayBuffer(4);
    expect(() => decodeFrame(buffer)).toThrow();
  });

  it("throws on a frame truncated mid-record", () => {
    const full = buildFrame(FRAME_TYPE_FULL, 0, [
      {
        icao24Hex: "a1b2c3",
        lat: 1,
        lon: 1,
        altFt: 100,
        headingDeg: 1,
        vertRateFpm: 1,
        velocityKt: 1,
        onGround: false,
      },
    ]);
    const truncated = full.slice(0, full.byteLength - 5);
    expect(() => decodeFrame(truncated)).toThrow();
  });
});
