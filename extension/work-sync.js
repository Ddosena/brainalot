// Transfer and verification for the canonical local Work API. No browser storage lives here.
export const CHUNK_BYTES = 128 * 1024;

export function bytesOf(value) { return new TextEncoder().encode(value); }

export async function sha256(bytes) {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("");
}

export function base64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

export async function uploadBlob(api, bytes) {
  if (bytes.byteLength > 64 * 1024 * 1024) throw new Error("Work blob exceeds 64 MiB");
  const hash = await sha256(bytes);
  const total = Math.max(1, Math.ceil(bytes.byteLength / CHUNK_BYTES));
  for (let index = 0; index < total; index++) {
    const part = bytes.subarray(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES);
    const receipt = await api(`/api/work/blobs/${hash}/chunks`, "POST", {
      index, total, content_base64: base64(part),
    });
    if (receipt.blob !== hash || receipt.total !== total || receipt.received < index + 1)
      throw new Error("Work blob upload receipt mismatch");
    if (index === total - 1 && (!receipt.complete || receipt.size !== bytes.byteLength))
      throw new Error("Work blob upload incomplete");
  }
  return hash;
}

export async function prepareCommit(api, state, images) {
  const encodedState = bytesOf(JSON.stringify(state));
  const state_blob = await uploadBlob(api, encodedState);
  const records = [];
  for (const [id, blob] of images) {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    records.push({ id, blob: await uploadBlob(api, bytes), mime: blob.type || "application/octet-stream" });
  }
  return { state_blob, images: records };
}

function equivalent(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  const ak = Object.keys(a).sort(), bk = Object.keys(b).sort();
  return ak.length === bk.length && ak.every((key, i) => key === bk[i] && equivalent(a[key], b[key]));
}

export async function verifyCommit(api, readImage, expected, state, images, hashes) {
  if (!expected?.initialized || !Number.isInteger(expected.revision)) throw new Error("Work commit receipt invalid");
  const remote = await api("/api/work/state");
  if (remote.revision !== expected.revision || !equivalent(remote.state, state))
    throw new Error("Work state verification failed");
  if (remote.images?.length !== images.length) throw new Error("Work image count verification failed");
  const listed = new Map(remote.images.map(row => [row.id, row]));
  for (const [id, blob] of images) {
    const meta = listed.get(id);
    if (!meta || meta.blob !== hashes.get(id) || meta.size !== blob.size || meta.mime !== (blob.type || "application/octet-stream"))
      throw new Error(`Work image metadata verification failed: ${id}`);
    const received = new Uint8Array(await readImage(id));
    if (received.byteLength !== blob.size || await sha256(received) !== meta.blob)
      throw new Error(`Work image bytes verification failed: ${id}`);
  }
  return remote;
}
