# CLAUDE.md
Projekt: GrowLog (Flask + SQLite + Vanilla-JS-PWA). Vollständige Übergabe und Entscheidungen: siehe `HANDOFF.md`.
- Sprache: Deutsch (UI, Texte, Commits, Antworten).
- Alle Einstellungen müssen im Web-UI änderbar sein, nicht nur per Umgebungsvariable.
- Deployment: Docker CLI + Portainer, kein docker compose; Image über GitHub Actions nach ghcr.io.
- Mobile-first, kein Build-Schritt. Bei UI-Änderungen Cache-Namen in `static/sw.js` hochzählen.
- gunicorn bleibt bei `-w 1` (Zustand im Prozessspeicher).
- Jede Datenabfrage muss nach `user_id` filtern.
