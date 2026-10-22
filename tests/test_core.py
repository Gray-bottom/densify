# -*- coding: utf-8 -*-
"""
核心压缩逻辑的验收测试

只依赖 core.py，不需要开界面。直接运行：
    python tests/test_core.py
或：
    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf
from PIL import Image, ImageDraw

import core


def make_image(path, size=(1240, 1754), text_lines=40):
    """造一张有文字的测试图，模拟文档页"""
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    for i in range(text_lines):
        d.text((40, 30 + i * 32), "Sample line %03d  0123456789 ABCDEFG" % i, fill="black")
    im.save(path)
    return path


def make_pdf(path, pages=2, size=(1240, 1754)):
    """把几张图打成一个 PDF"""
    doc = pymupdf.open()
    for i in range(pages):
        im = Image.new("RGB", size, "white")
        d = ImageDraw.Draw(im)
        for j in range(30):
            d.text((40, 40 + j * 40), "Page %d line %d" % (i + 1, j), fill="black")
        buf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        im.save(buf.name)
        buf.close()
        rect = pymupdf.Rect(0, 0, 595, 842)
        page = doc.new_page(width=595, height=842)
        page.insert_image(rect, filename=buf.name)
        os.unlink(buf.name)
    doc.save(path)
    doc.close()
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="densify_test_")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestFitImage(Base):
    def test_jpg_within_target(self):
        p = make_image(os.path.join(self.tmp, "a.png"))
        im = Image.open(p).convert("RGB")
        data, scale, info, fmt = core.best_encode(im, 150_000, "jpg")
        self.assertIsNotNone(data)
        self.assertLessEqual(len(data), 150_000)
        self.assertEqual(fmt, "jpg")

    def test_png_within_target(self):
        p = make_image(os.path.join(self.tmp, "a.png"))
        im = Image.open(p).convert("RGB")
        data, scale, info, fmt = core.best_encode(im, 150_000, "png")
        self.assertIsNotNone(data)
        self.assertLessEqual(len(data), 150_000)
        self.assertEqual(fmt, "png")

    def test_auto_picks_a_format(self):
        p = make_image(os.path.join(self.tmp, "a.png"))
        im = Image.open(p).convert("RGB")
        data, scale, info, fmt = core.best_encode(im, 200_000, "auto")
        self.assertIsNotNone(data)
        self.assertIn(fmt, ("jpg", "png"))
        self.assertLessEqual(len(data), 200_000)

    def test_keeps_full_size_when_budget_is_loose(self):
        """预算很宽时不应该缩小图片"""
        p = make_image(os.path.join(self.tmp, "a.png"), size=(600, 400), text_lines=5)
        im = Image.open(p).convert("RGB")
        data, scale, info, fmt = core.best_encode(im, 5_000_000, "jpg")
        self.assertIsNotNone(data)
        self.assertEqual(scale, 1.0)

    def test_extreme_small_target_returns_none_or_tiny(self):
        """目标不可能达成时应明确返回 None，而不是产出超标文件"""
        p = make_image(os.path.join(self.tmp, "a.png"))
        im = Image.open(p).convert("RGB")
        data, scale, info = core.fit_image(im, 300, "png")
        if data is not None:
            self.assertLessEqual(len(data), 300)

    def test_cancel_raises(self):
        p = make_image(os.path.join(self.tmp, "a.png"))
        im = Image.open(p).convert("RGB")
        with self.assertRaises(core.Cancelled):
            core.fit_image(im, 150_000, "jpg", cancel=lambda: True)


class TestCompressOne(Base):
    def test_image_to_jpg(self):
        p = make_image(os.path.join(self.tmp, "a.png"))
        res = core.compress_one(p, self.out, 120_000, "jpg")
        self.assertEqual(len(res), 1)
        self.assertLessEqual(res[0].size, 120_000)
        self.assertTrue(os.path.exists(res[0].path))
        self.assertTrue(res[0].path.endswith(".jpg"))

    def test_image_to_pdf(self):
        p = make_image(os.path.join(self.tmp, "a.png"))
        res = core.compress_one(p, self.out, 200_000, "pdf")
        self.assertLessEqual(res[0].size, 200_000)
        doc = pymupdf.open(res[0].path)
        self.assertEqual(doc.page_count, 1)
        doc.close()

    def test_pdf_merge_pages(self):
        p = make_pdf(os.path.join(self.tmp, "d.pdf"), pages=2)
        res = core.compress_one(p, self.out, 200_000, "jpg", merge_pages=True)
        self.assertEqual(len(res), 1, "多页拼接应只产出一个文件")
        self.assertIn("2页", os.path.basename(res[0].path))
        self.assertLessEqual(res[0].size, 200_000)

    def test_pdf_split_pages(self):
        p = make_pdf(os.path.join(self.tmp, "d.pdf"), pages=2)
        res = core.compress_one(p, self.out, 200_000, "jpg", merge_pages=False)
        self.assertEqual(len(res), 2, "逐页输出应产出两个文件")
        for r in res:
            self.assertLessEqual(r.size, 200_000)
            self.assertIn("_p", os.path.basename(r.path))

    def test_pdf_to_png(self):
        p = make_pdf(os.path.join(self.tmp, "d.pdf"), pages=1)
        res = core.compress_one(p, self.out, 150_000, "png")
        self.assertLessEqual(res[0].size, 150_000)
        self.assertTrue(res[0].path.endswith(".png"))

    def test_pdf_to_pdf(self):
        p = make_pdf(os.path.join(self.tmp, "d.pdf"), pages=2)
        res = core.compress_one(p, self.out, 300_000, "pdf")
        self.assertLessEqual(res[0].size, 300_000)
        doc = pymupdf.open(res[0].path)
        self.assertEqual(doc.page_count, 2)
        doc.close()

    def test_missing_file_raises(self):
        with self.assertRaises(Exception):
            core.compress_one(os.path.join(self.tmp, "nope.png"), self.out, 100_000, "jpg")

    def test_chinese_path(self):
        p = make_image(os.path.join(self.tmp, "中文 文件名.png"))
        res = core.compress_one(p, self.out, 150_000, "jpg")
        self.assertTrue(os.path.exists(res[0].path))
        self.assertIn("中文 文件名", res[0].path)


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="densify_probe_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_probe_image(self):
        p = make_image(os.path.join(self.tmp, "a.png"), size=(800, 600), text_lines=3)
        info, pages = core.probe(p)
        self.assertEqual(pages, 1)
        self.assertIn("800", info)

    def test_probe_pdf(self):
        p = make_pdf(os.path.join(self.tmp, "d.pdf"), pages=3)
        info, pages = core.probe(p)
        self.assertEqual(pages, 3)
        self.assertIn("3", info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
