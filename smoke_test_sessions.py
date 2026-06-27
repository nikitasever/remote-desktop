"""Smoke-тесты для session_history — CRUD на временном config.json."""

import json
import os
import tempfile
import time

from session_history import SessionHistory, SessionRecord


def test_all():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "config.json")

        # Предзаполняем config с другими ключами
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"address": "192.168.1.1", "password": "secret"}, f)

        h = SessionHistory(config_path=cfg)

        # 1. record_connection — создание
        r = h.record_connection("123456789", name="Рабочий ПК", os="Windows")
        assert r.id == "123456789"
        assert r.name == "Рабочий ПК"
        assert r.connection_count == 1
        assert r.os == "Windows"
        print("[OK] record_connection — create")

        # 2. record_connection — обновление (повторное подключение)
        r2 = h.record_connection("123456789")
        assert r2.connection_count == 2
        assert r2.name == "Рабочий ПК"  # имя сохраняется
        print("[OK] record_connection — update")

        # 3. Авто-имя
        time.sleep(0.01)
        r3 = h.record_connection("987654321")
        assert r3.name == "ПК-321"
        print("[OK] auto-name ПК-XXX")

        # 4. get_recent
        recent = h.get_recent()
        assert len(recent) == 2
        assert recent[0].id == "987654321"  # последний подключённый
        print("[OK] get_recent")

        # 5. toggle_favorite
        fav = h.toggle_favorite("123456789")
        assert fav is True
        favs = h.get_favorites()
        assert len(favs) == 1 and favs[0].id == "123456789"
        print("[OK] toggle_favorite on")

        h.toggle_favorite("123456789")
        assert len(h.get_favorites()) == 0
        print("[OK] toggle_favorite off")

        # 6. rename_session
        h.rename_session("987654321", "Домашний")
        recent = h.get_recent()
        names = {r.id: r.name for r in recent}
        assert names["987654321"] == "Домашний"
        print("[OK] rename_session")

        # 7. remove_session
        h.remove_session("987654321")
        assert len(h.get_recent()) == 1
        print("[OK] remove_session")

        # 8. Проверяем что другие ключи сохранились
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["address"] == "192.168.1.1"
        assert data["password"] == "secret"
        assert "sessions" in data
        print("[OK] other config keys preserved")

        # 9. Перезагрузка из файла
        h2 = SessionHistory(config_path=cfg)
        assert len(h2.get_recent()) == 1
        assert h2.get_recent()[0].id == "123456789"
        print("[OK] reload from file")

    print("\nAll smoke tests passed!")


if __name__ == "__main__":
    test_all()
