"""Trang web nhỏ để thử API ① getCert và API ② download của eLIS (api_tester).

CHỈ ĐỌC: không có nút nào gọi API ③, không ghi mooc_log.db. API ② là POST
nhưng chỉ tải file về, không đổi trạng thái bản ghi trên eLIS.

API key eLIS ở lại phía server (đọc từ .env như job thật) — trình duyệt chỉ
nói chuyện với trang này, không bao giờ thấy key.

Dữ liệu hiện ra là dữ liệu nhân viên THẬT, nên mặc định chỉ nghe 127.0.0.1.
Chạy trên server thì mở bằng SSH port-forward thay vì mở cổng ra ngoài:
    ssh -L 8000:127.0.0.1:8000 <user>@<server>

Cách dùng (từ thư mục MOOC):
    .venv/bin/python tools/api_tester.py              # http://127.0.0.1:8000
    .venv/bin/python tools/api_tester.py --port 8080
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import logging
import secrets
import sys
import time
from collections import OrderedDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
# Settings đọc .env theo thư mục hiện hành.
os.chdir(PROJECT_ROOT)

import magic  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, Response  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import client  # noqa: E402
from config import settings  # noqa: E402

logger = logging.getLogger("api_tester")

app = FastAPI(title="eLIS API tester", docs_url="/docs")

# File vừa tải qua API ②, giữ trong RAM để trình duyệt xem trước. Chỉ giữ vài
# file gần nhất: chứng chỉ vài MB mỗi cái.
_FILES: "OrderedDict[str, tuple[bytes, str, str]]" = OrderedDict()
_MAX_FILES = 20


#1. API cho trang web

@app.get("/api/config")
def api_config() -> dict:
    return {"elis_base_url": settings.elis_base_url,
            "elis_file_base_url": settings.elis_file_base_url,
            "api_key_set": bool(settings.elis_api_key),
            "max_page_size": client.MAX_PAGE_SIZE}


@app.get("/api/cert")
def api_cert(status: str = "", employee_email: str = "",
             page: int = 1, size: int = 100) -> dict:
    """API ① getCert với bộ tham số tùy chọn. Tham số rỗng thì không gửi."""
    params = {"page": page, "size": size}
    if status:
        params["status"] = status
    if employee_email:
        params["employeeEmail"] = employee_email.strip()

    started = time.monotonic()
    try:
        items = client._get_cert(params)
    except client.ElisError as e:
        return {"ok": False, "params": params, "error": str(e),
                "status_code": e.status_code,
                "seconds": round(time.monotonic() - started, 2)}
    return {"ok": True, "params": params, "count": len(items), "items": items,
            "seconds": round(time.monotonic() - started, 2)}


class DownloadRequest(BaseModel):
    pairs: list[dict] = Field(
        description='[{"UserCourseId": "...", "certificate_id": "..."}], tối đa 20')


@app.post("/api/download")
def api_download(req: DownloadRequest) -> dict:
    """API ② download-certificates. Trả metadata + token để xem file."""
    pairs = [{"UserCourseId": str(p.get("UserCourseId") or "").strip(),
              "certificate_id": str(p.get("certificate_id") or "").strip()}
             for p in req.pairs]
    pairs = [p for p in pairs if p["UserCourseId"] or p["certificate_id"]]
    if not pairs:
        raise HTTPException(400, "Cần ít nhất một cặp UserCourseId + certificate_id")

    started = time.monotonic()
    try:
        files = client.download_certificates(pairs)
    except client.ElisError as e:
        return {"ok": False, "request": pairs, "error": str(e),
                "status_code": e.status_code,
                "seconds": round(time.monotonic() - started, 2)}

    out = []
    for f in files:
        data = f.get("anh_bytes") or b""
        mime = magic.from_buffer(data[:8192], mime=True) if data else ""
        token = secrets.token_urlsafe(12)
        _FILES[token] = (data, mime, str(f.get("ten_file") or "certificate"))
        while len(_FILES) > _MAX_FILES:
            _FILES.popitem(last=False)
        out.append({"userCourseId": f.get("userCourseId"),
                    "certificate_id": f.get("certificate_id"),
                    "ten_file": f.get("ten_file"),
                    "bytes": len(data), "mime": mime,
                    "unsupported_mime": f.get("unsupported_mime"),
                    "token": token})

    returned = {str(f.get("userCourseId")) for f in files}
    missing = [p["UserCourseId"] for p in pairs if p["UserCourseId"] not in returned]
    return {"ok": True, "request": pairs, "files": out, "missing": missing,
            "seconds": round(time.monotonic() - started, 2)}


@app.get("/api/file/{token}")
def api_file(token: str, download: bool = False) -> Response:
    if token not in _FILES:
        raise HTTPException(404, "File đã hết hạn trong bộ nhớ — tải lại qua API ②")
    data, mime, name = _FILES[token]
    disposition = "attachment" if download else "inline"
    safe_name = "".join(c if c.isascii() and c not in '"\\' else "_" for c in name)
    return Response(data, media_type=mime or "application/octet-stream",
                    headers={"Content-Disposition": f'{disposition}; filename="{safe_name}"',
                             "Cache-Control": "no-store"})


#2. Trang web

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


PAGE = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>eLIS API tester</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1d2330; --muted:#667085; --line:#e3e6eb;
          --accent:#2457d6; --ok:#12805c; --err:#c0362c; --warn:#b26b00; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
         background:var(--bg); color:var(--ink); }
  header { padding:14px 20px; background:var(--card); border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; }
  header .env { color:var(--muted); font-size:13px; }
  header .env b { color:var(--ink); }
  main { padding:16px 20px; display:grid; gap:16px; max-width:1500px; }
  section { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
  h2 { font-size:15px; margin:0 0 10px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:end; }
  label { display:flex; flex-direction:column; gap:3px; font-size:12px; color:var(--muted); }
  input, select { font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:6px;
                  min-width:120px; background:#fff; color:var(--ink); }
  input.wide { min-width:330px; }
  button { font:inherit; padding:7px 14px; border-radius:6px; border:1px solid var(--accent);
           background:var(--accent); color:#fff; cursor:pointer; }
  button.ghost { background:#fff; color:var(--accent); }
  button.small { padding:3px 9px; font-size:12px; }
  button:disabled { opacity:.55; cursor:wait; }
  .status { margin:10px 0 0; font-size:13px; }
  .status.ok { color:var(--ok); } .status.err { color:var(--err); white-space:pre-wrap; }
  .tablewrap { overflow:auto; max-height:520px; margin-top:10px; border:1px solid var(--line); border-radius:6px; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { padding:6px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
  th { position:sticky; top:0; background:#f0f2f5; font-weight:600; white-space:nowrap; }
  tr:hover td { background:#f7f9fd; }
  td.mono, .mono { font-family:ui-monospace, Menlo, Consolas, monospace; font-size:12px; }
  /* Tên file dài không có khoảng trắng: cho ngắt ở bất kỳ ký tự nào để bảng không tràn ngang. */
  td.wrap { min-width:160px; max-width:260px; overflow-wrap:anywhere; word-break:break-word; }
  .pill { display:inline-block; padding:1px 7px; border-radius:10px; font-size:11px; font-weight:600; }
  .WAITING { background:#fff3d6; color:var(--warn); } .APPROVED { background:#dcf5ea; color:var(--ok); }
  .REJECTED { background:#fde4e1; color:var(--err); }
  pre { background:#0f1420; color:#dfe6f3; padding:10px; border-radius:6px; overflow:auto;
        max-height:360px; font-size:12px; margin:8px 0 0; }
  .grid2 { display:grid; grid-template-columns: minmax(0,1fr) minmax(0,1.3fr); gap:16px; }
  @media (max-width: 1000px) { .grid2 { grid-template-columns: 1fr; } }
  .preview { margin-top:10px; border:1px solid var(--line); border-radius:6px; min-height:120px;
             display:flex; align-items:center; justify-content:center; background:#fafbfc; }
  .preview iframe { width:100%; height:720px; border:0; }
  .preview img { max-width:100%; max-height:720px; }
  .muted { color:var(--muted); }
  .warnbox { background:#fff3d6; color:#6b4300; padding:8px 10px; border-radius:6px; margin-top:8px; }
</style></head>
<body>
<header>
  <h1>eLIS API tester</h1>
  <span class="env" id="env">đang đọc cấu hình…</span>
  <span class="env">Chỉ đọc — không có API ③</span>
</header>
<main>
<section>
  <h2>API ① getCert</h2>
  <div class="row">
    <label>status
      <select id="status">
        <option value="WAITING">WAITING</option><option value="APPROVED">APPROVED</option>
        <option value="REJECTED">REJECTED</option><option value="">(không gửi)</option>
      </select></label>
    <label>employeeEmail (để trống = không gửi)<input id="email" class="wide" placeholder="abc@fpt.com"></label>
    <label>page<input id="page" type="number" value="1" min="0" style="min-width:70px;width:80px"></label>
    <label>size<input id="size" type="number" value="100" min="1" max="1000" style="min-width:80px;width:90px"></label>
    <button id="btnCert">Gọi API ①</button>
    <label>Lọc kết quả<input id="filter" placeholder="tên, email, khóa, id…"></label>
  </div>
  <div class="muted" style="font-size:12px;margin-top:6px">
    Gọi theo email thì thường bỏ status để lấy mọi trạng thái (job dùng cách này để kiểm nộp trùng).</div>
  <div id="certStatus" class="status"></div>
  <div class="tablewrap" id="certWrap" hidden><table id="certTable"></table></div>
</section>

<div class="grid2">
<section>
  <h2>Bản ghi đã chọn</h2>
  <div class="muted" id="pickHint">Bấm một dòng trong bảng để xem toàn bộ trường.</div>
  <pre id="rowJson" hidden></pre>
</section>

<section>
  <h2>API ② download-certificates</h2>
  <div class="row">
    <label>UserCourseId<input id="ucId" class="wide mono"></label>
    <label>certificate_id<input id="certId" class="wide mono"></label>
    <button id="btnDl">Gọi API ②</button>
  </div>
  <div id="dlStatus" class="status"></div>
  <div id="dlInfo"></div>
  <div class="preview" id="preview" hidden></div>
</section>
</div>
</main>

<script>
const $ = id => document.getElementById(id);
let rows = [];
const COLS = [
  ["#", (r, i) => i + 1],
  ["employeeName", r => r.employeeName],
  ["employeeEmail", r => r.employeeEmail],
  ["courseName", r => r.courseName],
  ["providerName", r => r.providerName],
  ["submitStatus", r => `<span class="pill ${esc(r.submitStatus)}">${esc(r.submitStatus)}</span>`, true],
  ["submitDatetime", r => (r.submitDatetime || "").replace("T", " ").slice(0, 19)],
  ["ActionDateTime", r => (r.ActionDateTime || "").replace("T", " ").slice(0, 19)],
  ["ActionBy", r => r.ActionBy],
  ["comment", r => r.comment],
  ["certificateName", r => r.certificateName, false, "wrap"],
  ["id", r => r.id, false, "mono"],
  ["API ②", (r, i) => `<button class="small ghost" data-dl="${i}">Tải file</button>`, true],
];

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

async function loadConfig() {
  try {
    const c = await (await fetch("/api/config")).json();
    $("env").innerHTML = `eLIS: <b>${esc(c.elis_base_url)}</b> · file: <b>${esc(c.elis_file_base_url)}</b>`
      + (c.api_key_set ? "" : ' · <b style="color:#c0362c">ELIS_API_KEY trống</b>');
  } catch (e) { $("env").textContent = "Không đọc được cấu hình"; }
}

function renderTable() {
  const q = $("filter").value.trim().toLowerCase();
  const shown = rows.map((r, i) => [r, i]).filter(([r]) => !q || JSON.stringify(r).toLowerCase().includes(q));
  const head = "<tr>" + COLS.map(c => `<th>${c[0]}</th>`).join("") + "</tr>";
  const body = shown.map(([r, i]) => `<tr data-row="${i}">` + COLS.map(c => {
      const v = c[1](r, i);
      return `<td class="${c[3] || ""}">${c[2] ? v : esc(v)}</td>`;
    }).join("") + "</tr>").join("");
  $("certTable").innerHTML = head + body;
  $("certWrap").hidden = rows.length === 0;
}

async function callCert() {
  const btn = $("btnCert"); btn.disabled = true;
  $("certStatus").className = "status"; $("certStatus").textContent = "Đang gọi API ①…";
  const p = new URLSearchParams({status: $("status").value, employee_email: $("email").value,
                                 page: $("page").value || 1, size: $("size").value || 100});
  try {
    const d = await (await fetch("/api/cert?" + p)).json();
    if (!d.ok) {
      rows = []; renderTable();
      $("certStatus").className = "status err";
      $("certStatus").textContent = `LỖI (${d.seconds}s, HTTP ${d.status_code ?? "-"}): ${d.error}\nparams: ${JSON.stringify(d.params)}`;
      return;
    }
    rows = d.items; renderTable();
    const byStatus = {};
    rows.forEach(r => byStatus[r.submitStatus] = (byStatus[r.submitStatus] || 0) + 1);
    $("certStatus").className = "status ok";
    $("certStatus").textContent = `${d.count} bản ghi trong ${d.seconds}s · ` +
      Object.entries(byStatus).map(([k, v]) => `${k}: ${v}`).join(" · ") +
      ` · params: ${JSON.stringify(d.params)}` +
      (d.count >= d.params.size ? " · ĐẦY TRANG — còn trang sau" : "");
  } catch (e) {
    $("certStatus").className = "status err"; $("certStatus").textContent = "Lỗi gọi trang: " + e;
  } finally { btn.disabled = false; }
}

function pickRow(i) {
  const r = rows[i];
  $("pickHint").hidden = true;
  $("rowJson").hidden = false;
  $("rowJson").textContent = JSON.stringify(r, null, 2);
  $("ucId").value = r.id || "";
  $("certId").value = r.certificate_id || "";
}

async function callDownload() {
  const btn = $("btnDl"); btn.disabled = true;
  $("dlStatus").className = "status"; $("dlStatus").textContent = "Đang gọi API ②…";
  $("dlInfo").innerHTML = ""; $("preview").hidden = true; $("preview").innerHTML = "";
  try {
    const res = await fetch("/api/download", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({pairs: [{UserCourseId: $("ucId").value, certificate_id: $("certId").value}]})});
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || res.status);
    if (!d.ok) {
      $("dlStatus").className = "status err";
      $("dlStatus").textContent = `LỖI (${d.seconds}s, HTTP ${d.status_code ?? "-"}): ${d.error}`;
      return;
    }
    $("dlStatus").className = "status ok";
    $("dlStatus").textContent = `${d.files.length} file trong ${d.seconds}s`;
    let html = "";
    if (d.missing.length) html += `<div class="warnbox">eLIS không trả file cho: ${d.missing.map(esc).join(", ")}</div>`;
    for (const f of d.files) {
      html += `<table style="margin-top:8px"><tr><th>ten_file</th><td>${esc(f.ten_file)}</td></tr>
        <tr><th>userCourseId</th><td class="mono">${esc(f.userCourseId)}</td></tr>
        <tr><th>certificate_id</th><td class="mono">${esc(f.certificate_id)}</td></tr>
        <tr><th>Kích thước</th><td>${(f.bytes / 1024).toFixed(1)} KB</td></tr>
        <tr><th>MIME thật</th><td>${esc(f.mime)}</td></tr>
        <tr><th>Hệ thống đọc được?</th><td>${f.unsupported_mime
            ? `<b style="color:#c0362c">KHÔNG (${esc(f.unsupported_mime)}) → job xếp vào ca BỎ QUA</b>`
            : '<b style="color:#12805c">Có</b>'}</td></tr>
        <tr><th>File</th><td><a href="/api/file/${f.token}" target="_blank">mở tab mới</a> ·
            <a href="/api/file/${f.token}?download=true">tải xuống</a></td></tr></table>`;
    }
    $("dlInfo").innerHTML = html;
    const f = d.files[0];
    if (f) {
      const url = "/api/file/" + f.token;
      if (f.mime === "application/pdf") $("preview").innerHTML = `<iframe src="${url}"></iframe>`;
      else if (f.mime.startsWith("image/")) $("preview").innerHTML = `<img src="${url}" alt="certificate">`;
      else $("preview").innerHTML = `<span class="muted">Không xem trước được loại ${esc(f.mime)} — dùng link tải xuống.</span>`;
      $("preview").hidden = false;
    }
  } catch (e) {
    $("dlStatus").className = "status err"; $("dlStatus").textContent = "Lỗi: " + e.message;
  } finally { btn.disabled = false; }
}

$("btnCert").onclick = callCert;
$("btnDl").onclick = callDownload;
$("filter").oninput = renderTable;
$("email").onkeydown = e => { if (e.key === "Enter") callCert(); };
$("certTable").onclick = e => {
  const dl = e.target.closest("[data-dl]");
  if (dl) { pickRow(+dl.dataset.dl); callDownload(); return; }
  const tr = e.target.closest("tr[data-row]");
  if (tr) pickRow(+tr.dataset.row);
};
loadConfig();
</script>
</body></html>
"""


#3. Điểm vào

def main() -> int:
    p = argparse.ArgumentParser(description="Trang web thử API ① và ② của eLIS (chỉ đọc).")
    p.add_argument("--host", default="127.0.0.1",
                   help="Mặc định 127.0.0.1 — dữ liệu nhân viên thật, đừng mở ra ngoài")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
