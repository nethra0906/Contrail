/**
 * Decoder for the binary `/ws/live` wire protocol. Mirrors
 * `services/common/ws_protocol.py` byte-for-byte - this file has no
 * independent authority over the format, that module does. If the two ever
 * disagree, this one is wrong.
 *
 * Frame layout (little-endian):
 *   Header:  u8 version | u8 frame_type | u32 ts_delta_ms | u16 record_count
 *   Record:  u32 icao24_int | i32 lat_e7 | i32 lon_e7 | u16 alt_ft_div8
 *            | u16 heading_deci | i16 vert_rate_div8 | u16 velocity_kt | u8 flags
 */

export const PROTOCOL_VERSION = 1;
export const FRAME_TYPE_FULL = 0;
export const FRAME_TYPE_DELTA = 1;

const HEADER_SIZE = 8; // u8 + u8 + u32 + u16
const RECORD_SIZE = 21; // u32 + i32 + i32 + u16 + u16 + i16 + u16 + u8

const FLAG_ON_GROUND = 0b0000_0001;

export interface AircraftRecord {
  icao24: string;
  lat: number;
  lon: number;
  altFt: number;
  headingDeg: number;
  vertRateFpm: number;
  velocityKt: number;
  onGround: boolean;
}

export interface DecodedFrame {
  version: number;
  frameType: number;
  tsDeltaMs: number;
  records: AircraftRecord[];
}

export function decodeFrame(data: ArrayBuffer): DecodedFrame {
  if (data.byteLength < HEADER_SIZE) {
    throw new Error(`frame shorter than header: ${data.byteLength} bytes`);
  }

  const view = new DataView(data);
  let offset = 0;

  const version = view.getUint8(offset);
  offset += 1;
  const frameType = view.getUint8(offset);
  offset += 1;
  const tsDeltaMs = view.getUint32(offset, true);
  offset += 4;
  const recordCount = view.getUint16(offset, true);
  offset += 2;

  const expectedLength = HEADER_SIZE + recordCount * RECORD_SIZE;
  if (data.byteLength !== expectedLength) {
    throw new Error(
      `frame length ${data.byteLength} != expected ${expectedLength} for ${recordCount} records`
    );
  }

  const records: AircraftRecord[] = new Array(recordCount);
  for (let i = 0; i < recordCount; i++) {
    const icao24Int = view.getUint32(offset, true);
    offset += 4;
    const latE7 = view.getInt32(offset, true);
    offset += 4;
    const lonE7 = view.getInt32(offset, true);
    offset += 4;
    const altDiv8 = view.getUint16(offset, true);
    offset += 2;
    const headingDeci = view.getUint16(offset, true);
    offset += 2;
    const vrateDiv8 = view.getInt16(offset, true);
    offset += 2;
    const gsKt = view.getUint16(offset, true);
    offset += 2;
    const flags = view.getUint8(offset);
    offset += 1;

    records[i] = {
      icao24: icao24Int.toString(16).padStart(6, "0"),
      lat: latE7 / 1e7,
      lon: lonE7 / 1e7,
      altFt: altDiv8 * 8,
      headingDeg: headingDeci / 10,
      vertRateFpm: vrateDiv8 * 8,
      velocityKt: gsKt,
      onGround: (flags & FLAG_ON_GROUND) !== 0,
    };
  }

  return { version, frameType, tsDeltaMs, records };
}
