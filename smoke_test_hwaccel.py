"""
Smoke-тест аппаратного ускорения видео (H.264) — энкодер и декодер.

Проверяет, что:
  A) VideoEncoder сообщает фактический энкодер (active_encoder_name) и HW-флаг;
  B) roundtrip encode→decode даёт кадр правильных размеров (HW или софт — неважно);
  C) VideoDecoder сообщает фактический декодер (active_decoder_name) и HW-флаг,
     при этом HW-декодер ВАЛИДИРУЕТСЯ реальным кадром и тихо откатывается на софт;
  D) принудительный софт (prefer='sw') действительно даёт софтовый декодер;
  E) принудительный софт-энкодер (prefer='libx264') действительно libx264.

На этой машине (Intel HD 4600, qsv не открывается) HW почти наверняка
откатится на софт — это ПРОХОД, а не провал. Тест печатает, что реально
задействовано.
"""

import unittest

import numpy as np


W, H = 320, 240


def _roundtrip(enc, dec, n=3):
    """Кодирует n синтетических кадров и декодирует обратно.
    Returns: список декодированных RGB ndarray."""
    decoded = []
    for i in range(n):
        # Каждый кадр — свой ровный цвет (легко кодируется и проверяется).
        frame = np.full((H, W, 3), (i * 50) % 256, dtype=np.uint8)
        for data, _is_key in enc.encode(frame):
            decoded.extend(dec.decode(data))
    return decoded


class TestHwAccel(unittest.TestCase):

    def test_encoder_reports_active_name(self):
        import video
        enc = video.VideoEncoder(W, H, fps=15)
        try:
            self.assertIsNotNone(enc.active_encoder_name)
            self.assertIn(enc.active_encoder_name, video._ENCODER_PRIORITY)
            tag = "HW" if enc.is_hardware else "CPU"
            print(f"  [A] активный энкодер: {enc.active_encoder_name} ({tag})")
        finally:
            enc.close()

    def test_decoder_reports_active_name(self):
        import video
        dec = video.VideoDecoder(prefer="auto")
        try:
            self.assertIsNotNone(dec.active_decoder_name)
            tag = "HW" if dec.is_hardware else "CPU"
            print(f"  [C] активный декодер: {dec.active_decoder_name} ({tag})")
        finally:
            dec.close()

    def test_roundtrip_auto(self):
        import video
        enc = video.VideoEncoder(W, H, fps=15)
        dec = video.VideoDecoder(prefer="auto")
        try:
            frames = _roundtrip(enc, dec, n=4)
            self.assertGreaterEqual(len(frames), 1,
                "roundtrip auto не выдал ни одного кадра")
            fh, fw = frames[0].shape[:2]
            print(f"  [B] roundtrip auto: декодировано {len(frames)} кадров, "
                  f"размер {fw}x{fh}, enc={enc.active_encoder_name}, "
                  f"dec={dec.active_decoder_name}")
            self.assertEqual((fw, fh), (W, H),
                f"размер декодированного кадра {fw}x{fh} != {W}x{H}")
            self.assertEqual(frames[0].shape[2], 3, "ожидался RGB (3 канала)")
        finally:
            enc.close()
            dec.close()

    def test_force_software_decoder(self):
        import video
        enc = video.VideoEncoder(W, H, fps=15, prefer="libx264")
        dec = video.VideoDecoder(prefer="sw")
        try:
            self.assertEqual(dec.active_decoder_name, "h264",
                "prefer='sw' должен дать софтовый декодер h264")
            self.assertFalse(dec.is_hardware,
                "софтовый декодер не должен помечаться как HW")
            frames = _roundtrip(enc, dec, n=3)
            self.assertGreaterEqual(len(frames), 1,
                "софтовый roundtrip не выдал кадра")
            fh, fw = frames[0].shape[:2]
            self.assertEqual((fw, fh), (W, H))
            print(f"  [D] force-software OK: dec={dec.active_decoder_name}, "
                  f"кадров={len(frames)}, {fw}x{fh}")
        finally:
            enc.close()
            dec.close()

    def test_force_software_encoder(self):
        import video
        enc = video.VideoEncoder(W, H, fps=15, prefer="libx264")
        try:
            self.assertEqual(enc.active_encoder_name, "libx264",
                "prefer='libx264' должен дать софтовый энкодер")
            self.assertFalse(enc.is_hardware)
            print(f"  [E] force-software encoder OK: {enc.active_encoder_name}")
        finally:
            enc.close()

    def test_hw_decoder_validated_or_fallback(self):
        """prefer='hw' либо реально откроет валидный HW-декодер, либо тихо
        откатится на софт — но НИКОГДА не упадёт и всегда декодирует кадр."""
        import video
        enc = video.VideoEncoder(W, H, fps=15)
        dec = video.VideoDecoder(prefer="hw")
        try:
            frames = _roundtrip(enc, dec, n=4)
            self.assertGreaterEqual(len(frames), 1)
            fh, fw = frames[0].shape[:2]
            self.assertEqual((fw, fh), (W, H))
            tag = "HW" if dec.is_hardware else "CPU(fallback)"
            print(f"  [F] prefer='hw' -> {dec.active_decoder_name} ({tag}), "
                  f"кадров={len(frames)}")
        finally:
            enc.close()
            dec.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
