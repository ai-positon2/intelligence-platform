import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// `base` must match the Flask route this app is actually served from
// (see app.py's `ad_intelligence()` route), otherwise the built index.html
// references /assets/... at the site root, which 404s once this app is
// mounted under a path prefix. Every `npm run build` (including the CI
// rebuild workflow) regenerates index.html from this config, so fixing it
// here, rather than hand-editing the built index.html, survives future
// rebuilds.
export default defineConfig({
  base: '/p2/b2b-agents/ad-intelligence/',
  plugins: [react()],
})
