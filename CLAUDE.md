# CLAUDE.md
Projekt: GrowLog (Flask + SQLite + Vanilla-JS-PWA). Vollständige Übergabe und Entscheidungen: siehe `HANDOFF.md`.
- Sprache: Deutsch (UI, Texte, Commits, Antworten).
- Alle Einstellungen müssen im Web-UI änderbar sein, nicht nur per Umgebungsvariable.
- Deployment: Docker CLI + Portainer, kein docker compose; Image über GitHub Actions nach ghcr.io.
- Mobile-first, kein Build-Schritt. Bei UI-Änderungen Cache-Namen in `static/sw.js` hochzählen.
- gunicorn bleibt bei `-w 1` (Zustand im Prozessspeicher).
- Jede Datenabfrage muss nach `user_id` filtern.

## Arbeitsweise
- Jedes Feature auf eigenem Branch (`feature/…`). Test-Container auf dem Server (Port 8088, Datenkopie), erst nach Erik's Ja nach `main`.
- Vor jedem UI-Push im Browser prüfen (lokal mit Testdaten, Konsole auf Fehler lesen). `node --check` und pytest reichen nicht.
- Nicht raten bei Design-Entscheidungen (Prognose-Modell, Datenmodell, neue Verfahren, Anzeige): zuerst `/anforderungen` nutzen oder das Verständnis in 2–3 Sätzen bestätigen lassen und auf Antwort warten.
- Wörter wie „Art“, „Variante“, „Verfahren“, „Methode“ sind mehrdeutig: zurückfragen, was genau gemeint ist, bevor gebaut wird.
