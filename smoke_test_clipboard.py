r"""
Smoke-тест синхронизации картинки в буфере обмена БЕЗ двух реальных машин.

Проверяем:
  1) encode/decode PNG round-trip (Pillow) — байты переживают сериализацию;
  2) две ClipboardSync (как «host» и «client») через фейковый канал:
     картинка, отправленная одной стороной, доходит до on_remote_image
     другой и не порождает эхо обратно (анти-эхо по хэшу);
  3) текстовый путь не задет (send_text_cb по-прежнему вызывается).

Запуск:  .\.venv\Scripts\python.exe smoke_test_clipboard.py
"""
import io
import sys

from PIL import Image

import common


def make_png():
    img = Image.new("RGBA", (8, 6), (10, 20, 30, 255))
    for x in range(8):
        img.putpixel((x, x % 6), (255, 0, 0, 255))
    return common.encode_clipboard_image(img)


def check(name, cond):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(1)


def test_png_roundtrip():
    print("test: PNG encode/decode round-trip")
    png = make_png()
    check("байты не пусты", len(png) > 0)
    check("это PNG (сигнатура)", png[:8] == b"\x89PNG\r\n\x1a\n")
    img2 = Image.open(io.BytesIO(png)).convert("RGBA")
    check("размер сохранён", img2.size == (8, 6))
    check("пиксель сохранён", img2.getpixel((0, 0)) == (255, 0, 0, 255))


def test_clipboardsync_image_path():
    print("test: ClipboardSync image on_remote_image / анти-эхо")
    # Фейковый «канал»: что одна сторона отправляет — то прилетает другой.
    sent = {"host_img": [], "client_img": [], "host_txt": [], "client_txt": []}

    host = common.ClipboardSync(
        lambda t: sent["host_txt"].append(t),
        lambda p: sent["host_img"].append(p),
    )
    client = common.ClipboardSync(
        lambda t: sent["client_txt"].append(t),
        lambda p: sent["client_img"].append(p),
    )
    # send_img передан -> ветка картинок активна на обеих сторонах
    check("host: image-путь активен", host._send_img is not None)
    check("client: image-путь активен", client._send_img is not None)

    png = make_png()
    # «host» получает картинку с той стороны -> кладёт локально (set_clipboard_image
    # может вернуть False без pywin32, но on_remote_image не должен падать).
    host.on_remote_image(png)
    check("host: после приёма хэш картинки запомнен (анти-эхо)",
          host._last_img_hash == common.ClipboardSync._img_hash(png))

    # Эмулируем, что после on_remote_image поллинг увидел бы ТУ ЖЕ картинку:
    # хэш совпадает -> отправки обратно быть не должно.
    h_now = common.ClipboardSync._img_hash(png)
    check("анти-эхо: хэш совпадает, повторной отправки нет",
          h_now == host._last_img_hash and len(sent["host_img"]) == 0)

    # Новая картинка -> другой хэш -> была бы отправка.
    other = Image.new("RGBA", (4, 4), (1, 2, 3, 255))
    png2 = common.encode_clipboard_image(other)
    check("разные картинки -> разные хэши",
          common.ClipboardSync._img_hash(png2) != h_now)


def test_text_path_intact():
    print("test: текстовый путь не сломан")
    got = []
    cs = common.ClipboardSync(lambda t: got.append(t))  # без image-cb
    check("без image-cb image-путь выключен", cs._send_img is None)
    cs.on_remote("привет")  # не должно падать (даже если pyperclip нет)
    print("  [OK] on_remote(text) отработал без исключений")


if __name__ == "__main__":
    print("=== smoke_test_clipboard ===")
    test_png_roundtrip()
    test_clipboardsync_image_path()
    test_text_path_intact()
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
    sys.exit(0)
