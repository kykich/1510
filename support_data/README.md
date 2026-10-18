# Тестовые данные поддержки

Каталог `support_data/` — **вымышленные** пользователи и тикеты для задачи 3.
Реальные данные клиентов здесь хранить нельзя: для реального контура задайте
`SUPPORT_DATA_DIR` на папку вне репозитория.

## `users.json`

```json
{
  "users": [
    {
      "id": "u-1001",
      "name": "Анна Петрова",
      "email": "anna@example.com",
      "phone": "+7 999 100-10-01",
      "plan": "pro",
      "status": "active",
      "locale": "ru",
      "created_at": "2026-01-15",
      "two_factor": true,
      "last_seen": "2026-10-18 09:40"
    }
  ]
}
```

Обязательные поля: `id`, `name`, `email`. Остальные используются в контексте
ответа (тариф, статус, 2FA, последняя активность).

## `tickets.json`

```json
{
  "tickets": [
    {
      "id": "T-1001",
      "user_id": "u-1001",
      "subject": "Не работает авторизация после смены пароля",
      "description": "…",
      "status": "open",
      "priority": "high",
      "created_at": "2026-10-18 08:55",
      "updated_at": "2026-10-18 09:40",
      "product": "DeepSeek Chat",
      "version": "1410",
      "channel": "web",
      "error_code": "401",
      "tags": ["auth", "password", "2fa"],
      "steps_tried": ["…"],
      "messages": [
        {"at": "…", "role": "user", "text": "…"},
        {"at": "…", "role": "support", "text": "…"}
      ]
    }
  ]
}
```

`messages` — история обращения; `steps_tried` — что пользователь уже делал;
`error_code`, `version`, `product` помогают RAG-поиску по FAQ.

## Демо-тикет

`T-1001` — ошибка `401` после смены пароля, включена 2FA, оставалась старая
вкладка. FAQ объясняет, что делать с сессией/кэшем и когда эскалировать.
