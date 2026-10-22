# -*- coding: utf-8 -*-
"""
压缩核心算法 v2

把图片或 PDF 压到指定字节上限，输出 jpg / png / pdf。
支持中途取消与进度上报。

策略：
- 优先保住分辨率，其次才降质量：先按原尺寸试最高质量，不达标才逐级缩小
- png 走调色板降色深，jpg 走质量二分
- 格式为 auto 时两种都试一遍，取保留像素更多的那一种
"""

import io
import os
from collections import namedtuple

import pymupdf
from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
SUPPORTED_EXTS = IMAGE_EXTS | {".pdf"}

RENDER_DPI = 200          # PDF 页渲染上限，够清晰又不至于太占内存
MIN_JPG_Q = 55            # JPEG 质量底线，低于此值宁肯缩尺寸
PNG_COLOR_STEPS = (256, 224, 192, 160, 128, 96, 64, 48, 32, 24, 16)
SCALES = (1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52, 0.44, 0.36, 0.28)

# 逼近上限时留一点余量，避免不同工具按不同口径判超
SAFETY = 0.985

Result = namedtuple("Result", "path size fmt")


class Cancelled(Exception):
    """用户中止"""
    pass


def _tick(cancel):
    if cancel and cancel():
        raise Cancelled()


# ---------------------------------------------------------------- 编码

def _encode(im, fmt, quality=None, colors=None):
    buf = io.BytesIO()
    if fmt == "jpg":
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
        im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                    dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _best_jpeg(im, target_bytes, cancel=None, progress=None):
    """二分找当前尺寸下能达标的最大质量，返回 (data, q) 或 None"""
    lo, hi, best = 30, 95, None
    step = 0
    while lo <= hi:
        step += 1
        if progress:
            progress(min(0.95, step / 7.0))
        _tick(cancel)
        mid = (lo + hi) // 2
        data = _encode(im, "jpg", quality=mid)
        if len(data) <= target_bytes:
            best = (data, mid)
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def fit_image(im, target_bytes, fmt, cancel=None, progress=None):
    """
    在不超过 target_bytes 的前提下，返回清晰度最高的编码结果。
    返回 (data, scale, 说明)，失败返回 (None, None, None)
    """
    im = im.convert("RGB")
    w0, h0 = im.size
    total = len(SCALES)
    target_bytes = int(target_bytes * SAFETY)

    for idx, scale in enumerate(SCALES):
        _tick(cancel)
        if scale >= 0.999:
            cur = im
        else:
            cur = im.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))),
                            Image.LANCZOS)
        last = (idx == total - 1)

        if fmt == "jpg":
            hit = _best_jpeg(
                cur, target_bytes, cancel,
                (lambda v, i=idx: progress(i / total + v / total)) if progress else None)
            if hit:
                if hit[1] >= MIN_JPG_Q or last:
                    return hit[0], scale, "q%d" % hit[1]
        else:
            if progress:
                progress(idx / total)
            for c in PNG_COLOR_STEPS:
                _tick(cancel)
                data = _encode(cur, "png", colors=c)
                if len(data) <= target_bytes:
                    return data, scale, "%d色" % c
    return None, None, None


def fit_auto(im, target_bytes, cancel=None, progress=None):
    """png / jpg 各试一次，取保留像素更多的那种"""
    png_prog = (lambda v: progress(v * 0.5)) if progress else None
    jpg_prog = (lambda v: progress(0.5 + v * 0.5)) if progress else None

    p = fit_image(im, target_bytes, "png", cancel, png_prog)
    j = fit_image(im, target_bytes, "jpg", cancel, jpg_prog)

    if p[0] is None and j[0] is None:
        return None, None, None, None
    if p[0] is None:
        return j[0], j[1], j[2], "jpg"
    if j[0] is None:
        return p[0], p[1], p[2], "png"

    # 谁的缩放比例高，说明谁保留的像素多
    if p[1] > j[1] + 0.03:
        return p[0], p[1], p[2], "png"
    if j[1] > p[1] + 0.03:
        return j[0], j[1], j[2], "jpg"

    # 同等缩放：png 色数被压得很低而 jpg 质量还行，说明这张图不适合 PNG
    try:
        colors = int(p[2].replace("色", ""))
        q = int(j[2].replace("q", ""))
    except ValueError:
        colors, q = 256, 80
    if colors <= 32 and q >= 68:
        return j[0], j[1], j[2], "jpg"
    return p[0], p[1], p[2], "png"


def best_encode(im, target_bytes, fmt, cancel=None, progress=None):
    """统一入口，返回 (data, scale, info, 实际格式)"""
    if fmt == "auto":
        return fit_auto(im, target_bytes, cancel, progress)
    data, scale, info = fit_image(im, target_bytes, fmt, cancel, progress)
    return data, scale, info, fmt


# ---------------------------------------------------------------- 载入

def probe(path):
    """读文件基本信息，返回 (说明文字, 页数)"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        doc = pymupdf.open(path)
        n = doc.page_count
        r = doc[0].rect
        doc.close()
        return "%d 页 · %.0f×%.0fmm" % (n, r.width / 72 * 25.4, r.height / 72 * 25.4), n
    im = Image.open(path)
    w, h = im.size
    im.close()
    return "%d×%d" % (w, h), 1


def load_pages(path, dpi=RENDER_DPI, cancel=None):
    """读入文件，返回页面图像列表"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        doc = pymupdf.open(path)
        zoom = dpi / 72.0
        pages = []
        for pg in doc:
            _tick(cancel)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                colorspace=pymupdf.csRGB)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        doc.close()
        return pages

    with Image.open(path) as src:
        src.seek(0)
        im = src.convert("RGB")
    return [im]


def stitch(images, gap=18):
    """多页上下拼接成一张，宽度取最宽页"""
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (w, h), "white")
    y = 0
    for im in images:
        canvas.paste(im, ((w - im.width) // 2, y))
        y += im.height + gap
    return canvas


# ---------------------------------------------------------------- PDF

def compress_pdf_to_pdf(src, target_bytes, cancel=None):
    """PDF 进 PDF 出。无损优化 → 内嵌图重压 → 整页转图"""
    _tick(cancel)
    doc = pymupdf.open(src)

    try:
        doc.subset_fonts()
    except Exception:
        pass
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True, deflate_images=True,
             clean=True, use_objstms=1)
    if len(buf.getvalue()) <= target_bytes:
        doc.close()
        return buf.getvalue(), "无损优化"

    # 内嵌大图换 JPEG
    changed = False
    for page in doc:
        _tick(cancel)
        for item in page.get_images(full=True):
            xref = item[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.width * pix.height < 60000:
                    continue
                if pix.alpha or pix.n != 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                page.replace_image(xref, stream=_encode(img, "jpg", quality=80))
                changed = True
            except Exception:
                continue

    if changed:
        buf = io.BytesIO()
        doc.save(buf, garbage=4, deflate=True, deflate_images=True,
                 clean=True, use_objstms=1)
        if len(buf.getvalue()) <= target_bytes:
            doc.close()
            return buf.getvalue(), "图片重压"
    doc.close()

    # 降级：整页渲染成图片再包回去（文字层丢失）
    pages = load_pages(src, cancel=cancel)
    n = max(1, len(pages))
    ratio = 0.85
    buf = None
    for _ in range(5):
        _tick(cancel)
        per_page = max(15000, int(target_bytes * ratio / n))
        doc_out = pymupdf.open()
        for pg in pages:
            data, _, _ = fit_image(pg, per_page, "jpg", cancel)
            if data is None:
                continue
            im = Image.open(io.BytesIO(data))
            wpt = 595.0
            hpt = wpt * im.height / im.width
            page = doc_out.new_page(width=wpt, height=hpt)
            # 直接引用已压好的 JPEG 字节，不再二次编码
            page.insert_image(pymupdf.Rect(0, 0, wpt, hpt), stream=data)
        buf = io.BytesIO()
        doc_out.save(buf, garbage=4, deflate=True, deflate_images=True)
        doc_out.close()
        if len(buf.getvalue()) <= target_bytes:
            return buf.getvalue(), "整页转图（文字层丢失）"
        ratio *= 0.72
    return buf.getvalue(), "整页转图（已尽力，仍超目标）"


def image_to_pdf_bytes(im, target_bytes, cancel=None):
    """单张图片包成 A4 PDF，图片先压到目标体积"""
    data, scale, info, used = best_encode(im, int(target_bytes * 0.92), "jpg", cancel)
    if data is None:
        return None, None
    with Image.open(io.BytesIO(data)) as probe_img:
        w, h = probe_img.size
    doc = pymupdf.open()
    wpt, hpt = 595.0, 842.0
    margin = 24.0
    k = min((wpt - 2 * margin) / w, (hpt - 2 * margin) / h)
    dw, dh = w * k, h * k
    x0, y0 = (wpt - dw) / 2, (hpt - dh) / 2
    page = doc.new_page(width=wpt, height=hpt)
    page.insert_image(pymupdf.Rect(x0, y0, x0 + dw, y0 + dh), stream=data)
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True, deflate_images=True)
    doc.close()
    return buf.getvalue(), info


# ---------------------------------------------------------------- 主流程

def compress_one(src, out_dir, target_bytes, fmt,
                 merge_pages=True, log=None, cancel=None, progress=None):
    """
    处理单个文件，返回 [Result(path, size, fmt)]
    fmt: 'jpg' / 'png' / 'pdf' / 'auto'
    merge_pages: 多页 PDF 输出图片时，True 拼成一张，False 逐页输出
    """
    def say(msg):
        if log:
            log(msg)

    ext = os.path.splitext(src)[1].lower()
    stem = os.path.splitext(os.path.basename(src))[0]
    results = []

    # ---- PDF 进 PDF 出
    if ext == ".pdf" and fmt == "pdf":
        data, how = compress_pdf_to_pdf(src, target_bytes, cancel)
        dst = os.path.join(out_dir, stem + "_压缩.pdf")
        with open(dst, "wb") as f:
            f.write(data)
        say("%s ← %s（%s）" % ("%.0fKB" % (len(data) / 1024), os.path.basename(src), how))
        return [Result(dst, len(data), "pdf")]

    pages = load_pages(src, cancel=cancel)
    is_pdf = (ext == ".pdf")

    # ---- 多页转图片
    if fmt in ("jpg", "png", "auto"):
        if len(pages) > 1 and merge_pages:
            canvas = stitch(pages)
            data, scale, info, used = best_encode(canvas, target_bytes, fmt,
                                                  cancel, progress)
            if data is None:
                raise RuntimeError("压不到目标体积，试试放宽限制")
            dst = os.path.join(out_dir, "%s_%d页.%s" % (stem, len(pages), used))
            with open(dst, "wb") as f:
                f.write(data)
            say("拼接 %d 页 → %s（缩放%.0f%%，%s）"
                % (len(pages), os.path.basename(dst), scale * 100, info))
            return [Result(dst, len(data), used)]

        for i, pg in enumerate(pages, 1):
            _tick(cancel)
            data, scale, info, used = best_encode(pg, target_bytes, fmt,
                                                  cancel, progress)
            if data is None:
                raise RuntimeError("第%d页压不到目标体积" % i)
            suffix = "" if len(pages) == 1 else "_p%d" % i
            dst = os.path.join(out_dir, stem + suffix + "." + used)
            with open(dst, "wb") as f:
                f.write(data)
            say("%s（缩放%.0f%%，%s）" % (os.path.basename(dst), scale * 100, info))
            results.append(Result(dst, len(data), used))
        return results

    # ---- 输出 PDF
    if fmt == "pdf":
        if len(pages) == 1:
            data, info = image_to_pdf_bytes(pages[0], target_bytes, cancel)
            if data is None:
                raise RuntimeError("压不到目标体积")
            dst = os.path.join(out_dir, stem + ".pdf")
            with open(dst, "wb") as f:
                f.write(data)
            say("%s（%s）" % (os.path.basename(dst), info))
            return [Result(dst, len(data), "pdf")]

        doc = pymupdf.open()
        per_page = max(20000, int(target_bytes / len(pages) * 0.9))
        for pg in pages:
            _tick(cancel)
            data, _, _, _ = best_encode(pg, per_page, "jpg", cancel)
            if data is None:
                raise RuntimeError("压不到目标体积")
            im = Image.open(io.BytesIO(data))
            wpt = 595.0
            hpt = wpt * im.height / im.width
            p = doc.new_page(width=wpt, height=hpt)
            p.insert_image(pymupdf.Rect(0, 0, wpt, hpt), stream=data)
        buf = io.BytesIO()
        doc.save(buf, garbage=4, deflate=True)
        doc.close()
        dst = os.path.join(out_dir, stem + ".pdf")
        with open(dst, "wb") as f:
            f.write(buf.getvalue())
        say(os.path.basename(dst))
        return [Result(dst, len(buf.getvalue()), "pdf")]

    raise ValueError("不支持的输出格式：" + fmt)


def preview_thumb(path, max_side=520, cancel=None):
    """给预览面板用的缩略图"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        doc = pymupdf.open(path)
        zoom = 120 / 72.0
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                colorspace=pymupdf.csRGB)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
    else:
        with Image.open(path) as src:
            src.seek(0)
            im = src.convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    return im
