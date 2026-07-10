# Deploying ivanovresearch.dev

Static site lives in `site/` (`index.html` + `CNAME`). No build step — deploy the directory as-is.

## 0. Domain registration

- Register `ivanovresearch.dev` at any registrar (Cloudflare Registrar has at-cost pricing; Namecheap/Porkbun also fine).
- Note: `.dev` is on the HSTS preload list — the site **only** works over HTTPS. Both options below provide certificates automatically; just don't skip the HTTPS steps.

## Option A — GitHub Pages

### A1. Via Actions workflow (recommended, serves `site/` from `main`)

1. Create `.github/workflows/pages.yml`:

   ```yaml
   name: Deploy site
   on:
     push:
       branches: [main]
       paths: ["site/**"]
     workflow_dispatch:
   permissions:
     contents: read
     pages: write
     id-token: write
   concurrency:
     group: pages
     cancel-in-progress: true
   jobs:
     deploy:
       runs-on: ubuntu-latest
       environment:
         name: github-pages
         url: ${{ steps.deployment.outputs.page_url }}
       steps:
         - uses: actions/checkout@v4
         - uses: actions/upload-pages-artifact@v3
           with:
             path: site
         - id: deployment
           uses: actions/deploy-pages@v4
   ```

2. Repo → **Settings → Pages** → Source: **GitHub Actions**.
3. Push to `main`; the workflow publishes `site/`. (On Actions deploys GitHub ignores the `CNAME` file — the custom domain comes from step A4; the file matters only for Option A2.)

### A2. Via `gh-pages` branch (no workflow)

```bash
git subtree split --prefix site -b gh-pages
git push origin gh-pages
git branch -D gh-pages
```

Repo → **Settings → Pages** → Source: **Deploy from a branch** → `gh-pages` / `(root)`.
Repeat the three commands on every site update (or use the Actions variant to avoid that).

### A3. DNS for GitHub Pages

At your DNS provider:

| Type  | Name | Value |
|-------|------|-------|
| A     | `@`  | `185.199.108.153` |
| A     | `@`  | `185.199.109.153` |
| A     | `@`  | `185.199.110.153` |
| A     | `@`  | `185.199.111.153` |
| CNAME | `www` | `<github-username>.github.io` |

(Optionally AAAA records: `2606:50c0:8000::153` … `2606:50c0:8003::153`.)

### A4. HTTPS

- Settings → Pages → Custom domain: enter `ivanovresearch.dev`, wait for the DNS check to pass.
- Tick **Enforce HTTPS** once the certificate is issued (Let's Encrypt, automatic; can take up to ~1 hour after DNS propagates).
- Recommended: Settings → Pages → **verify the domain** for the account (prevents domain takeover if Pages is ever disabled).

## Option B — Cloudflare Pages

1. Move DNS to Cloudflare (free plan): add the site in the Cloudflare dashboard, switch nameservers at the registrar. (If the domain is registered with Cloudflare, this is already done.)
2. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git** → pick the repo.
   - Framework preset: **None**
   - Build command: *(empty)*
   - Build output directory: `site`
3. Every push to `main` deploys automatically; PRs get preview URLs.
4. Custom domain: in the Pages project → **Custom domains → Add** → `ivanovresearch.dev`. Cloudflare creates the needed `CNAME` record (proxied) automatically:

   | Type  | Name | Value | Proxy |
   |-------|------|-------|-------|
   | CNAME | `@`  | `<project>.pages.dev` | Proxied |
   | CNAME | `www` | `<project>.pages.dev` | Proxied |

5. HTTPS is automatic (Cloudflare Universal SSL). Set **SSL/TLS mode: Full (strict)** and enable **Always Use HTTPS** — mandatory for `.dev`.
6. The `CNAME` file is ignored by Cloudflare Pages (it's a GitHub Pages artifact); harmless to keep.

Alternative without Git integration: `npx wrangler pages deploy site --project-name ivanovresearch` (needs `wrangler login` once).

## Verification

```bash
curl -I https://ivanovresearch.dev        # expect 200, valid cert
curl -I http://ivanovresearch.dev         # expect 301 → https
```

Check the page in light and dark mode and on a phone-width viewport.

## Notes

- No secrets are involved anywhere in this site or its deployment.
- If both options are ever active, make sure only one owns the DNS records — pick one and delete the other's records.
