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
# Windows: d3d11va/dxva2 — это hwaccel поверх софт-декодера h264; пробуем их
# через отдельный механизм (см. _open_hwaccel_decoder). Плюс отдельный
# полноценный HW-декодер h264_qsv (Intel) и h264_cuvid (NVIDIA).
_DECODER_PRIORITY = ["h264_cuvid", "h264_qsv", "h264"]
# Аппаратные hwaccel-методы PyAV/FFmpeg (DirectX на Windows).
_HWACCEL_METHODS = ["d3d11va", "dxva2"]
_DECODER_OK = {}  # кэш результатов валидации HW-декодеров (по ключу-имени)

# Множество имён, считающихся аппаратными декодерами (для active_decoder_name).
_HW_DECODER_NAMES = set(["h264_cuvid", "h264_qsv"] +
                        ["h264/" + m for m in _HWACCEL_METHODS])

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

    @property
    def active_encoder_name(self):
        """Имя реально задействованного энкодера (например 'h264_qsv' или 'libx264')."""
        return self.name

    @property
    def is_hardware(self):
        """True, если задействован аппаратный энкодер."""
        return bool(self.name) and self.name != "libx264"

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


def _make_test_packets():
    """Кодирует 2 тестовых кадра libx264 и возвращает список bytes-пакетов."""
    enc = VideoEncoder(160, 128, fps=10)
    pkts = []
    for i in range(2):
        frame = np.full((128, 160, 3), i * 40, dtype=np.uint8)
        pkts.extend(enc.encode(frame) or [])
    enc.close()
    return [data for data, _kf in pkts]


def _open_decoder(name):
    """Открывает декодер по имени.

    Имена вида 'h264/<hwaccel>' (например 'h264/d3d11va') означают софтовый
    декодер h264 с аппаратным hwaccel-устройством DirectX. Остальные имена —
    обычные кодек-контексты (h264, h264_qsv, h264_cuvid).

    Returns: открытый CodecContext.
    """
    if "/" in name:
        codec_name, hwaccel = name.split("/", 1)
        cc = av.codec.context.CodecContext.create(codec_name, "r")
        # PyAV >= 10 поддерживает hwaccel через av.codec.hwaccel.HWAccel.
        try:
            from av.codec.hwaccel import HWAccel
            cc.hwaccel = HWAccel(device_type=hwaccel, allow_software_fallback=True)
        except Exception as e:
            raise RuntimeError(f"hwaccel {hwaccel} недоступен: {e}")
        cc.open()
        return cc
    cc = av.codec.context.CodecContext.create(name, "r")
    cc.open()
    return cc


def _decoder_can_decode(decoder_name: str, test_packets=None) -> bool:
    """Проверяет, что HW-декодер реально декодирует libx264-поток.
    Кодируем 2 тестовых кадра софтовым libx264 и пытаемся декодировать
    указанным декодером (включая hwaccel-варианты 'h264/d3d11va').
    True только если получили хотя бы один кадр И смогли перегнать его в RGB
    ndarray (важно для HW-поверхностей: open() мало, нужна реальная выгрузка).
    Результат кэшируется в _DECODER_OK."""
    if decoder_name in _DECODER_OK:
        return _DECODER_OK[decoder_name]
    ok = False
    try:
        if test_packets is None:
            test_packets = _make_test_packets()
        cc = _open_decoder(decoder_name)
        for data in test_packets:
            for frame in cc.decode(av.packet.Packet(data)):
                # Доводим до RGB ndarray — это и есть настоящая проверка
                # (HW-поверхность должна успешно скопироваться в системную память).
                arr = frame.to_ndarray(format="rgb24")
                if arr is not None and arr.size > 0:
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
    """H.264-декодер с выбором HW/SW и валидацией-с-фолбэком.

    prefer:
      'auto' / None / '' — пробуем аппаратные (cuvid/qsv/d3d11va/dxva2), затем софт;
      'hw'               — то же, но БЕЗ финального софт-фолбэка пропуска валидации
                           (всё равно если ничего не прошло — берём софт, чтобы не падать);
      'sw' / 'software' / 'h264' — сразу софтовый h264, без проб HW;
      <конкретное имя>   — ставим первым в очередь, остальные как фолбэк.
    """

    def __init__(self, prefer="auto"):
        self.name = None
        self.cc = None

        if prefer in ("sw", "software", "h264"):
            order = ["h264"]
        elif prefer in (None, "auto", "", "hw"):
            # HW полноценные декодеры + hwaccel-методы DirectX, софт — последним.
            order = ["h264_cuvid", "h264_qsv"]
            order += ["h264/" + m for m in _HWACCEL_METHODS]
            order += ["h264"]
        else:
            order = [prefer] + [d for d in _DECODER_PRIORITY if d != prefer]

        # Один набор тестовых пакетов на все валидации в этом конструкторе.
        test_packets = None
        for name in order:
            try:
                if name == "h264":
                    # Софтовый — не требует валидации, открываем напрямую.
                    self.cc = _open_decoder("h264")
                    self.name = "h264"
                    log.info("decoder selected: h264 (software)")
                    break
                # HW-декодер (cuvid/qsv/d3d11va/dxva2) может «открыться», но не
                # уметь декодировать реальный поток (Intel HD 4600: qsv
                # открывается, но libx264-поток не тянет -> 0 кадров). Поэтому
                # валидируем настоящим тестовым кадром, а не только open().
                if test_packets is None:
                    test_packets = _make_test_packets()
                if not _decoder_can_decode(name, test_packets):
                    log.debug("decoder %s opened but failed test decode", name)
                    continue
                self.cc = _open_decoder(name)
                self.name = name
                log.info("decoder selected: %s (HW)", name)
                break
            except Exception as e:
                log.debug("decoder %s failed: %s", name, e)
                continue

        if self.cc is None:
            # Аварийный фолбэк — стандартный софтовый декодер.
            self.cc = av.codec.context.CodecContext.create("h264", "r")
            self.cc.open()
            self.name = "h264"
            log.info("decoder fallback: h264 (software)")

    @property
    def active_decoder_name(self):
        """Имя реально задействованного декодера ('h264', 'h264_qsv', 'h264/d3d11va'…)."""
        return self.name

    @property
    def is_hardware(self):
        """True, если задействован аппаратный декодер/hwaccel."""
        return self.name in _HW_DECODER_NAMES

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
