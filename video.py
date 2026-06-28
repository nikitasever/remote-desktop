"""
Видео-кодек экрана (H.264) поверх PyAV/FFmpeg.

Энкодер живёт на host, декодер — на client. Вынесены в общий модуль, чтобы
smoke_test мог прогнать roundtrip без сети.

Кодек выбирается автоматически: сначала аппаратные (NVENC/AMF/QSV/VAAPI), при
неудаче открытия — надёжный софтовый libx264 (ultrafast+zerolatency). Так на
машинах без аппаратного энкодера всё работает, а где он есть — задействуется.
"""

import fractions
import logging
import time

import numpy as np
import av

log = logging.getLogger(__name__)

# Доступность модуля целиком (host/client проверяют перед использованием).
AVAILABLE = True

# Приоритет энкодеров: аппаратные сначала, надёжный софт — последним.
# NVENC (NVIDIA) → AMF (AMD) → QSV (Intel) → VAAPI (Linux) → libx264 (софт).
_ENCODER_PRIORITY = ["h264_nvenc", "h264_amf", "h264_qsv", "h264_vaapi", "libx264"]

# Приоритет декодеров: аппаратные сначала, софтовый h264 — последним.
_DECODER_PRIORITY = ["h264_cuvid", "h264_qsv", "h264"]
_DECODER_OK = {}  # кэш результатов валидации HW-декодеров

# Кэш результатов зондирования доступных энкодеров.
_available_encoders_cache = None


def _encoder_options(name):
    if name == "libx264":
        return {"preset": "ultrafast", "tune": "zerolatency", "g": "120", "bf": "0"}
    if name == "h264_nvenc":
        return {"preset": "p1", "tune": "ull", "g": "120", "bf": "0", "delay": "0"}
    if name == "h264_qsv":
        return {"preset": "veryfast", "g": "120", "bf": "0"}
    if name == "h264_amf":
        return {"usage": "ultralowlatency", "g": "120", "bf": "0"}
    if name == "h264_vaapi":
        return {"g": "120", "bf": "0"}
    return {}


def get_available_encoders(width=320, height=240, force_recheck=False):
    """Зондирует какие H.264-энкодеры реально работают на этой машине.

    Пробует открыть каждый энкодер и закодировать один тестовый кадр.
    Результат кэшируется (сбросить: force_recheck=True).

    Returns: list of (encoder_name, encode_time_ms) для рабочих энкодеров.
    """
    global _available_encoders_cache
    if _available_encoders_cache is not None and not force_recheck:
        return list(_available_encoders_cache)

    width = _even(width)
    height = _even(height)
    results = []
    test_frame_rgb = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)

    for name in _ENCODER_PRIORITY:
        try:
            cc = av.codec.context.CodecContext.create(name, "w")
            cc.width = width
            cc.height = height
            cc.pix_fmt = "yuv420p"
            cc.time_base = fractions.Fraction(1, 30)
            cc.bit_rate = 1_000_000
            cc.options = _encoder_options(name)
            cc.open()

            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(test_frame_rgb), format="rgb24")
            frame = frame.reformat(format="yuv420p")
            frame.pts = 0

            t0 = time.perf_counter()
            packets = list(cc.encode(frame))
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            # Закрываем
            try:
                for _ in cc.encode(None):
                    pass
            except Exception:
                pass

            if packets:
                results.append((name, round(elapsed_ms, 2)))
                log.info("encoder probe: %s OK (%.1f ms)", name, elapsed_ms)
            else:
                log.info("encoder probe: %s — открылся, но не отдал пакет", name)
        except Exception as e:
            log.debug("encoder probe: %s — недоступен: %s", name, e)

    _available_encoders_cache = results
    return list(results)


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
        self.init_encode_ms = None  # время кодирования первого кадра (бенчмарк)

        if prefer in (None, "auto", ""):
            order = list(_ENCODER_PRIORITY)
        else:
            # Принудительный выбор — ставим его первым, остальные как фолбэк.
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
                log.info("encoder selected: %s (%dx%d @ %d fps, %d kbps)",
                         name, self.width, self.height, self.fps, self.bitrate // 1000)
                break
            except Exception as e:
                log.debug("encoder %s failed: %s", name, e)
                last_err = e
                continue
        if self.cc is None:
            raise RuntimeError(f"Не удалось открыть H.264-энкодер: {last_err}")

        # Бенчмарк: кодируем один тестовый кадр и логируем время.
        self._benchmark()

    def _benchmark(self):
        """Кодирует один чёрный кадр и логирует время — показывает, работает ли HW."""
        try:
            test = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(test, format="rgb24")
            frame = frame.reformat(format="yuv420p")
            frame.pts = self._pts
            self._pts += 1
            t0 = time.perf_counter()
            pkts = list(self.cc.encode(frame))
            elapsed = (time.perf_counter() - t0) * 1000.0
            self.init_encode_ms = round(elapsed, 1)
            log.info("encoder benchmark: %s — 1 frame in %.1f ms (%d bytes)",
                     self.name, elapsed, sum(len(bytes(p)) for p in pkts))
        except Exception as e:
            log.warning("encoder benchmark failed: %s", e)
        finally:
            # Бенчмарк выше съедает стартовый IDR+SPS/PPS из потока энкодера.
            # Форсируем keyframe, чтобы ПЕРВЫЙ реальный кадр снова нёс заголовки
            # и опорный кадр — иначе декодер на той стороне падает InvalidData.
            self._force_key = True

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


def _decoder_can_decode(decoder_name: str) -> bool:
    """Проверяет, что HW-декодер реально декодирует libx264-поток.
    Кодируем 2 тестовых кадра софтовым libx264 и пытаемся декодировать
    указанным декодером. True только если получили хотя бы один кадр.
    Результат кэшируется в _DECODER_OK."""
    if decoder_name in _DECODER_OK:
        return _DECODER_OK[decoder_name]
    ok = False
    try:
        import numpy as _np
        enc = VideoEncoder(160, 128, fps=10)
        pkts = []
        for i in range(2):
            frame = _np.full((128, 160, 3), i * 40, dtype=_np.uint8)
            pkts.extend(enc.encode(frame) or [])
        enc.close()
        cc = av.codec.context.CodecContext.create(decoder_name, "r")
        cc.open()
        for data, _kf in pkts:
            for _frame in cc.decode(av.packet.Packet(data)):
                ok = True
        try:
            cc.close()
        except Exception:
            pass
    except Exception as e:
        log.debug("decoder %s validation error: %s", decoder_name, e)
        ok = False
    _DECODER_OK[decoder_name] = ok
    return ok


class VideoDecoder:
    def __init__(self, prefer="auto"):
        self.name = None
        self.cc = None

        if prefer in (None, "auto", ""):
            order = list(_DECODER_PRIORITY)
        else:
            order = [prefer] + [d for d in _DECODER_PRIORITY if d != prefer]

        for name in order:
            try:
                cc = av.codec.context.CodecContext.create(name, "r")
                # HW-декодер (cuvid/qsv) может «открыться», но не уметь
                # декодировать реальный поток (Intel HD 4600: qsv открывается,
                # но libx264-поток не тянет -> 0 кадров). Поэтому валидируем
                # настоящим тестовым кадром, а не только open().
                if name != "h264":
                    cc.open()
                    if not _decoder_can_decode(name):
                        log.debug("decoder %s opened but failed test decode", name)
                        try:
                            cc.close()
                        except Exception:
                            pass
                        continue
                self.cc = cc
                self.name = name
                log.info("decoder selected: %s", name)
                break
            except Exception as e:
                log.debug("decoder %s failed: %s", name, e)
                continue

        if self.cc is None:
            # Аварийный фолбэк — стандартный софтовый декодер.
            self.cc = av.codec.context.CodecContext.create("h264", "r")
            self.name = "h264"
            log.info("decoder fallback: h264 (software)")

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
