"""Static report rendering and private immutable, digest-bound file bundles."""
from __future__ import annotations

import hashlib
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from uuid import uuid4

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether


MAX_FILE_BYTES = 20 * 1024 * 1024
TOKEN = re.compile(r"[0-9a-f]{32}-[0-9a-f]{64}\Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sections(snapshot):
    """A shared content model keeps HTML and PDF evidence/counts aligned."""
    asset, summary = snapshot["asset"], snapshot["summary"]
    yield "报告范围", [
        f"报告 #{snapshot['reportId']} | 资产 #{asset['scopeId']} | {asset['domain']}",
        f"统计区间：{snapshot['periodBegin']} 至 {snapshot['periodEnd']} | 生成时间：{snapshot['generatedAt']}",
        snapshot["basis"], f"当前页面数：{asset['pageCount']}；模板：{snapshot['templateVersion']}"]
    yield "巡检概览", [
        f"任务数：{summary['tasks']}；Finding 条数：{summary['findingRows']}；去重风险数：{summary['distinctRisks']}；待处理风险：{summary['activeRisks']}",
        "严重度分布：" + "；".join(f"{k}={v}" for k, v in summary["bySeverity"].items()),
        "当前状态分布：" + "；".join(f"{k}={v}" for k, v in summary["byStatus"].items()),
        "零条结果仅表示本区间已记录任务无相关发现，不能证明全站安全。", snapshot["evidencePolicy"]]
    yield "任务清单", [f"任务 #{t['taskId']} | {t['createdAt']} | {t['status']}" for t in snapshot["tasks"]] or ["区间内无任务。"]
    yield "每日趋势", [f"{t['date']} | 任务 {t['tasks']} | 当日去重风险 {t['distinctRisks']}" for t in snapshot["trend"]]
    for finding in snapshot["findings"]:
        yield f"风险 #{finding['findingId']} · {finding['title']}", [
            f"类型：{finding['type']} | 等级：{finding['severity']} | 当前状态：{finding['status']}",
            "来源任务：" + ", ".join(map(str, finding["taskIds"])),
            "来源 Finding：" + ", ".join(map(str, finding["findingIds"])),
            f"结果集首次观测：{finding['firstSeen']}；最后观测：{finding['lastSeen']}",
            f"证据 SHA256：{finding['evidenceSha256']}",
            f"影响：{finding['impact']}", f"修复建议：{finding['suggestion']}"]
    if not snapshot["findings"]:
        yield "风险明细", ["本结果集无风险记录。"]


def render_html(snapshot):
    content = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
               "<meta name='viewport' content='width=device-width,initial-scale=1'>",
               "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">",
               "<title>安全巡检报告</title><style>body{font:15px/1.7 system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 24px;color:#203047}h1{color:#12345a}h2{border-bottom:1px solid #ccd8e5;margin-top:28px;padding-bottom:8px}p{overflow-wrap:anywhere;margin:6px 0}section{break-inside:avoid}</style>",
               "<body><h1>安全巡检报告</h1>"]
    for heading, lines in sections(snapshot):
        content.append("<section><h2>" + escape(heading) + "</h2>" + "".join("<p>" + escape(line) + "</p>" for line in lines) + "</section>")
    return ("".join(content) + "</body></html>").encode("utf-8")


def render_pdf(snapshot):
    # CID Chinese font avoids dependence on a Windows font path or external assets.
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    regular = ParagraphStyle("ReportBody", fontName="STSong-Light", fontSize=10, leading=16,
                             wordWrap="CJK", splitLongWords=True, spaceAfter=5)
    heading = ParagraphStyle("ReportHeading", parent=regular, fontSize=13, leading=20,
                             textColor=colors.HexColor("#12345a"), spaceBefore=13, spaceAfter=7, keepWithNext=True)
    title = ParagraphStyle("ReportTitle", parent=heading, fontSize=22, leading=30, alignment=TA_CENTER)
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=(210 * mm, 297 * mm), leftMargin=20 * mm,
                            rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
                            title="安全巡检报告", author="AnHeng Inspection", pageCompression=1)
    flow = [Paragraph("安全巡检报告", title), Spacer(1, 5 * mm)]
    first_risk = True
    for label, lines in sections(snapshot):
        group = [Paragraph(escape(label), heading)] + [Paragraph(escape(line), regular) for line in lines]
        if label.startswith("风险 #"):
            if first_risk:
                flow.append(PageBreak())
                first_risk = False
            # Keep a risk's evidence and advice together; oversize groups may still split safely.
            flow.append(KeepTogether(group))
        else:
            flow.extend(group)

    def footer(canvas, document):
        canvas.setFont("STSong-Light", 9)
        canvas.setFillColor(colors.HexColor("#52657a"))
        canvas.drawString(20 * mm, 11 * mm, f"T26-v1 | Report #{snapshot['reportId']} | 脱敏导出")
        canvas.drawRightString(190 * mm, 11 * mm, f"第 {document.page} 页")

    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


class ReportStorage:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError("report root must be a real, private directory")

    def publish(self, snapshot):
        files = {"snapshot.json": canonical(snapshot), "report.html": render_html(snapshot), "report.pdf": render_pdf(snapshot)}
        if any(len(value) > MAX_FILE_BYTES for value in files.values()):
            raise ValueError("report exceeds file limit")
        manifest = canonical({key: {"sha256": digest(value), "bytes": len(value)} for key, value in files.items()})
        token = uuid4().hex + "-" + digest(manifest)
        staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.root))
        try:
            for name, value in {**files, "manifest.json": manifest}.items():
                path = staging / name
                with path.open("xb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                path.chmod(0o600)
            for attempt in range(6):
                try:
                    os.replace(staging, self.root / token)
                    break
                except PermissionError as error:
                    # Windows scanners may briefly retain a directory handle after close.
                    if os.name != "nt" or error.winerror not in {5, 32} or attempt == 5:
                        raise
                    time.sleep(0.02 * 2**attempt)
            return token
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _read(self, folder, name, limit):
        path = folder / name
        if path.is_symlink() or path.resolve().parent != folder or not path.is_file():
            raise ValueError("invalid report path")
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("report exceeds file limit")
        return content

    def read(self, token, format):
        if not isinstance(token, str) or not TOKEN.fullmatch(token) or format not in {"html", "pdf"}:
            raise ValueError("invalid report token or format")
        folder = self.root / token
        if folder.is_symlink() or folder.resolve().parent != self.root or not folder.is_dir():
            raise ValueError("invalid bundle path")
        manifest_bytes = self._read(folder, "manifest.json", 4096)
        if digest(manifest_bytes) != token[33:]:
            raise ValueError("manifest integrity failure")
        manifest = json.loads(manifest_bytes)
        filename = "report." + format
        value = self._read(folder, filename, MAX_FILE_BYTES)
        if manifest[filename] != {"sha256": digest(value), "bytes": len(value)}:
            raise ValueError("report integrity failure")
        return value
