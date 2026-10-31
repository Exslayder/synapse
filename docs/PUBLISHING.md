# Публикация проекта на GitHub

Инструкция от пустой папки до готового релиза с `Synapse.exe`, который умеет
находить собственные обновления.

---

## 1. Что нужно

- установленный [Git для Windows](https://git-scm.com/download/win);
- аккаунт GitHub;
- локальная копия проекта `synapse` (эта папка);
- желательно — Python 3.12 и PyInstaller, чтобы собрать `.exe` локально и проверить
  до первого push.

Проверьте, что Git видит ваши данные:

```powershell
git config --global user.name
git config --global user.email
```

Если пусто:

```powershell
git config --global user.name "Ваше Имя"
git config --global user.email "you@example.com"
```

---

## 2. Что попадёт в репозиторий, а что нет

В проекте уже есть `.gitignore`. Он исключает:

| Исключается | Почему |
| --- | --- |
| `config.json`, `config.broken-*.json` | личные настройки и пути |
| `profiles/` | cookies и сохранённые входы — **никогда не публикуйте** |
| `logs/`, `*.log` | журналы с доменами и ошибками |
| `app.lock` | служебный файл запуска |
| `updates/` | скачанные обновления |
| `dist/`, `build/`, `*.spec` | результаты сборки |
| `*.exe`, `*.zip`, `*.msi`, `*.sha256` | готовые сборки и контрольные суммы (их место — в Releases, не в git) |
| `*.tmp`, `*.bak`, `.coverage`, `htmlcov/`, `.tox/`, `.nox/` | временные файлы и кэш инструментов |
| `__pycache__/`, `*.py[cod]`, `*.egg-info/` | кэш Python |
| `.venv/`, `venv/` | виртуальные окружения |
| `.idea/`, `.vscode/`, `.DS_Store`, `Thumbs.db`, `desktop.ini` | мусор редакторов и ОС |
| `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | кэш инструментов |

Проверить перед первым коммитом:

```powershell
git init
git add -A
git status --short
```

В списке не должно быть `profiles/`, `config.json`, `dist/`, `logs/`.
Если что-то лишнее всё же появилось — добавьте в `.gitignore` и выполните
`git rm -r --cached <путь>`.

Полезно сразу проверить размер будущего репозитория:

```powershell
git ls-files | Measure-Object -Line
```

---

## 3. Создание репозитория на GitHub

1. Откройте <https://github.com/new>.
2. **Repository name**: `synapse` (или любое другое имя).
3. **Description**: короткое описание, например
   «Windows hub for Gemini, ChatGPT, Claude and Grok behind a local DoH proxy».
4. **Public** — если хотите, чтобы обновления мог скачать любой пользователь.
   Для **Private** GitHub Releases API тоже работает, но скачивание требует токена,
   а встроенная проверка обновлений рассчитана на публичный репозиторий.
5. **Не** ставьте галочки «Add a README», «Add .gitignore», «Choose a license» —
   эти файлы уже есть в проекте, иначе получится конфликт при первом push.
6. Нажмите **Create repository**.

---

## 4. Первый push

```powershell
cd F:\Python\tools\ai\opencode\synapse

git add -A
git commit -m "Synapse 1.0.2"
git branch -M main
git remote add origin https://github.com/ВАШ-ЛОГИН/synapse.git
git push -u origin main
```

Если Git попросит пароль — используйте **Personal Access Token** вместо пароля
(Settings → Developer settings → Personal access tokens → Tokens (classic) →
Generate new token, права `repo`). Либо настройте Git Credential Manager — тогда
откроется окно входа в браузере.

Проверка:

```powershell
git remote -v
git log --oneline -3
```

---

## 5. Укажите свой репозиторий в программе

Приложение ищет обновления в `exslayder/synapse`. Если ваш логин другой,
поправьте одно место — `src/synapse/updater.py`:

```python
DEFAULT_REPO = "ВАШ-ЛОГИН/synapse"
```

Либо не меняйте код, а задайте переменную окружения перед запуском:

```powershell
$env:SYNAPSE_REPO = "ВАШ-ЛОГИН/synapse"
python run.py --check-updates
```

Репозиторий в настройках приложения показывается на вкладке «Обновления» —
удобно проверить, что путь верный.

---

## 6. Права GitHub Actions

Workflow публикует релизы, поэтому ему нужно право писать в репозиторий.

1. Репозиторий → **Settings** → **Actions** → **General**.
2. **Workflow permissions** → **Read and write permissions**.
3. Сохраните (**Save**).

В самом workflow уже прописано:

```yaml
permissions:
  contents: write
```

Но глобальная настройка репозитория должна разрешать запись — иначе шаг
`softprops/action-gh-release` завершится ошибкой `403`.

---

## 7. Первый запуск сборки

После push в `main` workflow `Build Windows EXE` запустится сам. Посмотреть:

- вкладка **Actions** → **Build Windows EXE** → последний запуск;
- шаг **Tests** — unit-тесты и `compileall`;
- шаг **Build EXE** — сборка `dist/Synapse.exe`;
- шаг **Smoke test** — запуск `Synapse.exe --version`.

Запустить вручную: **Actions** → **Build Windows EXE** → **Run workflow** →
ветка `main` → **Run workflow**.

Скачать результат: внизу страницы запуска, блок **Artifacts** →
`Synapse-<номер>` (хранится 30 дней). Внутри:

```
Synapse.exe
Synapse.exe.sha256
Synapse-build-info.json
pip-freeze.txt
```

---

## 8. Релиз по тегу

Версия берётся из `src/synapse/__init__.py` (`__version__`) и `pyproject.toml`.
Перед тегом обновите оба значения и `CHANGELOG.md`.

```powershell
# версия уже 1.0.2
git add -A
git commit -m "Release 1.0.2"
git push

git tag v1.0.2
git push origin v1.0.2
```

Тег `v1.0.2` запускает workflow, который:

1. прогоняет тесты;
2. собирает `.exe`;
3. создаёт **обычный релиз** `v1.0.2` с файлами `Synapse.exe`,
   `.sha256`, build-info и `pip-freeze.txt`.

Проверка: вкладка **Releases** → `v1.0.2`. Файл `Synapse.exe` должен быть
в списке Assets. Именно его скачивает встроенный updater.

> Тег и версия в коде должны совпадать. Если в коде `1.0.2`, а тег `v1.2.0`,
> приложение после обновления снова предложит «обновиться» до `1.0.2`.

---

## 9. Еженедельные сборки

В workflow есть расписание:

```yaml
schedule:
  - cron: "0 3 * * 1"
```

Каждый понедельник в 03:00 UTC собирается свежий `.exe` и публикуется как
**prerelease** `weekly-YYYY.MM.DD`. Это удобно, чтобы проверить сборку на
актуальных pip/PyInstaller, не выпуская новую версию.

Prerelease не предлагается пользователям как обновление: `updater.py`
предпочитает стабильные релизы.

Отключить расписание можно, удалив блок `schedule` из
`.github/workflows/build.yml`.

---

## 10. Как пользователи получают обновления

1. Приложение при запуске (раз в сутки) или по кнопке «Проверить сейчас»
   обращается к `https://api.github.com/repos/ВАШ-ЛОГИН/synapse/releases`.
2. Если тег новее текущей версии, появляется кнопка «Обновление X.Y.Z».
3. «Скачать .exe» сохраняет файл в `updates/` рядом с программой.
4. Пользователь закрывает приложение, заменяет `Synapse.exe` и запускает снова.
   `config.json`, `profiles/` и `logs/` не трогаются.

Альтернатива — скачать `.exe` со страницы **Releases** вручную.

---

## 11. Локальная сборка перед публикацией

Чтобы не узнать о проблеме из логов Actions:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

python -m unittest discover -s tests -t . -v
python -m compileall -q src tests run.py build.py tools
python build.py
```

Проверка готового файла:

```powershell
.\dist\Synapse.exe --version
.\dist\Synapse.exe --check-updates
```

`--version` должен напечатать ту же версию, что и тег.

---

## 12. Частые проблемы

| Симптом | Причина и решение |
| --- | --- |
| `remote: Repository not found` | неверный URL или нет прав; проверьте `git remote -v` и логин |
| `403` на шаге релиза | Settings → Actions → General → **Read and write permissions** |
| Релиз создан, но без `.exe` | сборка упала на шаге **Build EXE**; смотрите лог шага |
| `--check-updates` пишет `репозиторий не найден (404)` | в `updater.py` остался `exslayder/synapse` или репозиторий приватный |
| Приложение снова предлагает ту же версию | тег новее, чем `__version__` в коде; обновите версию и пересоберите |
| Actions не запускается на тег | тег должен начинаться с `v`, например `v1.0.2`, и быть отправлен через `git push origin v1.0.2` |
| `dist/` или `profiles/` попали в коммит | `git rm -r --cached dist profiles`, проверьте `.gitignore` |
| SmartScreen предупреждает | `.exe` не подписан; это ожидаемо, подпись требует платного сертификата |

---

## 13. Полезные команды

```powershell
git status                              # что изменено
git add -A                              # подготовить всё
git commit -m "..."                     # закоммитить
git push                                # отправить в main
git tag v1.0.2                          # поставить тег
git push origin v1.0.2                  # отправить тег (запустит релиз)
git tag                                 # список тегов
gh release list                         # релизы через GitHub CLI, если установлен
gh run list --workflow build.yml        # список запусков workflow
```

Удалить ошибочный тег:

```powershell
git tag -d v1.0.0
git push origin :refs/tags/v1.0.0
```

После этого релиз на GitHub нужно удалить вручную во вкладке **Releases**.
