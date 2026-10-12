#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语义检索 Web demo 服务器（2026 创新大赛路演用）

仅用标准库 http.server，零新增依赖：
    GET /                → 单页检索 UI（内联 HTML/JS，浅色简洁风格）
    GET /api/search?q=..&n=..  → JSON 接口

启动：python scripts/search_demo_server.py  （默认端口 8765）
"""
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from semantic_search import do_search  # noqa: E402

PORT = 8765

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>合工大通知检索 · Demo</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         background: #f5f7fa; color: #2c3e50; min-height: 100vh; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 48px 20px; }
  h1 { font-size: 24px; text-align: center; margin-bottom: 6px; }
  .sub { text-align: center; color: #8a9bb0; font-size: 13px; margin-bottom: 28px; }
  .searchbar { display: flex; gap: 10px; margin-bottom: 14px; }
  .searchbar input { flex: 1; padding: 12px 16px; font-size: 16px;
        border: 1px solid #d5dee8; border-radius: 8px; outline: none; }
  .searchbar input:focus { border-color: #4a90d9; }
  .searchbar button { padding: 12px 24px; font-size: 15px; color: #fff;
        background: #4a90d9; border: none; border-radius: 8px; cursor: pointer; }
  .searchbar button:hover { background: #3a7bc0; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }
  .chips span { padding: 5px 12px; background: #fff; border: 1px solid #d5dee8;
        border-radius: 999px; font-size: 13px; color: #5a6c80; cursor: pointer; }
  .chips span:hover { border-color: #4a90d9; color: #4a90d9; }
  .meta { color: #8a9bb0; font-size: 12px; margin-bottom: 14px; }
  .card { background: #fff; border: 1px solid #e3e9f0; border-radius: 10px;
        padding: 16px 18px; margin-bottom: 12px; transition: box-shadow .15s; }
  .card:hover { box-shadow: 0 4px 14px rgba(74,144,217,.12); }
  .card .title { font-size: 15px; font-weight: 600; color: #2c3e50; }
  .card .title a { color: #2c3e50; text-decoration: none; }
  .card .title a:hover { color: #4a90d9; }
  .card .info { margin-top: 8px; font-size: 12px; color: #8a9bb0;
        display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  .score { background: #eaf3fc; color: #3a7bc0; padding: 2px 9px;
        border-radius: 999px; font-size: 12px; }
  .empty { text-align: center; color: #aab8c8; padding: 40px 0; font-size: 14px; }
  .footer { text-align: center; color: #b6c2cf; font-size: 12px; margin-top: 40px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>合工大通知信息 · 语义检索</h1>
  <div class="sub">BM25 + 本地 LLM 降级链 · 零 API 成本 · 数据来源：本地归档 257 条</div>
  <div class="searchbar">
    <input id="q" type="text" placeholder="输入查询，如：保研 / 竞赛报名 / 奖学金"
           onkeydown="if(event.key==='Enter')search()">
    <button onclick="search()">搜索</button>
  </div>
  <div class="chips">
    <span onclick="quick('保研')">保研</span>
    <span onclick="quick('竞赛报名')">竞赛报名</span>
    <span onclick="quick('奖学金')">奖学金</span>
    <span onclick="quick('校运会')">校运会</span>
    <span onclick="quick('讲座')">讲座</span>
  </div>
  <div id="meta" class="meta"></div>
  <div id="results"><div class="empty">输入查询词开始检索</div></div>
  <div class="footer">hfut_info_monitor · 2026 创新大赛 demo</div>
</div>
<script>
async function search(){
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const box = document.getElementById('results');
  box.innerHTML = '<div class="empty">检索中…</div>';
  try{
    const r = await fetch('/api/search?q='+encodeURIComponent(q)+'&n=8');
    const d = await r.json();
    document.getElementById('meta').textContent =
      '引擎：' + d.engine + ' · 库内 ' + d.total + ' 条' +
      (d.rewritten ? ' · LLM 改写：' + d.rewritten : '');
    if(!d.hits.length){ box.innerHTML='<div class="empty">无命中结果</div>'; return; }
    box.innerHTML = d.hits.map(h=>
      '<div class="card"><div class="title"><a href="'+h.url+'" target="_blank">'
      + esc(h.title) + '</a></div><div class="info"><span class="score">相关度 '
      + h.score + '</span><span>'+esc(h.date)+'</span><span>'+esc(h.source)
      +'</span></div></div>').join('');
  }catch(e){
    box.innerHTML = '<div class="empty">请求失败：'+e+'</div>';
  }
}
function quick(w){ document.getElementById('q').value = w; search(); }
function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html")
        elif parsed.path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0].strip()
            try:
                n = int((qs.get("n") or ["5"])[0])
            except ValueError:
                n = 5
            if not q:
                self._send(400, json.dumps({"error": "缺少参数 q"},
                            ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return
            try:
                result = do_search(q, max(1, min(n, 20)))
                self._send(200, json.dumps(result, ensure_ascii=False,
                            indent=2).encode("utf-8"), "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)},
                            ensure_ascii=False).encode("utf-8"),
                           "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):
        pass  # 静默日志，路演时终端干净


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"语义检索 demo 已启动: http://127.0.0.1:{port}")
    print(f"API: http://127.0.0.1:{port}/api/search?q=保研  (Ctrl+C 退出)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
