/// <reference types="vite/client" />

declare module "*.less" {
  const classes: { [key: string]: string };
  export default classes;
}

/** Injected by vite.config.ts — port the dev server listens on. */
declare const DEV_SERVER_PORT: number | undefined;