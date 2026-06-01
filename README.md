# 🖼️ Telegram Screenshot Bot

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-blue.svg)](https://www.docker.com/)
[![Playwright](https://img.shields.io/badge/Playwright-1.44.0-green.svg)](https://playwright.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Telegram-бот для безопасного предпросмотра ссылок. Отправляет скриншот веб-страницы и текстовую карточку с метаданными, чтобы пользователь не переходил по подозрительным ссылкам.

---

## ✨ Возможности

- 🔗 **Мгновенное предупреждение** — до генерации скриншота бот предупреждает о потенциальной опасности
- 📱 **Мобильный вид** — скриншоты в разрешении 390×844 (@2x) для реалистичного отображения
- 🖼️ **Полная страница** — снимает всю страницу и нарезает на части (до 5120 px)
- 🏷️ **Метаданные** — извлекает title, description, price, brand, rating (JSON-LD Schema.org)
- 🛡️ **SSRF-защита** — блокирует запросы к приватным IP-адресам
- 💾 **Кэширование** — TTLCache для file_id отправленных фото
- 🚫 **Блокировка рекламы** — отключает загрузку трекеров и рекламных скриптов
- 🔧 **Оптимизация под 512 МБ RAM** — SEMAPHORE=1, управление памятью

---

## 🏗️ Архитектура

```mermaid
flowchart TD
    A[User → Telegram] --> B[aiogram Router]
    B --> C{security.is_safe?}
    C -->|No| D[🚫 Заблокировано]
    C -->|Yes| E[cache.get?]
    E -->|Hit| F[Отправка из кэша]
    E -->|Miss| G[⚠️ Предупреждение]
    G --> H[Параллельно:]
    H --> I[metadata.fetch/httpx]
    H --> J[screenshot.shoot/Playwright]
    I --> K[merge_meta]
    J --> K
    K --> L[reply_photo/media_group]
    L --> M[cache.save]

