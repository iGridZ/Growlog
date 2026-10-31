---
name: anforderungen
description: Kurzer Fragebogen vor größeren Features oder Design-Entscheidungen in GrowLog (Prognose-Modell, Datenmodell, neue Verfahren, Anzeige). Nutzen, wenn eine Aufgabe mehrdeutig ist oder mehr als ein paar Zeilen ändert, oder wenn der Nutzer /anforderungen schreibt.
---

# Anforderungen klären

Ziel: Missverständnisse vor dem Bauen finden. Nichts ändern, bevor der Nutzer bestätigt hat.

1. Lies die Aufgabe und `HANDOFF.md` (Eriks Vorgaben). Notiere, welche Wörter mehrdeutig sind (z. B. „Art“, „Variante“, „Verfahren“).
2. Stelle mit AskUserQuestion höchstens 4 Fragen mit je 2–4 Optionen. Die Optionen müssen sich gegenseitig ausschließen und konkret sein (Beispiel statt Fachwort). Wähle aus:
   - **Was genau?** Die Auslegung der mehrdeutigen Stelle, mit je einem Beispiel pro Option.
   - **Was soll gleich bleiben?** Verhalten, Anzeige oder Daten, die nicht angefasst werden dürfen.
   - **Wann ist es gut?** Woran Erik merkt, dass es funktioniert (Zahl, Anzeige, Test auf dem Server).
   - **Wie vorgehen?** Erst Test-Container oder gleich mergen; Tests zuerst oder danach.
3. Fasse die Antworten in 2–3 Sätzen zusammen: „Ich baue …, ich ändere nicht …, fertig ist es, wenn …“. Warte auf ein Ja.
4. Erst danach bauen: eigener Branch, Tests, Browser-Prüfung, Push des Branches. Nach `main` nur mit Eriks Ja.

Bei Unsicherheit lieber eine Frage zu viel als ein falsch gebautes Feature.
