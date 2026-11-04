# Участие в Synapse

## Требования

- Windows 10/11 x64.
- Node.js 22+.
- Rust stable.
- Visual Studio Build Tools с компонентами C++.
- WebView2 Runtime.

## Локальная сборка

```powershell
npm ci
powershell -ExecutionPolicy Bypass -File .github/scripts/fetch-xray.ps1
npm run tauri build
```

`fetch-xray.ps1` скачивает Xray-core закреплённой версии и проверяет SHA-256.
Скрипт нужен и локально, и в CI: `xray.exe` не хранится в репозитории.

Проверка без установщиков:

```powershell
npx tauri build --no-bundle
```

Тесты:

```powershell
cd src-tauri
cargo test
```

## Pull request

`main` защищён ruleset: прямые пуши, force-push и удаление ветки запрещены.
Любое изменение оформляется через PR.

Перед PR:

1. Соберите приложение и запустите `cargo test`.
2. Проверьте, что в diff нет реальных VPN-ссылок, UUID, `pbk`, `tunnel.dat`,
   `config.json`, `profiles/` и `logs/`.
3. Не меняйте версию и не создавайте тег без отдельного согласования.

## Секреты

- Реальные ссылки `vless://`, UUID и `pbk` нельзя коммитить, вставлять в issue,
  скриншоты и логи.
- Для тестов используйте только заглушки.
- `docs/vpn-integration.md` и `src-tauri/binaries/` уже добавлены в `.gitignore`.
- VPN-ссылка пользователя хранится в `tunnel.dat` через Windows DPAPI и не должна
  попадать в репозиторий.

## Зависимости и релизы

- Dependabot открывает PR с обновлениями зависимостей; их нужно просматривать
  и мержить вручную.
- Еженедельный workflow обновляет lock-файлы и открывает отдельный PR.
- Релиз создаётся только по тегу `v*` и только после явного решения мейнтейнера.
- Портативная сборка публикуется как `Synapse_portable.zip`; отдельные
  `Synapse.exe`/`xray.exe` в релиз не кладутся.

## Безопасность

Уязвимости не обсуждаются в публичных issue. Порядок описан в
[SECURITY.md](SECURITY.md).
