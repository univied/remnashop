<div align="center" markdown>

<p align="center">
    <a href="https://github.com/snoups/remnashop/blob/main/README.md"><b>ENGLISH</b></a> •
    <u><b>РУССКИЙ</b></u>
</p>

![remnashop](https://github.com/user-attachments/assets/57ba5832-4646-45e1-b082-f8f2f5e82c3e)

**Этот проект представляет собой Telegram-бота для продажи VPN-подписок, интегрированного с Remnawave.**

[![Static Badge](https://img.shields.io/badge/public_group-white?style=social&logo=Telegram&logoColor=blue&logoSize=auto&labelColor=white&link=https%3A%2F%2Ft.me%2Fsn0ups)](https://t.me/remna_shop)
[![Static Badge](https://img.shields.io/badge/remnawave-white?style=social&logo=Telegram&logoColor=blue&logoSize=auto&labelColor=white&link=https%3A%2F%2Ft.me%2Fsnoups)](https://t.me/+xQs17zMzwCY1NzYy)
![GitHub Repo stars](https://img.shields.io/github/stars/snoups/remnashop)

<p align="center">
    <br />
    <p align="center">
        <a href="https://remnashop.mintlify.app/docs/ru/overview/releases">
            <img src="https://img.shields.io/badge/Начать-%E2%86%92-0969da?style=for-the-badge&labelColor=0969da&color=0969da" alt="Начать" width="200" height="auto">
        </a>
    </p>
    <a href="https://github.com/snoups/remnashop/releases">
        <img src="https://img.shields.io/github/v/release/snoups/remnashop?label=Latest%20release&style=social" alt="Latest release">
    </a>
</p>
</div>
## Оплата через Anore

В панели бота откройте **RemnaShop → Платёжные шлюзы → Anore**. Укажите
`SHOP_ID`, `API_KEY` и `SECRET_KEY` магазина для подписи вебхуков.
Используйте `an_test_…` для тестового платежа из панели и `an_live_…`
для покупок. Включите шлюз и проверьте, что Anore может обратиться к
`https://<домен>/api/v1/payments/anore`. В Anore включите события
`payment.succeeded` и `payment.expired`.

При выключенном `H2H_ENABLED` покупатель получает ссылку на форму Anore.
При включённом шлюз запрашивает оплату через СБП и выдаёт прямую ссылку
`sbpUrl`, когда она есть в ответе Anore. Иначе открывается форма Anore. Доступность
прямой ссылки управляется настройками магазина на стороне Anore.

Для идентификации плательщика передаётся его Telegram ID и, если известен,
Telegram username. Для покупателей без Telegram ID используется
подтверждённый email.

Если магазин работает в режиме франшизы, заполните данные филиала в настройках
Anore в боте:

- `FRANCHISE_TYPE` — `telegram_bot` или `website`.
- `FRANCHISE_ADDRESS` — username бота филиала либо HTTPS origin сайта, например
  `https://shop.example`.
- `FRANCHISE_OWNER_TELEGRAM_ID` — Telegram ID владельца филиала.
- `FRANCHISE_OWNER_TELEGRAM_USERNAME` — username владельца, если известен.

Эти настройки относятся к конкретному экземпляру RemnaShop. Для обычного
магазина оставьте все поля `FRANCHISE_*` пустыми.

[Документация Anore](https://anore.cc/docs#create)
