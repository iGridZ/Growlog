# TASK.md – Aufgabenstand GrowLog

Stand: 2026-11-02. Wird nach jeder Arbeitssitzung aktualisiert (erledigt nach unten, Offenes nach oben).
Entscheidungen und Hintergrund stehen in `HANDOFF.md`, Arbeitsregeln in `CLAUDE.md`.

## Offen

### Jetzt als Nächstes
- [ ] `fix/review-2026-11` auf dem Test-Container (Port 8088, Datenkopie) prüfen: `python -m pytest`, Oberfläche im Browser, Konsole ohne Fehler. Danach Eriks Ja, dann nach `main`.

### Feature „ohne Wiegen nutzbar“ (Anforderungen geklärt, startet nach Merge der Fixes)
Branch: `feature/ohne-wiegen`. Beschlossen am 2026-11-02:
- [ ] Pro Pflanze Schalter im Tab Pflanzen: Automatisch (Standard) / Wird gewogen / Wird nicht gewogen.
- [ ] „Automatisch“: Pflanze gilt als ungewogen, wenn seit 3 Tagen keine Messung eingetragen wurde.
- [ ] Discord-Block: bei ungewogenen Pflanzen die Gießeinträge von heute und gestern zeigen (heute nur „Noch keine Gewichte eingetragen.“).
- [ ] Gieß-Übersicht (Tab Wiegen): „zuletzt gegossen vor X Tagen, Y ml“ statt Gewicht und Prognose.
- [ ] Discord-Erinnerung: dasselbe statt Gewichts-Prognose.
- [ ] Tab Wiegen: Eingabefeld für ungewogene Pflanzen ausblenden; alte Gewichte und Diagramm bleiben im Verlauf.
- [ ] Tests, Browser-Prüfung, Cache-Name in `static/sw.js` hochzählen.
- Nicht ändern: Wiegen-Code, Prognose-Modell, „Letzte 7 Tage“.

### Weitere Ideen aus dem Review (noch nicht entschieden, erst `/anforderungen`)
- [ ] Einträge bearbeiten (heute nur löschen und neu eintragen).
- [ ] Nährstoff-Felder (AlfaBoost, Betta Roots) sind fest verdrahtet; ggf. frei konfigurierbar.
- [ ] Verlauf-Listen zeigen nur 30/20 Einträge, Notizen 100; „mehr laden“.
- [ ] Admin-Passwort-Reset per Kommandozeile.
- [ ] `trust_proxy` ist standardmäßig an: bei direktem Zugriff ohne Proxy lässt sich die IP fälschen. Hinweis im Admin-Bereich, Standard nicht ändern (NPM-Setup).
- [ ] Offline-Warteschlange fürs Speichern von Gewichten.
- [ ] Tests für Restore, CSV-Export, Discord-Text, Config, Pflanze löschen; optional Lint.
- [ ] `app.py` ggf. aufteilen (Vorschlag: Assistent-Block nach `assist.py`); Spalte `plants.threshold` ist ungenutzt.
- [ ] Gieß-Modell: höherer Verbrauch direkt nach dem Gießen (braucht 2–3 weitere Gießzyklen Daten).
- [ ] Profil-Freigabe per öffentlichem Link ohne Konto (nur falls Erik es will).
- [ ] Alfa-/Roots-Mengen der Gießeinträge vom 25.10. im Verlauf-Tab nachtragen (kein Code nötig).

## Erledigt

### 2026-11-02 – Code-Review, Branch `fix/review-2026-11`
- [x] Einladung wird atomar eingelöst; doppelter Benutzername ergibt 400 statt 500.
- [x] Eingabeprüfung: ungültige Zeit (400), Nicht-Strings bei Name/Webhook, Notiz max. 2000 Zeichen und nur bekannte Pflanze, Zielgewicht 100–20000 g.
- [x] `discord_post` fängt Timeouts; gekürzter Discord-Text behält den schließenden Codeblock.
- [x] CSV-Export schützt vor Excel-Formeln (`=`, `+`, `-`, `@`).
- [x] `app.js`: Pflanzenname, Notiz-Pflanze und Mix-Text escaped; try/catch bei Notiz speichern, Löschen, Webhook entfernen, Discord-Tab; „kein Gießen nötig“ statt „ca. 0 ml“.
- [x] `sw.js` auf `growlog-v16`; `/register` und `/setup` werden nicht mehr gecacht.
- [x] README (Schwellen-Text entfernt) und HANDOFF (Cache-Version, `/api/insights`, offene Punkte) aktualisiert.
- [x] Neue Tests in `tests/test_review.py` (noch nicht ausgeführt, nur Syntax geprüft).

### Bis 2026-10-27 (Chat-Phase, laut HANDOFF)
- [x] Flask + SQLite + PWA, Docker-Image über GitHub Actions nach ghcr.io.
- [x] Mehrbenutzer mit Login, Rollen, Einladungslinks, Rate-Limit, Datentrennung.
- [x] Einstellungen im Web-UI (Zeitzone, Reverse-Proxy), Backup/Restore, Tages-Backup, CSV-Export.
- [x] Profil teilen (nur lesen für angemeldete Nutzer).
- [x] Gieß-Assistent mit Licht/Dunkel-Modell, Prognose und ml-Vorschlag; Schwellenwert entfernt.
- [x] Discord-Block, Webhook-Versand und tägliche Gießerinnerung.
- [x] Wochenübersicht und Prognose-Check im Tab Verlauf.
- [x] pytest-Suite (Auth, Datentrennung, Migration, Profil, Assistent, Insights, Erinnerung) und CI-Job.
