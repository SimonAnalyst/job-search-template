# Wyszukiwarka ofert pracy

Prosty projekt do zbierania ofert według własnych kryteriów. GitHub Actions uruchamia wyszukiwanie raz dziennie, a gotowy dashboard HTML można pobrać i otworzyć lokalnie. E-mail z nowymi ofertami jest opcjonalny.

## Co będzie potrzebne

- Konto GitHub i własne **prywatne** repozytorium z tym projektem.
- Do użycia szerokiej wyszukiwarki Adzuna: konto i własne `app_id` oraz `app_key` z [Adzuna API](https://developer.adzuna.com/overview).
- Opcjonalnie adresy stron karier firm korzystających z Greenhouse lub Lever.
- Opcjonalnie konto pocztowe umożliwiające wysyłkę SMTP i hasło aplikacji, jeśli wymaga go dostawca.

Adzuna jest źródłem o szerokim zakresie ofert. Greenhouse i Lever przeszukują tylko wskazane strony karier; nie przeszukują całego internetu. W Lever wybierz `region = "global"` dla `jobs.lever.co` albo `region = "eu"` dla `jobs.eu.lever.co`. W jednej konfiguracji można wybrać jeden region. Ten projekt nie zawiera adapterów wszystkich portali z innych wyszukiwarek. Dostępność ofert zależy od źródeł, limitów API i ustawionych kryteriów. Bezpłatny poziom Adzuna jest przewidziany do oceny API; sprawdź aktualny limit i warunki na swoim koncie przed ustawieniem codziennych zapytań. Zapoznaj się z [zasadami Adzuna](https://developer.adzuna.com/docs/terms_of_service) i zasadami pozostałych źródeł.

## 1. Przygotuj repozytorium

1. Utwórz w GitHub nowe **prywatne** repozytorium bez dodawania README. Na swoim komputerze sklonuj je przez `git clone ADRES_REPOZYTORIUM` (adres HTTPS skopiujesz z przycisku **Code**). Skopiuj do powstałego folderu pliki projektu, w tym ukryty folder `.github`, lecz bez folderu `.git` z otrzymanej kopii. W folderze repozytorium wykonaj `git add .`, `git commit -m "Start"`, `git branch -M main` i `git push -u origin main`. Jeśli GitHub poprosi o zalogowanie, użyj własnego konta i metody logowania Git. Możesz też skorzystać z prywatnej kopii przygotowanej dla Ciebie.
2. Jeśli dostałeś dostęp do repozytorium innej osoby, przed rozpoczęciem pracy przygotuj własną prywatną kopię. Każdy, kto ma dostęp do repozytorium, może też odczytać jego gałąź z historią ofert i wyniki działań.
3. Otwórz plik `config.toml`, wybierz hasła wyszukiwania, lokalizację i źródła. Zapisz zmiany w repozytorium. Przykładowe opcje są opisane w pliku.
4. Workflow prosi o dostęp do zapisu historii w tym repozytorium. Jeśli po uruchomieniu zapis zostanie odrzucony, sprawdź ustawienia i reguły repozytorium lub organizacji; administrator może ograniczać takie uprawnienia.

## 2. Skonfiguruj źródła

W pliku `config.toml` włącz źródła, których chcesz używać. `country` dotyczy tylko Adzuna; Greenhouse i Lever mogą zwracać oferty z całego świata, więc ustaw `location`, jeżeli chcesz zawęzić wyniki. Filtr lokalizacji jest tekstowy i zależy od zapisu lokalizacji w źródle, więc może pomijać pasujące oferty. Dla Adzuna wpisz własne dane w GitHub przez **Settings → Secrets and variables → Actions → New repository secret**:

- `ADZUNA_APP_ID`
- `ADZUNA_APP_KEY`

Wartości pobierzesz z własnego konta Adzuna. Nie wpisuj ich do `config.toml`, plików projektu ani wiadomości. Dla Lever i Greenhouse wpisz identyfikatory konkretnych stron karier w odpowiednich listach w konfiguracji; nie wymagają tych kluczy. Weryfikuj, czy adres wybranej firmy faktycznie korzysta z danej platformy.

## 3. Uruchom i otwórz dashboard

1. W GitHub otwórz kartę **Actions** i w razie potrzeby włącz workflow.
2. Wybierz **Oferty pracy → Run workflow → Run workflow**. Poczekaj na zakończenie.
3. Wejdź w zakończone uruchomienie, w sekcji **Artifacts** pobierz `dashboard-ofert`, rozpakuj ZIP i otwórz `dashboard.html` na swoim komputerze.
4. Przy kolejnych uruchomieniach pobierz nową wersję dashboardu. Plik jest samodzielny i nie wymaga uruchamiania serwera.

Gdy ręczny test zakończy się poprawnie, włącz codzienne zbieranie: w GitHub wejdź w **Settings → Secrets and variables → Actions → Variables → New repository variable**, ustaw nazwę `JOB_SEARCH_ENABLED` i wartość `true`, a następnie zapisz. Bez tej zmiennej uruchomienia według harmonogramu są pomijane; ręczne nadal działają. Aby zatrzymać harmonogram, usuń zmienną albo zmień jej wartość.

Po włączeniu proces uruchamia się codziennie o 06:17 UTC (w Polsce 07:17 zimą albo 08:17 latem). GitHub może opóźnić lub pominąć zaplanowane uruchomienie; w karcie Actions można sprawdzić stan i uruchomić je ręcznie. Artefakt dashboardu jest przechowywany przez 30 dni. Historia ofert jest osobno zapisywana w gałęzi `job-data`; pliki głównego kodu nie są tam umieszczane. Przy pierwszym uruchomieniu gałąź powstaje automatycznie. Usunięcie tej gałęzi usuwa zapisaną historię i powoduje traktowanie znalezionych ofert jako nowych.

Jeśli źródło zwróci błąd, sprawdź wynik uruchomienia Actions. Częściowa awaria jest raportowana w logu; awaria wszystkich źródeł kończy proces błędem i zachowuje poprzednią historię. Sam fakt, że workflow zakończył się powodzeniem, nie gwarantuje znalezienia wszystkich ofert na rynku.

## 4. Opcjonalny e-mail

W `config.toml` ustaw `enabled = true` w sekcji `[email]`. W **Settings → Secrets and variables → Actions** dodaj:

- `SMTP_HOST` — serwer pocztowy;
- `SMTP_PORT` — port serwera;
- `SMTP_SECURITY` — `ssl` albo `starttls`, zgodnie z dostawcą;
- `SMTP_USERNAME` i `SMTP_PASSWORD` — dane logowania (najlepiej hasło aplikacji);
- `SMTP_FROM` i `SMTP_TO` — adres nadawcy i odbiorcy.

Korzystaj z ustawień wskazanych przez dostawcę poczty. Funkcja jest domyślnie wyłączona. Uruchomienie z włączoną pocztą wysyła wiadomość, gdy w kolejce są nowe oferty z bieżącego albo wcześniejszego uruchomienia. Historia i kolejka są zapisywane przed próbą wysyłki. Jeśli wysyłka się nie powiedzie, sprawdź log Actions; proces spróbuje ponownie po kolejnym udanym pobraniu ofert. Gdy po dostarczeniu wiadomości proces przerwie się przed zapisaniem potwierdzenia, ponowienie może spowodować duplikat.

## Uruchomienie na komputerze

Wymagany jest Python 3.11 lub nowszy. Jeśli lokalnie korzystasz z Adzuna, w otwartym oknie PowerShell ustaw klucze na czas tej sesji, wprowadzając własne wartości po wyświetleniu prośby:

```powershell
$env:ADZUNA_APP_ID = Read-Host "Podaj Adzuna app_id"
$env:ADZUNA_APP_KEY = Read-Host "Podaj Adzuna app_key"
```

Dla samych Greenhouse i Lever klucze nie są potrzebne. W folderze projektu, po skonfigurowaniu `config.toml`, uruchom:

```powershell
python -m jobsearch.cli run --config config.toml --state data/jobs.json --dashboard output/dashboard.html --new-jobs output/new-jobs.json
```

Lokalne uruchomienie korzysta z lokalnego pliku historii; nie synchronizuje go automatycznie z gałęzią GitHub. Aby obejrzeć wyniki z Actions bez konfiguracji Pythona, pobierz artefakt jak opisano wyżej. Lokalnie utworzone pliki wynikowe i sekrety nie powinny trafiać do repozytorium.

## Prywatność i ograniczenia

Konfiguracja wyszukiwania i gałąź z ofertami są widoczne dla osób z dostępem do repozytorium. Nie umieszczaj w konfiguracji danych wrażliwych. GitHub Secrets nie są częścią repozytorium, ale dostęp do wyników, logów i uprawnień workflow należy ograniczyć do zaufanych osób. Oferty mogą wygasać lub zmieniać się u źródła; otwórz oryginalny link, aby sprawdzić aktualność. Wynik nie jest automatyczną aplikacją o pracę.









