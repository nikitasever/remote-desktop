"""
Безголовый smoke-тест аудио для TCP-пути (host.py/client.py).

Проверяет:
1. Импорт audio.py.
2. Opus encode -> frame-bytes -> decode round-trip (без реального устройства):
   синтезируем PCM-кадр (синус), кодируем OpusEncoder, декодируем OpusDecoder,
   убеждаемся что на выходе int16-стерео нужной формы.
3. Протокольные байты MSG_AUDIO_INFO/MSG_AUDIO заданы и не пересекаются с
   остальными типами сообщений.
4. Graceful no-op: AudioPlayer и LoopbackCapture конструируются даже без
   устройства/PyAudio (available может быть False — это допустимо), и feed()
   на недоступном плеере не падает.

Тест НЕ требует реального аудиоустройства и playback-железа.
Выход 0 при успехе.
"""

import sys

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def main():
    # 1. Импорт
    try:
        import audio
        import common
        import numpy as np
        import_ok = True
    except Exception as e:
        print(f"  import error: {e}")
        print("\nИТОГ (audio-tcp): 0/1 проверок пройдено")
        sys.exit(1)
    check("import audio/common/numpy", import_ok)

    # 2. Протокольные байты
    info, au = common.MSG_AUDIO_INFO, common.MSG_AUDIO
    print(f"      MSG_AUDIO_INFO=0x{info:02x}, MSG_AUDIO=0x{au:02x}")
    # Собираем все остальные MSG_* и проверяем отсутствие коллизий
    others = [v for k, v in vars(common).items()
              if k.startswith("MSG_") and k not in ("MSG_AUDIO_INFO", "MSG_AUDIO")
              and isinstance(v, int)]
    check("байты MSG_AUDIO* уникальны (нет коллизий)",
          info not in others and au not in others and info != au)

    # 3. Opus round-trip без устройства
    rt_ok = False
    try:
        enc = audio.OpusEncoder()
        dec = audio.OpusDecoder()
        # Синус 440 Гц, один кадр FRAME_SAMPLES стерео
        t = np.arange(audio.FRAME_SAMPLES) / audio.SAMPLE_RATE
        tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        pcm = np.column_stack([tone, tone])  # (FRAME_SAMPLES, CHANNELS)

        decoded_frames = []
        # Opus может выдать пакет(ы) с задержкой — прогоняем несколько кадров
        for _ in range(5):
            for pkt in enc.encode(pcm):
                assert isinstance(pkt, (bytes, bytearray)) and len(pkt) > 0
                for arr in dec.decode(pkt):
                    decoded_frames.append(arr)
        rt_ok = len(decoded_frames) > 0
        if decoded_frames:
            a = decoded_frames[0]
            print(f"      decoded shape={a.shape}, dtype={a.dtype}")
            rt_ok = a.dtype == np.int16 and a.ndim == 2 and a.shape[1] == audio.CHANNELS
    except Exception as e:
        print(f"  round-trip error: {e}")
    check("Opus encode->decode round-trip (s16 stereo)", rt_ok)

    # 4. Graceful: LoopbackCapture конструируется без падения
    cap_ok = False
    try:
        cap = audio.LoopbackCapture()
        print(f"      LoopbackCapture.available={cap.available}")
        # read() на недоступном — None, без исключения
        if not cap.available:
            cap_ok = cap.read() is None
        else:
            cap_ok = True  # есть устройство — тоже норм
        cap.stop()
    except Exception as e:
        print(f"  capture error: {e}")
    check("LoopbackCapture graceful (нет устройства -> no-op)", cap_ok)

    # 5. Graceful: AudioPlayer конструируется и feed() не падает
    play_ok = False
    try:
        player = audio.AudioPlayer()
        print(f"      AudioPlayer.available={player.available}")
        # feed на недоступном плеере должен тихо игнорироваться
        player.feed(b"\x00\x00")
        player.stop()
        play_ok = True
    except Exception as e:
        print(f"  player error: {e}")
    check("AudioPlayer graceful (feed/stop без падения)", play_ok)

    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nИТОГ (audio-tcp): {passed}/{total} проверок пройдено")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
