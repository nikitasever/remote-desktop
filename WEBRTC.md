# WebRTC-транспорт (Фаза B) — PoC

Экспериментальный транспорт поверх WebRTC: видео идёт медиа-треком по
UDP/DTLS-SRTP с контролем перегрузки, ввод — по DataChannel. Устойчивее
TCP-пути при потерях/джиттере и умеет проходить через NAT.

Файлы:
- `rtc_common.py` — сигналинг (переиспользует `relay.py` как канал обмена SDP).
- `host_rtc.py` — хост: видео-трек из `host.ScreenStreamer` + control-DataChannel.
- `client_rtc.py` — клиент: приём видео в окно pygame + ввод по DataChannel.
- `smoke_test_rtc.py` — безголовый тест (видео-кадры + ping/pong). **Проверено: 2/2.**

## Запуск локально (одна машина / LAN)

```bash
# 1) сигнальный сервер (он же из Фазы A)
python relay.py --port 5800

# 2) хост (управляемый ПК)
python host_rtc.py --relay 127.0.0.1:5800 --id myroom --fps 30

# 3) клиент (ваш ПК)
python client_rtc.py --relay 127.0.0.1:5800 --id myroom
```

В LAN хватает host-кандидатов ICE — STUN/TURN не нужен.

## Через интернет / за NAT

1. **relay.py на VPS** с публичным IP (он нужен только для обмена SDP, трафик
   идёт P2P). Открыть порт 5800.
2. **STUN** (бесплатный) — для определения внешнего адреса при «обычном» NAT.
   По умолчанию используется `stun:stun.l.google.com:19302`, дополнительная
   настройка не требуется:
   ```bash
   python host_rtc.py   --relay VPS:5800 --id myroom
   python client_rtc.py --relay VPS:5800 --id myroom
   ```
   Можно указать свои серверы (через запятую) или отключить STUN:
   ```bash
   # Свои STUN серверы
   python host_rtc.py --relay VPS:5800 --id myroom \
       --stun "stun:stun1.l.google.com:19302,stun:stun2.l.google.com:19302"
   # Отключить STUN (только host-кандидаты)
   python host_rtc.py --relay VPS:5800 --id myroom --stun ""
   ```
3. **TURN** — для симметричного NAT, когда P2P не строится (трафик пойдёт через
   TURN-сервер). Поднять coturn на VPS:
   ```
   # /etc/turnserver.conf
   listening-port=3478
   tls-listening-port=5349
   fingerprint
   lt-cred-mech
   user=rd:СЕКРЕТ
   realm=ваш.домен
   external-ip=ВНЕШНИЙ_IP
   # Порты для relay (ограничить диапазон для firewall)
   min-port=49152
   max-port=65535
   ```
   Запуск coturn: `turnserver -c /etc/turnserver.conf`

   Указать TURN хосту и клиенту:
   ```bash
   python host_rtc.py --relay VPS:5800 --id myroom \
       --turn turn:VPS:3478 --turn-user rd --turn-pass СЕКРЕТ

   python client_rtc.py --relay VPS:5800 --id myroom \
       --turn turn:VPS:3478 --turn-user rd --turn-pass СЕКРЕТ
   ```
   Или через переменные окружения (удобно для автоматизации):
   ```bash
   export RD_TURN_URL=turn:VPS:3478
   export RD_TURN_USER=rd
   export RD_TURN_PASS=СЕКРЕТ
   python host_rtc.py --relay VPS:5800 --id myroom
   ```

### CLI-флаги ICE

| Флаг           | Env-переменная | Описание |
|----------------|----------------|----------|
| `--stun`       | —              | STUN URL(s), через запятую. По умолчанию `stun:stun.l.google.com:19302`; `""` — отключить |
| `--turn`       | `RD_TURN_URL`  | TURN URL, напр. `turn:vps:3478` |
| `--turn-user`  | `RD_TURN_USER` | TURN username (long-term credentials) |
| `--turn-pass`  | `RD_TURN_PASS` | TURN password |

CLI-флаг имеет приоритет над переменной окружения.

## Тесты

- `smoke_test_rtc.py` — E2E тест WebRTC (relay + host + client, localhost). **2/2.**
- `smoke_test_ice.py` — юнит-тесты ICE-конфигурации (build_ice_config).

## Известные ограничения PoC

- **Кодек софтовый.** aiortc кодирует видео сам (VP8/H.264 на CPU) и НЕ
  использует аппаратный энкодер. На слабом CPU (как Intel i5-4570) для 1080p30
  это тяжело — снижайте `--scale 0.75/0.5` или `--fps`. Аппаратный энкодер из
  Фазы A здесь не задействован (см. развилку в `PLAN_OPTIMIZATION.md`).
- **Пароль пока только гейт комнаты.** Медиапоток шифруется DTLS-SRTP, но
  end-to-end-пароля поверх (как AES-GCM в TCP-пути) пока нет — добавить при
  доведении до продакшена.
- **Буфер обмена и передача файлов** в RTC-пути ещё не проброшены (есть в
  TCP-пути). DataChannel для них готов — добавить типы сообщений.
- NAT-traversal через реальный интернет **на одной машине не проверить** —
  нужна вторая сеть. Локально (loopback/LAN) путь подтверждён.
