import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit .next/standalone so the Docker image can run the server without
  // node_modules. Additive: `next dev` and `next start` are unaffected.
  output: "standalone",
};

export default nextConfig;
