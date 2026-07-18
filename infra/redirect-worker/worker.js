// Cloudflare Worker: 301 every request on hndshake.com / www.hndshake.com
// to the canonical host, preserving path and query. Routes are declared in
// wrangler.toml; deploy with `npx wrangler deploy` from this directory.
export default {
  fetch(request) {
    const url = new URL(request.url);
    url.hostname = "permitstatus.hndshake.com";
    return Response.redirect(url.toString(), 301);
  },
};
