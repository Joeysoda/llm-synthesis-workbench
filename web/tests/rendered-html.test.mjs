import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Chinese local workbench shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/);
  assert.match(html, /<title>本机合成数据工作台<\/title>/);
  assert.match(html, /合成数据平台/);
  assert.match(html, /Synthetic Data Kit/);
  assert.match(html, /Easy Dataset/);
  assert.match(html, /SynLogic/);
  assert.match(html, /仅限本机访问/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/);
});

test("client implements six routes and plain desktop layout", async () => {
  const [page, css, layout, packageJson, synthetic, easy, synlogic, tasks, settings] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../app/synthetic/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/easy-dataset/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/synlogic/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/tasks/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/settings/page.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(page, /选择一个工具开始/);
  assert.match(synthetic, /普通 QA/);
  assert.match(synthetic, /CoT 思维链/);
  assert.match(synthetic, /JSON 最小格式示例/);
  assert.match(easy, /文档问答/);
  assert.match(easy, /数据蒸馏/);
  assert.match(easy, /图片问答/);
  assert.match(easy, /评估数据/);
  assert.match(synlogic, /调用原始验证器/);
  assert.match(tasks, /查看需要处理的任务/);
  assert.match(settings, /LLM_CREDENTIAL_ROTATED=true/);
  assert.match(css, /\.sidebar/);
  assert.doesNotMatch(css, /linear-gradient|radial-gradient|timeline/);
  assert.match(layout, /lang="zh-CN"/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});
