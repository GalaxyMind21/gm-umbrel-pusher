// lib/payout-store.js. Explicit private-Blob loader for the payout watch (gm-discord-bot needs "@vercel/blob" >= 2.3).
// Uses a SEPARATE private Blob store (vercel blob create-store --access private), connected to the project; auth is OIDC
// on Vercel (PAYOUT_BLOB_READ_WRITE_TOKEN from the prefixed store connection). Nothing here is ever publicly readable. `sdk` is injectable for tests.
import * as blob from "@vercel/blob";
export function makeStore(sdk = blob) {
  const auth = process.env.PAYOUT_BLOB_READ_WRITE_TOKEN ? { token: process.env.PAYOUT_BLOB_READ_WRITE_TOKEN } : {};
  return {
    async loadJson(path) {
      let r;
      try { r = await sdk.get(path, { access: "private", useCache: false, ...auth }); }
      catch (e) { if (e?.name === "BlobNotFoundError" || /not.?found/i.test(String(e?.message))) return null; throw e; }
      if (!r || r.statusCode !== 200) return null;
      return JSON.parse(await new Response(r.stream).text());
    },
    async putJson(path, obj) {
      await sdk.put(path, JSON.stringify(obj), { access: "private", allowOverwrite: true, addRandomSuffix: false, contentType: "application/json", ...auth });
    },
  };
}
