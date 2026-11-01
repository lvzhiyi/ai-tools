// 构建前端：src/ → ../studio/web/static/（产物提交进仓库，用控制台的人不需要装 Node）
// npm run build   一次性构建（压缩）
// npm run dev     监听改动自动重新构建，刷新浏览器即可
import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";

const watch = process.argv.includes("--watch");
const outdir = "../studio/web/static";
mkdirSync(outdir, { recursive: true });
copyFileSync("index.html", `${outdir}/index.html`);

const options = {
  entryPoints: { app: "src/main.tsx" },
  bundle: true,
  outdir,
  format: "esm",
  target: "es2022",
  jsx: "automatic",
  minify: !watch,
  sourcemap: watch ? "inline" : false,
  loader: { ".css": "css" },
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
  // 本机没有 node_modules 时可以用 NODE_PATH 指向全局安装的包
  nodePaths: process.env.NODE_PATH ? process.env.NODE_PATH.split(":") : [],
  logLevel: "info",
};

if (watch) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
  console.log("监听中：改动 src/ 会自动重新构建");
} else {
  await esbuild.build(options);
}
