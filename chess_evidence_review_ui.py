from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Any


def render_chess_evidence_review_html(payload: Mapping[str, Any]) -> str:
    artifact_id = str(payload.get("artifact_id") or "")
    source_profile = str(payload.get("source_profile") or "")
    source_digest = str(payload.get("source_document_sha256") or "")
    browser_payload = dict(payload)
    browser_payload.pop("source_document_sha256", None)
    browser_payload["rows"] = [
        {key: value for key, value in dict(row).items() if key != "source_document_sha256"}
        for row in payload.get("rows") or []
        if isinstance(row, Mapping)
    ]
    seed = json.dumps(browser_payload, ensure_ascii=False).replace("</", "<\\/")
    replacements = {
        "__ARTIFACT__": html.escape(artifact_id),
        "__PROFILE__": html.escape(source_profile),
        "__SOURCE_SHORT__": html.escape(source_digest[:12] + "..." if source_digest else "brak"),
        "__SEED__": seed,
    }
    result = _PAGE
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


_PAGE = """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/static/chess_evidence_review.css">
  <title>KindleMaster - markery diagramow</title>
</head>
<body>
  <main class="review-shell">
    <header class="review-header">
      <div>
        <p class="eyebrow">KindleMaster / dowody zrodlowe</p>
        <h1>Zaznacz marker. Reszte policzy mechanizm.</h1>
        <p class="lede">Rysuj prostokat tylko wokol widocznego symbolu. Brak markera potwierdzaj wylacznie na kompletnym cropie.</p>
      </div>
      <dl class="source-card">
        <div><dt>Artefakt</dt><dd>__ARTIFACT__</dd></div>
        <div><dt>Profil</dt><dd>__PROFILE__</dd></div>
        <div><dt>Zrodlo</dt><dd>__SOURCE_SHORT__</dd></div>
      </dl>
    </header>

    <section class="progress-deck" aria-label="Postep kolejki">
      <div class="progress-copy"><strong id="progress-label">0 / 0</strong><span>zamknietych rekordow</span></div>
      <div class="progress-track" aria-hidden="true"><span id="progress-bar"></span></div>
      <div class="metric-row">
        <span><b id="metric-open">0</b> otwarte</span>
        <span><b id="metric-visible">0</b> marker</span>
        <span><b id="metric-absence">0</b> brak</span>
        <span><b id="metric-unclear">0</b> niejasne</span>
      </div>
      <span class="save-state" id="save-state" role="status" aria-live="polite">Gotowe</span>
    </section>

    <div class="workspace">
      <aside class="queue-panel" aria-label="Kolejka diagramow">
        <label class="search-field">Szukaj<input id="queue-search" type="search" placeholder="ID albo strona"></label>
        <div class="queue-filters" role="group" aria-label="Filtr statusu">
          <button type="button" class="filter is-active" data-filter="all">Wszystkie</button>
          <button type="button" class="filter" data-filter="open">Otwarte</button>
          <button type="button" class="filter" data-filter="closed">Zamkniete</button>
        </div>
        <nav id="queue-list" class="queue-list" aria-label="Diagramy"></nav>
      </aside>

      <section class="review-stage" aria-labelledby="diagram-title">
        <div class="stage-head">
          <div><p id="diagram-kicker" class="eyebrow">Diagram</p><h2 id="diagram-title">Ladowanie...</h2></div>
          <div class="stage-actions"><button type="button" id="previous-item" class="secondary">Poprzedni</button><button type="button" id="next-item" class="secondary">Nastepny</button></div>
        </div>

        <div class="review-columns">
          <section class="image-panel" aria-label="Crop do oznaczenia">
            <div id="image-stage" class="image-stage" tabindex="0" aria-label="Narysuj prostokat wokol markera">
              <img id="evidence-image" alt="Crop dowodu markera" draggable="false">
              <div id="bbox" class="bbox" hidden><span>marker</span></div>
              <div id="image-empty" class="image-empty" hidden><strong>Brak cropa do oznaczenia</strong><span>Ten rekord wymaga ponownego wygenerowania zasobu. Nie zgaduj bbox.</span></div>
            </div>
            <div class="image-toolbar"><span id="asset-kind">brak zasobu</span><button type="button" id="clear-bbox" class="text-button">Wyczysc bbox</button></div>
            <p class="drawing-help">Przeciagnij po obrazie od jednego rogu markera do drugiego. Bbox zapisuje sie w ukladzie 0..1 wzgledem tego cropa.</p>
          </section>

          <form id="review-form" class="decision-panel">
            <section>
              <p class="section-number">01</p><h3>Co widzisz?</h3>
              <div class="choice-grid" role="radiogroup" aria-label="Typ markera">
                <label><input type="radio" name="marker_shape" value="outline_triangle"><span><b>W</b> Biale maja ruch</span></label>
                <label><input type="radio" name="marker_shape" value="filled_triangle"><span><b>B</b> Czarne maja ruch</span></label>
                <label><input type="radio" name="marker_shape" value="none_confirmed"><span><b>0</b> Brak markera</span></label>
                <label><input type="radio" name="marker_shape" value="unclear"><span><b>?</b> Niejednoznaczny</span></label>
              </div>
            </section>

            <section>
              <p class="section-number">02</p><h3>Warunki dowodu</h3>
              <label class="field">Strona ruchu<select id="side-to-move"><option value="">Nie ustalono</option><option value="w">Biale</option><option value="b">Czarne</option></select></label>
              <label class="check-row"><input id="crop-complete" type="checkbox"><span>Crop obejmuje caly obszar, w ktorym marker mogl wystapic</span></label>
              <label class="field">Osoba weryfikujaca<input id="reviewer" autocomplete="name" placeholder="np. PM"></label>
              <label class="field">Uwagi<textarea id="notes" rows="3" placeholder="Tylko istotne niejednoznacznosci"></textarea></label>
            </section>

            <section class="decision-footer">
              <p id="validation" class="validation" role="alert"></p>
              <div class="decision-actions">
                <button type="button" data-decision="unclear" class="secondary">Do wyjasnienia</button>
                <button type="button" data-decision="excluded" class="secondary">Wyklucz</button>
                <button type="submit" class="primary">Zapisz i nastepny</button>
              </div>
            </section>
          </form>
        </div>
      </section>
    </div>
  </main>
  <script type="application/json" id="review-seed">__SEED__</script>
  <script defer src="/static/chess_evidence_review.js"></script>
</body>
</html>
"""
