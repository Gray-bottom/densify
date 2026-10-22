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

# 屏蔽弹窗
gui.messagebox.askyesno = lambda *a, **k: True
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
