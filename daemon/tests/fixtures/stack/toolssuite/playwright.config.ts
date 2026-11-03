import { defineConfig } from '@playwright/test';

const port = process.env.PORT ?? '4300';

export default defineConfig({
  webServer: { command: `npm start -- --port ${port}`, url: `http://localhost:${port}` },
});
