import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // セルフホスト（Pi の Docker。robot/selfhost/web.Dockerfile）でビルドするときだけ、
  // node_modules なしで動くサーバー一式（.next/standalone）を書き出す。Vercel 版には影響しない
  output: process.env.NEXT_OUTPUT_STANDALONE === "1" ? "standalone" : undefined,
};

export default nextConfig;
