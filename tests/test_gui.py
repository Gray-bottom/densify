# -*- coding: utf-8 -*-
"""
界面层验收测试

会真的创建窗口，跑完自动关闭。直接运行：
    python tests/test_gui.py
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui
from PIL import Image, ImageDraw

# 把配置重定向到临时目录，避免污染用户的 config.json
_TMP_CONF = tempfile.mkdtemp(prefix="densify_gui_")
gui.CONF_PATH = os.path.join(_TMP_CONF, "config.json")

# 屏蔽弹窗（askyesno 需要可控，用于验证覆盖提示）
ASK_LOG = []
ASK_REPLY = {"v": True}


def _fake_ask(title, msg, **kw):
    ASK_LOG.append((title, msg))
    return ASK_REPLY["v"]


gui.messagebox.askyesno = _fake_ask
gui.messagebox.showwarning = lambda *a, **k: None
gui.messagebox.showerror = lambda *a, **k: None
gui.messagebox.showinfo = lambda *a, **k: None


def make_image(path, size=(800, 1000)):
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    for i in range(20):
        d.text((30, 30 + i * 40), "line %d" % i, fill="black")
    im.save(path)
    return path


class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui.App()
        cls.app.update_idletasks()
        cls.app.update()
        if hasattr(cls.app, "welcome"):
            cls.app._close_welcome()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        ASK_LOG.clear()
        ASK_REPLY["v"] = True
        # 等上一轮跑完，否则 on_clear 会被 running 挡住，污染下一个用例
        deadline = time.time() + 60
        while self.app.running and time.time() < deadline:
            self.app.update()
            time.sleep(0.03)
        self.app.on_clear()
        self.tmp = tempfile.mkdtemp(prefix="densify_case_")
        self.img = make_image(os.path.join(self.tmp, "a.png"))

    # ---------- 文件管理
    def test_add_file(self):
        self.assertTrue(self.app._add_path(self.img))
        self.assertEqual(len(self.app.items), 1)

    def test_add_duplicate_ignored(self):
        self.assertTrue(self.app._add_path(self.img))
        self.assertFalse(self.app._add_path(self.img), "同一文件不应重复加入")
        self.assertEqual(len(self.app.items), 1)

    def test_add_unsupported_ext(self):
        p = os.path.join(self.tmp, "x.docx")
        open(p, "w").close()
        self.assertFalse(self.app._add_path(p))

    def test_remove_selected(self):
        self.app._add_path(self.img)
        key = list(self.app.items)[0]
        self.app.tree.selection_set(key)
        self.app.on_remove()
        self.assertEqual(len(self.app.items), 0)

    def test_clear(self):
        self.app._add_path(self.img)
        self.app.on_clear()
        self.assertEqual(len(self.app.items), 0)
        self.assertEqual(len(self.app.tree.get_children()), 0)

    # ---------- 目标大小
    def test_target_parsing(self):
        self.app.ent_size.delete(0, "end")
        self.app.ent_size.insert(0, "200")
        self.app.cmb_unit.set("KB")
        self.assertEqual(self.app._read_target(), 200_000)

    def test_target_mb(self):
        self.app.ent_size.delete(0, "end")
        self.app.ent_size.insert(0, "1.5")
        self.app.cmb_unit.set("MB")
        self.assertEqual(self.app._read_target(), 1_500_000)

    def test_target_invalid(self):
        self.app.ent_size.delete(0, "end")
        self.app.ent_size.insert(0, "abc")
        with self.assertRaises(ValueError):
            self.app._read_target()

    def test_row_shows_actual_target(self):
        self.app._add_path(self.img)
        key = list(self.app.items)[0]
        values = self.app._row_values(key)
        # 第 4 列是目标，应显示实际数值而不是「默认」
        self.assertIn("KB", values[3])

    def test_per_file_target_override(self):
        self.app._add_path(self.img)
        key = list(self.app.items)[0]
        self.app.items[key]["target"] = 300_000
        self.app._refresh_row(key)
        values = self.app._row_values(key)
        self.assertIn("300", values[3])

    # ---------- 运行
    def test_run_without_files_does_nothing(self):
        self.app.on_run()
        self.assertFalse(self.app.running)

    def test_run_and_wait(self):
        out = os.path.join(self.tmp, "out")
        self.app._add_path(self.img)
        self.app.ent_size.delete(0, "end")
        self.app.ent_size.insert(0, "150")
        self.app.cmb_unit.set("KB")
        self.app.cmb_fmt.set("jpg")
        self.app.ent_out.delete(0, "end")
        self.app.ent_out.insert(0, out)
        self.app.on_run()

        deadline = time.time() + 60
        while self.app.running and time.time() < deadline:
            self.app.update()
            time.sleep(0.03)
        self.assertFalse(self.app.running, "压缩应在超时前结束")
        files = os.listdir(out)
        self.assertEqual(len(files), 1)
        self.assertLessEqual(os.path.getsize(os.path.join(out, files[0])), 150_000)

    def test_cancel(self):
        out = os.path.join(self.tmp, "out2")
        for i in range(3):
            self.app._add_path(make_image(os.path.join(self.tmp, "b%d.png" % i), (1400, 1800)))
        self.app.cmb_fmt.set("png")       # png 慢一点，便于中途取消
        self.app.ent_out.delete(0, "end")
        self.app.ent_out.insert(0, out)
        self.app.on_run()
        self.app.update()
        self.app.on_cancel()
        deadline = time.time() + 60
        while self.app.running and time.time() < deadline:
            self.app.update()
            time.sleep(0.03)
        self.assertFalse(self.app.running, "取消后应尽快停止")

    # ---------- 配置
    def test_conf_roundtrip(self):
        self.app.ent_size.delete(0, "end")
        self.app.ent_size.insert(0, "333")
        self.app.cmb_unit.set("KB")
        self.app.cmb_fmt.set("png")
        self.app._save_conf()
        self.app._load_conf()
        self.assertEqual(self.app.ent_size.get(), "333")
        self.assertEqual(self.app.cmb_fmt.get(), "png")

    def test_welcome_flag_persisted(self):
        self.app._show_welcome()
        self.app._close_welcome()
        self.assertTrue(self.app._conf.get("seen_welcome"))

    # ---------- 覆盖保护
    def _run_with_dir(self, src, out_dir, fmt="jpg", target="50"):
        ASK_LOG.clear()
        self.app.on_clear()
        self.app._add_path(src)
        self.app.ent_size.delete(0, "end")
        self.app.ent_size.insert(0, target)
        self.app.cmb_unit.set("KB")
        self.app.cmb_fmt.set(fmt)
        self.app.ent_out.delete(0, "end")
        self.app.ent_out.insert(0, out_dir)
        self.app.on_run()
        # 真的开始跑了就等它结束，避免污染后续用例
        deadline = time.time() + 90
        while self.app.running and time.time() < deadline:
            self.app.update()
            time.sleep(0.03)

    def test_overwrite_warns_when_output_replaces_source(self):
        """输出目录 = 源目录、扩展名相同：必须提示会替换原文件"""
        ASK_REPLY["v"] = False          # 拒绝，源文件应保持不动
        p = make_image(os.path.join(self.tmp, "photo.jpg"))
        before = os.path.getsize(p)
        self._run_with_dir(p, self.tmp, "jpg")
        titles = [t for t, _ in ASK_LOG]
        self.assertIn("确认覆盖", titles, "同目录同扩展名时必须弹覆盖确认")
        msg = dict(ASK_LOG).get("确认覆盖", "")
        self.assertIn("photo.jpg", msg)
        self.assertEqual(os.path.getsize(p), before, "拒绝后源文件不能变")

    def test_no_warn_when_extension_differs(self):
        """jpeg 压成 jpg 是同目录但不同名，不该报「替换原文件」"""
        ASK_REPLY["v"] = True
        p = make_image(os.path.join(self.tmp, "photo.jpeg"))
        self._run_with_dir(p, self.tmp, "jpg")
        for _, msg in ASK_LOG:
            self.assertNotIn("原文件无法恢复", msg)

    def test_no_warn_for_pdf_to_pdf_same_dir(self):
        """PDF→PDF 输出带 _压缩 后缀，不会覆盖源文件"""
        ASK_REPLY["v"] = True
        try:
            import pymupdf
        except ImportError:
            self.skipTest("没有 pymupdf")
        p = os.path.join(self.tmp, "doc.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "hello")
        doc.save(p)
        doc.close()
        self._run_with_dir(p, self.tmp, "pdf", target="400")
        for _, msg in ASK_LOG:
            self.assertNotIn("原文件无法恢复", msg)

    def test_no_warn_when_output_dir_differs(self):
        """输出到别的目录，怎么都不会碰源文件"""
        ASK_REPLY["v"] = True
        out = os.path.join(self.tmp, "elsewhere")
        os.makedirs(out, exist_ok=True)
        self._run_with_dir(self.img, out, "jpg")
        for _, msg in ASK_LOG:
            self.assertNotIn("原文件无法恢复", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
