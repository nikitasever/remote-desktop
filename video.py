"""
Видео-кодек экрана (H.264) поверх PyAV/FFmpeg.

Энкодер живёт на host, декодер — на client. Вынесены в общий модуль, чтобы
smoke_test мог прогнать roundtrip без сети.

Кодек выбирается автоматически: сначала аппаратные (QSV/NVENC/AMF), при неудаче
открытия — надёжный софтовый libx264 (ultrafast+zerolatency). Так на машинах без
аппаратного энкодера всё работает, а где он есть — задействуется.
"""

import fractions

import numpy as np
import av

# Доступность модуля целиком (host/client проверяют перед использованием).
AVAILABLE = True

# Приоритет: аппаратные сначала, надёжный софт — последним.
_ENCODER_PRIORITY = ["h264_qsv", "h264_nvenc", "h264_amf", "libx264"]


def _encoder_options(name):
    if name == "libx264":
        return {"preset": "ultrafast", "tune": "zerolatency", "g": "120", "bf": "0"}
    if name == "h264_nvenc":
        return {"preset": "p1", "tune": "ull", "g": "120", "bf": "0", "delay": "0"}
    if name == "h264_qsv":
        return {"preset": "veryfast", "g": "120", "bf": "0"}
    if name == "h264_amf":
        return {"usage": "ultralowlatency", "g": "120", "bf": "0"}
    return {}


def quality_to_bitrate(quality: int, w: int, h: int) -> int:
    """GUI-«чёткость» (50..90) + разрешение -> целевой битрейт, бит/с."""
    base = {50: 2.0, 60: 3.0, 70: 5.0, 80: 8.0, 90: 12.0}
    mbps = base.get(int(quality), 5.0)
    # нормируем к площади 1080p — на меньших разрешениях столько не нужно
    factor = max(0.35, (w * h) / (1920 * 1080))
    return int(mbps * 1_000_000 * factor)


def _even(v: int) -> int:
    """yuv420p требует чётных сторон."""
    return v - (v % 2)


class VideoEncoder:
    def __init__(self, width, height, fps=30, bitrate=6_000_000, prefer="auto"):
        self.width = _even(width)
        self.height = _even(height)
        self.fps = max(1, int(fps))
        self.bitrate = int(bitrate)
        self._pts = 0
        self._force_key = False
        self.name = None
        self.cc = None

        if prefer in (None, "auto", ""):
            order = _ENCODER_PRIORITY
        else:
            order = [prefer] + [e for e in _ENCODER_PRIORITY if e != prefer]

        last_err = None
        for name in order:
            try:
                cc = av.codec.context.CodecContext.create(name, "w")
                cc.width = self.width
                cc.height = self.height
                cc.pix_fmt = "yuv420p"
                cc.time_base = fractions.Fraction(1, self.fps)
                cc.bit_rate = self.bitrate
                cc.options = _encoder_options(name)
                cc.open()
                self.cc = cc
                self.name = name
                break
            except Exception as e:
                last_err = e
                continue
        if self.cc is None:
            raise RuntimeError(f"Не удалось открыть H.264-энкодер: {last_err}")

    def force_keyframe(self):
        self._force_key = True

    def encode(self, rgb: np.ndarray):
        """RGB ndarray -> список (bytes, is_keyframe). Обычно 1 пакет на кадр."""
        h, w = rgb.shape[:2]
        if (w, h) != (self.width, self.height):
            rgb = rgb[:self.height, :self.width]
        frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format="rgb24")
        frame = frame.reformat(format="yuv420p")
        frame.pts = self._pts
        self._pts += 1
        if self._force_key:
            try:
                from av.video.frame import PictureType
                frame.pict_type = PictureType.I
            except Exception:
                pass
            self._force_key = False
        out = []
        for pkt in self.cc.encode(frame):
            out.append((bytes(pkt), bool(pkt.is_keyframe)))
        return out

    def close(self):
        try:
            if self.cc:
                for _ in self.cc.encode(None):  # дренаж буфера энкодера
                    pass
        except Exception:
            pass


class VideoDecoder:
    def __init__(self):
        self.cc = av.codec.context.CodecContext.create("h264", "r")

    def decode(self, data: bytes):
        """bytes одного пакета -> список RGB ndarray (0 или 1 кадр)."""
        pkt = av.packet.Packet(data)
        out = []
        for frame in self.cc.decode(pkt):
            out.append(frame.to_ndarray(format="rgb24"))
        return out

    def close(self):
        try:
            self.cc.close()
        except Exception:
            pass
