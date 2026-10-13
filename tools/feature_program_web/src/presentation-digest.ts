import type { Json } from './model';

// Match the producer's sorted, two-space, ASCII JSON with its final LF. This is
// used only for supplied content bindings; it is not a secret or authentication API.
export function canonicalJson(value: Json): string {
  const quote = (value: string) =>
    JSON.stringify(value).replace(
      /[\u007f-\uffff]/g,
      (character) => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'),
    );
  const compare = (left: string, right: string) => {
    const a = Array.from(left, (c) => c.codePointAt(0)!),
      b = Array.from(right, (c) => c.codePointAt(0)!);
    for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i]! - b[i]!;
    return a.length - b.length;
  };
  function encode(item: Json, level: number): string {
    if (typeof item === 'string') return quote(item);
    if (!item || typeof item !== 'object') return JSON.stringify(item);
    const indent = '  '.repeat(level),
      child = indent + '  ';
    if (Array.isArray(item))
      return item.length
        ? '[\n' +
            item.map((entry) => child + encode(entry, level + 1)).join(',\n') +
            '\n' +
            indent +
            ']'
        : '[]';
    const entries = Object.entries(item).sort(([a], [b]) => compare(a, b));
    return entries.length
      ? '{\n' +
          entries
            .map(([key, entry]) => child + quote(key) + ': ' + encode(entry, level + 1))
            .join(',\n') +
          '\n' +
          indent +
          '}'
      : '{}';
  }
  return encode(value, 0) + '\n';
}

const constants = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);
const rotate = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits));

/** Synchronous SHA-256 keeps the captured display view atomic at render time. */
export function presentationDigest(value: Json): string {
  const source = new TextEncoder().encode(canonicalJson(value));
  const bytes = new Uint8Array(Math.ceil((source.length + 9) / 64) * 64);
  bytes.set(source);
  bytes[source.length] = 128;
  const data = new DataView(bytes.buffer);
  data.setUint32(bytes.length - 8, Math.floor(source.length / 0x20000000));
  data.setUint32(bytes.length - 4, (source.length * 8) >>> 0);
  const state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const words = new Uint32Array(64);
  for (let offset = 0; offset < bytes.length; offset += 64) {
    for (let index = 0; index < 16; index++) words[index] = data.getUint32(offset + index * 4);
    for (let index = 16; index < 64; index++) {
      const x = words[index - 15]!,
        y = words[index - 2]!;
      const a = rotate(x, 7) ^ rotate(x, 18) ^ (x >>> 3);
      const b = rotate(y, 17) ^ rotate(y, 19) ^ (y >>> 10);
      words[index] = words[index - 16]! + a + words[index - 7]! + b;
    }
    let [a, b, c, d, e, f, g, h] = [...state] as [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
    ];
    for (let index = 0; index < 64; index++) {
      const first =
        (h +
          (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) +
          ((e & f) ^ (~e & g)) +
          constants[index]! +
          words[index]!) >>>
        0;
      const second =
        ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + first) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (first + second) >>> 0;
    }
    [a, b, c, d, e, f, g, h].forEach((word, index) => {
      state[index] = state[index]! + word;
    });
  }
  return [...state].map((word) => word.toString(16).padStart(8, '0')).join('');
}
