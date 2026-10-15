# Phil Showcase Website

A one-page site for **Phil**, built with [Astro](https://astro.build/) and deployed on [Cloudflare](https://pages.cloudflare.com/).

## Local Development

```bash
cd site
npm install
npm run dev
```

Visit `http://localhost:4321` in your browser.

## Build

```bash
npm run build
npm run preview
```

## Cloudflare Deployment from GitHub

This website is structured as a monorepo subfolder inside `site/`. When deploying via the Cloudflare dashboard:

1. In Cloudflare, go to **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
2. Select your repository: `ahochoy/phil`.
3. Set the build settings:
   - **Framework preset**: `Astro`
   - **Root directory**: `site`
   - **Build command**: `npm run build`
   - **Build output directory**: `dist/client` (or `dist` for standard static)
4. Click **Save and Deploy**.

### Custom Domain

Once deployed, go to **Custom domains** in the Cloudflare Pages project settings and add your custom domain or subdomain. Cloudflare provisions the certificate.

## Configuration & Links

All links, author details, and metrics can be configured in [`src/config.ts`](./src/config.ts).
