"""
Безголовый smoke-тест аудио-захвата (audio.py).

Проверяет:
1. Импорт audio.py без ошибок.
2. LoopbackAudioTrack конструируется (available=True если есть устройство,
   False на headless — оба варианта допустимы).
3. recv() возвращает корректный av.AudioFrame (48 kHz, s16, stereo).
4. Если устройство доступно — кадры приходят без исключений.
   Если нет — тишина, тоже без исключений.
5. stop() корректно завершает трек.

Тест ВСЕГДА exit 0 независимо от наличия аудиоустройства.
"""

import asyncio
import sys

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def main():
    # 1. Импорт
    import_ok = False
    try:
        import audio
        import_ok = True
    except Exception as e:
        print(f"  import error: {e}")
    check("import audio", import_ok)
    if not import_ok:
        print(f"\nИТОГ (audio): 0/1 проверок пройдено")
        sys.exit(1)

    # 2. Конструктор
    track = None
    construct_ok = False
    try:
        track = audio.LoopbackAudioTrack()
        construct_ok = True
    except Exception as e:
        print(f"  construct error: {e}")
    check("LoopbackAudioTrack() конструируется", construct_ok)
    if not construct_ok:
        print(f"\nИТОГ (audio): {sum(ok)}/{len(ok)} проверок пройдено")
        sys.exit(1)

    print(f"      available = {track.available}")

    # 3. recv() отдаёт корректные кадры
    async def test_recv():
        frames = []
        for _ in range(5):
            f = await track.recv()
            frames.append(f)
        return frames

    recv_ok = False
    frames = []
    try:
        frames = asyncio.run(test_recv())
        recv_ok = len(frames) == 5
    except Exception as e:
        print(f"  recv error: {e}")
    check("recv() возвращает 5 кадров без ошибок", recv_ok)

    # 4. Формат кадров
    format_ok = False
    if frames:
        f = frames[0]
        sr_ok = f.sample_rate == 48000
        fmt_ok = f.format.name == "s16"
        pts_ok = f.pts is not None
        format_ok = sr_ok and fmt_ok and pts_ok
        print(f"      sample_rate={f.sample_rate}, format={f.format.name}, "
              f"pts={f.pts}, samples={f.samples}")
    check("формат кадра: s16, 48000 Hz, pts задан", format_ok)

    # 5. PTS монотонно растёт
    pts_ok = False
    if len(frames) >= 2:
        pts_ok = all(frames[i + 1].pts > frames[i].pts for i in range(len(frames) - 1))
    check("PTS монотонно растёт", pts_ok)

    # 6. stop() без ошибок
    stop_ok = False
    try:
        track.stop()
        stop_ok = True
    except Exception as e:
        print(f"  stop error: {e}")
    check("stop() завершается корректно", stop_ok)

    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nИТОГ (audio): {passed}/{total} проверок пройдено")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
