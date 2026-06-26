"""
Тест-репродьюсер бага «0 кадров на статичном экране».

Симптом: клиент подключается, RTT в норме, но FPS = 0.0, экран чёрный.
Host-лог показывает успешную инициализацию (dxcam, libx264), но MSG_VIDEO
кадры не приходят к клиенту. Экран на удалённой машине СТАТИЧЕН.

Гипотезы:
  A) dxcam.grab() → None на статике, mss-фолбэк не переотдаёт кадры
  B) dxcam.grab() бросает исключение (сессия RDP, нет монитора) — нет обработки
  C) Encoder буферизует одинаковые кадры и не отдаёт пакеты

Test A: capture() с fake dxcam (grab→None), проверяем переотдачу
Test B: capture() с fake dxcam (grab raises) — нет try/except → крэш serve()
Test C: encoder.encode() для статичных (одинаковых) кадров
Test D: полный E2E через relay
Test E: mss thread-safety (grab из чужого потока)
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
RELAY_PORT = 5817
PW = "capture-test-pw-42"
ROOM = "capturetest"


class TestA_DxcamNoneFallback(unittest.TestCase):
    """dxcam.grab() вечно → None (статичный экран). Фолбэк на mss должен
    переотдавать кадры каждые 0.5с."""

    def _make_streamer_fake_dxcam_none(self):
        import host
        class FakeDxcam:
            def grab(self):
                return None
            def release(self):
                pass
        streamer = host.ScreenStreamer(scale=1.0)
        streamer._dx = FakeDxcam()
        streamer._last_arr = None
        streamer._last_emit = 0.0
        return streamer

    def test_periodic_reemit(self):
        streamer = self._make_streamer_fake_dxcam_none()
        try:
            count = 0
            for _ in range(60):
                if streamer.capture() is not None:
                    count += 1
                time.sleep(0.05)
            print(f"  [A] кадров за 3с (dxcam=None, mss fallback): {count}")
            self.assertGreaterEqual(count, 5,
                f"За 3с при переотдаче 0.5с ожидалось ≥5 кадров, получили {count}")
        finally:
            streamer.close()


class TestB_DxcamGrabRaises(unittest.TestCase):
    """dxcam.grab() бросает исключение (RDP-сессия, нет монитора, device lost).

    В текущем capture() НЕТ try/except вокруг self._dx.grab(). Если grab()
    упадёт, исключение пролетит вверх по стеку в serve() → вся сессия
    падает → клиент получает 0 кадров.

    Это наиболее вероятная причина бага на удалённой машине.
    """

    def test_capture_survives_dxcam_exception(self):
        import host

        call_count = {"n": 0}

        class FaultyDxcam:
            """grab() бросает — как на удалённом ПК без активного монитора."""
            def grab(self):
                call_count["n"] += 1
                raise OSError("DXGI device lost — нет активного монитора")
            def release(self):
                pass

        streamer = host.ScreenStreamer(scale=1.0)
        streamer._dx = FaultyDxcam()
        streamer._last_arr = None
        streamer._last_emit = 0.0

        # capture() ДОЛЖЕН перехватить ошибку grab() и отдать кадр через mss.
        # Текущий код НЕ перехватывает → исключение → AssertionError здесь = баг найден.
        frames = 0
        errors = 0
        for i in range(10):
            try:
                f = streamer.capture()
                if f is not None:
                    frames += 1
            except Exception as e:
                errors += 1
                if errors == 1:
                    print(f"  [B] capture() упал: {type(e).__name__}: {e}")
            time.sleep(0.05)

        print(f"  [B] кадров={frames}, ошибок={errors}, "
              f"grab() вызван {call_count['n']} раз")

        streamer.close()

        # КЛЮЧЕВАЯ ПРОВЕРКА: capture() НЕ должен падать при сбое dxcam.
        # Если errors > 0 — баг: нет обработки исключений от dxcam.
        self.assertEqual(errors, 0,
            f"РЕПРОДЬЮСЕР БАГА: capture() бросил {errors} исключений при "
            f"сбое dxcam.grab(). Нет try/except — serve() падает, "
            f"клиент получает 0 кадров.")
        self.assertGreaterEqual(frames, 1,
            f"capture() вернул 0 кадров при сбое dxcam (ожидался mss-фолбэк)")


class TestC_EncoderStaticFrames(unittest.TestCase):
    """Encoder должен отдавать пакеты даже для одинаковых (статичных) кадров.
    С tune=zerolatency буферизации быть не должно."""

    def test_static_frames_produce_packets(self):
        try:
            import video
        except ImportError:
            self.skipTest("PyAV/video недоступен")

        w, h = 320, 240
        enc = video.VideoEncoder(w, h, fps=30,
            bitrate=video.quality_to_bitrate(70, w, h))
        # Одинаковый кадр (статичный экран)
        static_frame = np.full((h, w, 3), 128, dtype=np.uint8)
        packets = 0
        for _ in range(30):
            for data, is_key in enc.encode(static_frame):
                packets += 1
        enc.close()
        print(f"  [C] пакетов от 30 одинаковых кадров: {packets}")
        self.assertGreaterEqual(packets, 25,
            f"Encoder отдал {packets}/30 пакетов для статичных кадров "
            f"(возможна буферизация)")


class TestD_E2E_StaticScreen(unittest.TestCase):
    """Полный E2E через relay — считает MSG_VIDEO за 8 секунд."""

    def test_video_frames_arrive(self):
        import common
        try:
            import video
        except ImportError:
            self.skipTest("PyAV/video недоступен")

        env = dict(os.environ)
        procs = []

        try:
            relay = subprocess.Popen(
                [PY, "relay.py", "--port", str(RELAY_PORT)],
                cwd=HERE, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            procs.append(relay)
            time.sleep(1.0)

            host_proc = subprocess.Popen(
                [PY, "host.py",
                 "--relay", f"127.0.0.1:{RELAY_PORT}",
                 "--id", ROOM,
                 "--password", PW,
                 "--engine", "x264"],
                cwd=HERE, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            procs.append(host_proc)
            time.sleep(2.0)

            key = common.derive_key(PW)
            chan = common.SecureChannel(key)
            s = socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=5)
            common.relay_register(s, "client", ROOM)
            line = common.relay_read_line(s)
            self.assertIn("paired", line, f"Relay не свёл: {line}")
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            common.send_frame(s, chan, common.MSG_HELLO,
                              json.dumps({"video": True}).encode("utf-8"))

            s.settimeout(10)
            frames = 0
            got_video_info = False
            got_screen_info = False
            deadline = time.time() + 8

            while time.time() < deadline:
                try:
                    mt, body = common.recv_frame(s, chan)
                except (socket.timeout, Exception):
                    break
                if mt == common.MSG_SCREEN_INFO:
                    got_screen_info = True
                    info = common.parse_json(body)
                    print(f"  [D] SCREEN_INFO: {info.get('w')}x{info.get('h')}")
                elif mt == common.MSG_VIDEO_INFO:
                    got_video_info = True
                    vinfo = common.parse_json(body)
                    print(f"  [D] VIDEO_INFO: {vinfo}")
                elif mt == common.MSG_VIDEO:
                    frames += 1

            s.close()
            print(f"  [D] MSG_VIDEO кадров за 8с: {frames}")

            self.assertTrue(got_screen_info, "Не получен SCREEN_INFO")
            self.assertTrue(got_video_info, "Не получен VIDEO_INFO")
            self.assertGreaterEqual(frames, 3,
                f"РЕПРОДЬЮСЕР БАГА: за 8с получено {frames} MSG_VIDEO кадров "
                f"(ожидалось ≥3). Клиент видит FPS 0.0.")

        finally:
            for p in procs:
                try:
                    p.terminate()
                except Exception:
                    pass
            time.sleep(0.5)


class TestE_MssThreadSafety(unittest.TestCase):
    """mss.MSS() из одного потока, grab() из другого."""

    def test_mss_grab_foreign_thread(self):
        import mss as mss_module

        sct = mss_module.MSS()
        mon = sct.monitors[1]
        results = {"frame": None, "error": None}

        def worker():
            try:
                raw = sct.grab(mon)
                results["frame"] = np.frombuffer(raw.rgb, dtype=np.uint8).reshape(
                    raw.height, raw.width, 3)
            except Exception as e:
                results["error"] = str(e)

        # Контроль: основной поток
        raw_main = sct.grab(mon)
        arr_main = np.frombuffer(raw_main.rgb, dtype=np.uint8).reshape(
            raw_main.height, raw_main.width, 3)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        sct.close()

        if results["error"]:
            print(f"  [E] mss.grab() из чужого потока упал: {results['error']}")
            self.fail(f"mss.grab() thread-safety: {results['error']}")

        self.assertIsNotNone(results["frame"])
        main_black = np.all(arr_main == 0)
        worker_black = np.all(results["frame"] == 0)
        print(f"  [E] main чёрный={main_black}, worker чёрный={worker_black}")

        if not main_black:
            self.assertFalse(worker_black,
                "mss.grab() из чужого потока — чёрный кадр (BitBlt DC thread-affinity)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
