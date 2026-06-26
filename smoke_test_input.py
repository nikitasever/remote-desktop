"""
Регресс-тест маппинга клавиш в host.InputInjector (без GUI, ввод не инжектится).

Главное: при зажатом Ctrl/Alt/Win печатная клавиша должна слаться как
ВИРТУАЛЬНАЯ (KeyCode.from_vk), иначе на не-латинской раскладке pynput
вводит Unicode-символ и сочетания (Ctrl+C/Ctrl+A) не срабатывают.
"""
import host

ok = []
def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def main():
    inj = host.InputInjector(1, 1)

    # Без модификаторов: обычная буква -> from_char (Unicode-ввод, печатает символ)
    inj._mods_down = set()
    k = inj._key_from_event({"char": "c"})
    check("без мода: 'c' идёт как символ (vk=None)", getattr(k, "vk", None) is None and k.char == "c")

    # Зажат Ctrl: буква -> виртуальная клавиша C (0x43), независимо от раскладки
    inj._mods_down = {"ctrl"}
    k = inj._key_from_event({"char": "c"})
    check("Ctrl+'c': идёт как VK C (0x43)", getattr(k, "vk", None) == ord("C"))

    # Зажат Alt: цифра -> VK '1' (0x31)
    inj._mods_down = {"alt"}
    k = inj._key_from_event({"char": "1"})
    check("Alt+'1': идёт как VK '1' (0x31)", getattr(k, "vk", None) == ord("1"))

    # Спец-клавиша по имени не зависит от модификаторов
    inj._mods_down = {"ctrl"}
    k = inj._key_from_event({"name": "enter"})
    check("name=enter -> Key.enter", k == host.Key.enter)

    # Shift НЕ трекается как мод -> буква остаётся символом (заглавная печатается Unicode'ом верно)
    inj._mods_down = set()  # shift не добавляется в _mods_down в handle()
    k = inj._key_from_event({"char": "C"})
    check("Shift+'C' остаётся символом (vk=None)", getattr(k, "vk", None) is None and k.char == "C")

    # handle() должен наполнять _mods_down при kdown name=ctrl и очищать при kup
    inj2 = host.InputInjector(1, 1)
    # перехватываем нажатия, чтобы не трогать реальную клавиатуру
    sent = []
    inj2.kb.press = lambda key: sent.append(("press", key))
    inj2.kb.release = lambda key: sent.append(("release", key))
    inj2.handle({"k": "kdown", "name": "ctrl"})
    check("после kdown ctrl: мод зажат", "ctrl" in inj2._mods_down)
    inj2.handle({"k": "kdown", "char": "c"})
    check("Ctrl зажат -> 'c' ушла как VK C", any(op == "press" and getattr(key, "vk", None) == ord("C") for op, key in sent))
    inj2.handle({"k": "kup", "name": "ctrl"})
    check("после kup ctrl: мод отпущен", "ctrl" not in inj2._mods_down)

    total, passed = len(ok), sum(1 for x in ok if x)
    print(f"\nИТОГ (input mapping): {passed}/{total} проверок пройдено")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
