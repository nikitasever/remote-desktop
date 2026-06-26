"""
Захват системного звука (WASAPI loopback) и AudioStreamTrack для aiortc.

Использует PyAudioWPatch для доступа к WASAPI loopback-устройствам на Windows.
aiortc сам кодирует аудио в Opus перед отправкой — трек отдаёт «сырые» PCM-
кадры av.AudioFrame (s16, 48 kHz, стерео).

Если аудиоустройство недоступно (headless, нет динамиков), трек отдаёт тишину
вместо ошибки — graceful degradation.
"""

import asyncio
import fractions
import logging
import threading
import time
from collections import deque

import av
import numpy as np
from aiortc.mediastreams import AudioStreamTrack

LOG = logging.getLogger(__name__)

# Opus в aiortc ожидает 48 kHz; кадр = 960 сэмплов (20 мс).
SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_SAMPLES = 960          # 20 ms @ 48 kHz — стандартный Opus-фрейм
FRAME_DURATION = FRAME_SAMPLES / SAMPLE_RATE
_TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)

# Максимум буферизованных кадров (~200 мс).  Старые отбрасываются.
_MAX_QUEUE = 10


def _find_loopback_device():
    """Находит WASAPI-loopback устройство.  Возвращает (device_info, PyAudio)
    или (None, None) если ничего нет."""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        LOG.warning("pyaudiowpatch не установлен — аудио отключено")
        return None, None

    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        LOG.warning("WASAPI API не найден — аудио отключено")
        p.terminate()
        return None, None

    loopback = None
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice") and dev["hostApi"] == wasapi["index"]:
            loopback = dev
            break

    if loopback is None:
        LOG.warning("WASAPI loopback-устройство не найдено — аудио отключено")
        p.terminate()
        return None, None

    return loopback, p


def _resample_if_needed(data, src_rate, src_channels):
    """Ресэмплинг в 48 kHz стерео (s16) через av, если частота/каналы отличаются."""
    if src_rate == SAMPLE_RATE and src_channels == CHANNELS:
        return data
    # Простой ресэмплинг через numpy: nearest-neighbour (достаточно для loopback,
    # где частота обычно 48 kHz).  Полноценный resampler можно добавить позже.
    samples = len(data) // src_channels
    arr = data.reshape(samples, src_channels)

    # Channels: mono->stereo / stereo->mono
    if src_channels == 1 and CHANNELS == 2:
        arr = np.column_stack([arr, arr])
    elif src_channels > CHANNELS:
        arr = arr[:, :CHANNELS]

    # Rate
    if src_rate != SAMPLE_RATE:
        target_len = int(samples * SAMPLE_RATE / src_rate)
        indices = np.round(np.linspace(0, samples - 1, target_len)).astype(int)
        arr = arr[indices]

    return arr


class LoopbackAudioTrack(AudioStreamTrack):
    """AudioStreamTrack, отдающий системный звук через WASAPI loopback.

    Если устройство недоступно, ``self.available`` = False и recv() отдаёт
    тишину — aiortc всё равно кодирует и шлёт пустые Opus-фреймы (клиент
    слышит тишину, без ошибок).
    """

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue: deque = deque(maxlen=_MAX_QUEUE)
        self._pts = 0
        self._started = False
        self._pa = None
        self._stream = None
        self._dev_rate = SAMPLE_RATE
        self._dev_channels = CHANNELS
        self.available = False

        loopback, pa = _find_loopback_device()
        if loopback is None:
            return

        self._pa = pa
        self._dev_rate = int(loopback["defaultSampleRate"])
        self._dev_channels = int(loopback["maxInputChannels"])

        try:
            import pyaudiowpatch as pyaudio
            # Размер буфера подобран под ~20 мс на родной частоте устройства.
            dev_frame = max(256, int(self._dev_rate * FRAME_DURATION))
            self._stream = pa.open(
                format=pyaudio.paFloat32,
                channels=self._dev_channels,
                rate=self._dev_rate,
                input=True,
                input_device_index=int(loopback["index"]),
                frames_per_buffer=dev_frame,
                stream_callback=self._callback,
            )
            self._stream.start_stream()
            self.available = True
            LOG.info("WASAPI loopback запущен: %s @ %d Hz, %d ch",
                     loopback["name"], self._dev_rate, self._dev_channels)
        except Exception as exc:
            LOG.warning("Не удалось открыть loopback-поток: %s", exc)
            self._cleanup_pa()

    # -- PyAudio callback (вызывается из аудио-потока PortAudio) -------------

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio
        try:
            raw = np.frombuffer(in_data, dtype=np.float32)
            # float32 → int16 для av.AudioFrame (формат s16)
            pcm = np.clip(raw, -1.0, 1.0)
            pcm_s16 = (pcm * 32767).astype(np.int16)
            arr = _resample_if_needed(pcm_s16, self._dev_rate, self._dev_channels)

            # Нарезаем на кадры по FRAME_SAMPLES
            total = len(arr) // CHANNELS
            pos = 0
            while total - pos >= FRAME_SAMPLES:
                chunk = arr[pos:pos + FRAME_SAMPLES]
                self._queue.append(chunk)
                pos += FRAME_SAMPLES
        except Exception:
            pass
        return (None, pyaudio.paContinue)

    # -- aiortc recv ---------------------------------------------------------

    async def recv(self):
        # Пейсинг: один кадр каждые 20 мс (как реальное время).
        if self._started:
            await asyncio.sleep(FRAME_DURATION)
        self._started = True

        if self._queue:
            pcm = self._queue.popleft()
        else:
            # Тишина, если нет данных (нет звука или устройство недоступно).
            pcm = np.zeros((FRAME_SAMPLES, CHANNELS), dtype=np.int16)

        # s16 — packed (interleaved) формат: shape = (1, samples * channels)
        frame = av.AudioFrame.from_ndarray(
            pcm.flatten().reshape(1, -1),
            format="s16",
            layout="stereo",
        )
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = _TIME_BASE
        self._pts += FRAME_SAMPLES
        return frame

    # -- Cleanup -------------------------------------------------------------

    def _cleanup_pa(self):
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def stop(self):
        """Вызывается aiortc при закрытии трека."""
        self._cleanup_pa()
        super().stop()
