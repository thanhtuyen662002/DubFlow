// JSON data files bundled into the app (esbuild and Vite inline them).
declare module "*.json" {
  const value: unknown;
  export default value;
}
