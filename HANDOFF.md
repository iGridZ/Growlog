# GrowLog – Übergabe für Claude Code

Stand: 2026-10-27. Besitzer: Erik (Emsdetten, spricht Deutsch – UI, Texte, Commit-Nachrichten und Antworten auf Deutsch).
Bis hierher entstand das Projekt in einem Claude-Chat, ab jetzt wird es in Claude Code (mit Planungsfunktion) weiterentwickelt.
Der Chat selbst ist nur noch für Pflanzenpflege (Wiegen/Gießen/Klima) zuständig.

## Was GrowLog ist
Grow-Tagebuch als PWA für Erik und Freunde: Gewichte (Topf + Untersetzer), Gießen in ml, Notizen, Verlaufsdiagramm,
fertiger Discord-Block, Gieß-Assistent, Export/Backup, Mehrbenutzer. Läuft als Docker-Container auf Eriks Server.

## Stack und Struktur
- Flask + SQLite (`sqlite3`, Backup-API), gunicorn (`-w 1 --threads 4`, Port 8087). `-w 1` bewusst: Rate-Limit und Server-Config (TZ, Proxy) liegen im Prozessspeicher.
- `app.py` (~1300 Zeilen, alles Backend), `static/` mit `index.html`, `app.js`, `style.css`, `sw.js` (Cache-Name hochzählen bei UI-Änderungen, aktuell `growlog-v8`), `login.html`, `register.html`, `setup.html`, Manifest, Icons.
- Reines Vanilla-JS, keine Build-Tools. `Dockerfile` (python:3.13-slim), `requirements.txt` (flask 3.1, gunicorn 23).
- CI: `.github/workflows/docker.yml` baut bei Push auf `main` das Image `ghcr.io/igridz/growlog:latest` (amd64). Repo: https://github.com/iGridZ/Growlog (Eigentümer iGridZ).

## Architektur-Entscheidungen
- **Konten:** Session-Cookie (itsdangerous, 90 Tage, HttpOnly, SameSite=Lax, Secure hinter HTTPS), werkzeug-Hashes, Rollen admin/user, Einladungslinks (7 Tage, einmalig), IP-Rate-Limit (5 Fehlversuche / 5 min), Admin kann sperren/löschen/Passwort zurücksetzen. Erster Admin über `/setup` (oder optional `APP_USER`/`APP_PASSWORD`).
- **Datentrennung:** alle Tabellen haben `user_id`; jede Abfrage filtert über `uid()`. Altdaten (user_id 0) werden beim ersten Start dem ersten Admin zugeordnet (Migration in `init_db()`).
- **Einstellungen nur im UI:** Eriks ausdrückliche Vorgabe – nichts soll in `docker run`/Compose konfiguriert werden müssen. Server-Einstellungen (Zeitzone, Reverse-Proxy) in Tabelle `config`, Admin-Bereich im Konto-Tab. Env-Variablen sind nur optionale Startwerte. Neue Einstellungen daher immer im UI anbieten.
- **Discord-Webhook:** pro Nutzer, strenge URL-Regex (SSRF-Schutz). Backup/Restore nur Admin (Restore ersetzt alle Konten, legt vorher `vor-restore-…` an). Tägliches Auto-Backup, 14 Stück in `data/backups`.
- **Profil teilen:** `users.share` (Standard 0). Andere angemeldete Nutzer sehen schreibgeschützt Pflanzen, Gewichtsverlauf, Gießen (`/api/community`, `/api/profile/<id>`), nie Notizen/Webhook/Einstellungen. Ein öffentlicher Link ohne Konto wurde angeboten, aber nicht gewünscht/entschieden.
- **Gieß-Assistent (`compute_assist`):** Verbrauch aus Messungen, aber nicht als Tagesmittel: Licht-/Dunkelphase getrennt (Standard Licht 17:00–11:00), Gewichtung neuerer Messungen (Halbwertszeit 2 Tage), Wachstumstrend, Prognose in 15-Minuten-Schritten, Gießzeitpunkt (Standard 17:30), ml-Vorschlag = (Ziel − erwartetes Gewicht)/gemessenes g-pro-ml-Verhältnis. Ziel/Schwelle automatisch aus letztem Gießen (Messung danach/davor) oder manuell pro Pflanze. Eriks Wunsch: „realitätsnah, nicht Durchschnitt der letzten 4 Tage“.
- **Discord-Block:** nur heute + gestern, Verlust gegenüber letztem Wiegen des Vortags (inkl. Messung nach dem Gießen als Basis).

## Eriks Vorgaben / was er NICHT will
- Deployment: Docker CLI (Skript `growlog.sh` auf dem Server, **kein docker compose**) und Portainer; Image kommt von ghcr.io. Update: Image neu ziehen, Container neu erstellen, Daten liegen im gemounteten `data`-Ordner.
- Nicht gewünscht: Fotos, Events/Klima-Aufzeichnung (macht Discord bzw. VIVOSUN-App/Controller). Gewicht ist nicht die wichtigste Größe fürs Gießen, deshalb keine Überfrachtung.
- Bedienung vor allem am Handy (Pixel, Bitwarden für Login) → mobile-first, große Eingabefelder.
- Das Sandbox-Netz des Chats blockierte Docker Hub; Images baut deshalb GitHub Actions. In Claude Code lokal testen mit `docker build`, falls verfügbar, sonst `python3 -m gunicorn -w 1 -b 127.0.0.1:8099 app:app` mit `DB_PATH=/tmp/t.db`.

## Tests
Es gibt keine Testsuite im Repo (die bisherigen Playwright-/Python-Skripte lagen nur im Chat-Scratchpad). Empfehlung: `tests/` mit pytest (Flask test client) für Auth, Datentrennung, Migration, Assist-Modell und Profil-Freigabe anlegen. Wichtige Regression: Migration einer v1/v2-Datenbank (ohne `user_id`) auf Mehrbenutzer.

## Offene Ideen / nächste Schritte (Planung)
1. **Gieß-Modell verbessern:** höherer Verbrauch direkt nach dem Gießen (Feuchtigkeitsfaktor) – braucht 2–3 weitere Gießzyklen echter Daten; danach Abgleich Prognose vs. Realität.
2. **Import:** CSV/JSON-Import im UI (aktuell nur DB-Restore); Eriks Werte vom 25.–26.10. wurden über ein einmaliges Skript eingetragen (`docker exec -i growlog python3 - < import_werte.py`, nicht im Repo).
3. **Tests und CI:** pytest, optional Lint, CI-Job vor dem Image-Build.
4. **Profil-Freigabe:** optional öffentlicher Link ohne Konto (nur falls Erik es will).
5. **Aufräumen:** `app.py` ist groß – ggf. in Module aufteilen (auth, assist, api); `__pycache__` in `.gitignore` prüfen.
6. **Sicherheit prüfen:** CSRF (SameSite=Lax + JSON-API), Rate-Limits der Einladungs-/Setup-Routen, Passwort-Regeln.
7. Alfa-/Roots-Mengen der Gießeinträge vom 25.10. fehlen (im Verlauf-Tab nachtragen, kein Code nötig).

## Schwellenwert im Gieß-Assistenten – Eriks Kritik (zu klären/umbauen)
Erik ist mit der Herkunft des Schwellengewichts **nicht einverstanden**. Aktuelles Verhalten (`compute_assist` in `app.py`, Variablen `auto_target`/`auto_thr`):
- **Ziel (auto):** Gewicht der ersten Messung nach dem letzten Gießen (Messung, nach der ein Gießeintrag liegt, `post[i]`).
- **Schwelle (auto):** Gewicht der Messung unmittelbar *davor* (`entries[i-1]`), aber nur wenn diese ≤ 36 h vor der Nach-Gieß-Messung liegt. Also: das Gewicht, bei dem Erik zuletzt tatsächlich gegossen hat.
- Manuell überschreibbar pro Pflanze im Tab „Pflanzen“ (`plants.target`/`plants.threshold`, manuell gewinnt: `p["threshold"] or d["auto_thr"]`).
- Verwendet wird die Schwelle für: Füllstand in % (`(cur-thr)/(target-thr)`), `due_at`/`hours_left` (erster Prognosepunkt ≤ Schwelle) und die Wahl des Gießtermins (`chosen`: letzter Termin, bevor die Schwelle vor dem nächsten Termin erreicht würde).

**Problem:** Die Schwelle ist nur ein Zufallsstichprobe seiner Gewohnheit – er kann zu früh oder zu spät gegossen haben, und je nach Uhrzeit der Vor-Messung (z. B. 17:13 vor dem Gießen) streut der Wert. Sie sagt nichts darüber, ob die Pflanze wirklich Wasser brauchte. Mit nur 1–2 Gießzyklen ist sie besonders unzuverlässig, und eine zu hohe Schwelle lässt den Assistenten dauernd „gießen“ vorschlagen bzw. eine zu niedrige nie.

**Lösungsideen für die Planung (mit Erik abstimmen):**
1. Beim Gießeintrag ein Feld „war der Topf trocken genug?“ (zu nass / passend / zu trocken); die Schwelle wird nur aus „passend“-Gießvorgängen gelernt und über mehrere Zyklen gemittelt (Median), nicht aus dem letzten.
2. Schwelle als Anteil des Ziel-Gewichts über dem Trockengewicht (Topf + Erde + Untersetzer ungegossen wiegen = Leergewicht), z. B. Schwelle = Leergewicht + 50–60 % der Wasserkapazität (Ziel − Leergewicht); Anteil im UI einstellbar. Das ist unabhängig von Eriks Zufallszeitpunkt.
3. Schwelle pro Pflanze im UI als Prozent-Regler („gießen bei X % Füllstand“) statt Gramm; Gramm werden daraus berechnet.
4. Erst ab N ≥ 3 Gießzyklen eine automatische Schwelle anzeigen, davor „zu wenig Daten“ und nur manuelle Werte nutzen.
Erik muss entscheiden, welcher Ansatz gewünscht ist; bis dahin sind manuelle Werte der verlässlichste Weg.

## Pflanzen-Kontext (nur zum Verständnis der Daten)
Drei gekaufte Stecklinge im 80x80-VIVOSUN-Zelt: Blueberry Muffin (BM), Rainbow Soap (RS), London Marker (LM). Gewichte immer inkl. Untersetzer, morgens ca. 6:30–7:00 und abends gegen 17:30 (Gießzeit). Beratung zu Gießen/Klima/Training bleibt im Chat, nicht in diesem Projekt.
