from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from typing import Any


PIECE_OPTIONS = (
    ("", "Puste", "×"),
    ("K", "Biały król", "♔"),
    ("Q", "Biały hetman", "♕"),
    ("R", "Biała wieża", "♖"),
    ("B", "Biały goniec", "♗"),
    ("N", "Biały skoczek", "♘"),
    ("P", "Biały pion", "♙"),
    ("k", "Czarny król", "♚"),
    ("q", "Czarny hetman", "♛"),
    ("r", "Czarna wieża", "♜"),
    ("b", "Czarny goniec", "♝"),
    ("n", "Czarny skoczek", "♞"),
    ("p", "Czarny pion", "♟"),
)


def render_fen_manual_review_html(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_identity: Mapping[str, Any] | None = None,
    artifact_id: str = "",
) -> str:
    identity = dict(source_identity or {})
    cards = "\n".join(render_fen_manual_review_card(row) for row in rows)
    if not cards:
        cards = '<div class="empty-state">Brak diagramów do oznaczenia.</div>'
    seed_rows = [{key: value for key, value in row.items() if key != "nearby_text"} for row in rows]
    seed_json = json.dumps(seed_rows, ensure_ascii=False).replace("</", "<\\/")
    source_digest = str(
        identity.get("source_document_sha256")
        or identity.get("source_artifact_sha256")
        or "brak"
    )
    source_binding = str(identity.get("source_binding") or "artifact")
    source_binding_label = {
        "source_pdf_sha256": "SHA pliku źródłowego",
        "preserved_source_sha256": "zachowany SHA źródła",
        "preserved_source_sha256_pdf_mismatch": "zachowany SHA źródła; obcy PDF odrzucony",
        "artifact_report_sha256": "SHA raportu konwersji",
    }.get(source_binding, source_binding)
    replacements = {
        "__ARTIFACT_ID__": html.escape(str(artifact_id or "local")),
        "__ARTIFACT_JSON__": json.dumps(str(artifact_id or "local"), ensure_ascii=False),
        "__SOURCE_BINDING__": html.escape(source_binding_label),
        "__SOURCE_DIGEST__": html.escape(source_digest),
        "__SOURCE_DIGEST_JSON__": json.dumps(source_digest, ensure_ascii=False),
        "__SOURCE_KEY__": json.dumps(source_digest[:24], ensure_ascii=False),
        "__ROW_COUNT__": str(len(rows)),
        "__CARDS__": cards,
        "__PIECE_EDITOR_TEMPLATE__": _piece_editor_template_markup(),
        "__SEED_JSON__": seed_json,
    }
    result = _PAGE_TEMPLATE
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def render_fen_manual_review_card(row: Mapping[str, Any]) -> str:
    index = max(0, int(row.get("review_index") or 1) - 1)
    fingerprint = html.escape(str(row.get("diagram_fingerprint") or ""), quote=True)
    board_image = str(row.get("board_crop_rel_path") or row.get("crop_rel_path") or "")
    context_image = str(row.get("context_crop_rel_path") or "")
    marker_image = str(row.get("marker_crop_rel_path") or "")
    marker_search_image = str(row.get("marker_search_crop_rel_path") or "")
    title = html.escape(str(row.get("caption") or row.get("diagram_id") or "Diagram"))
    diagram_id = html.escape(str(row.get("diagram_id") or ""))
    candidate = html.escape(str(row.get("fen_candidate") or "brak"))
    priority_value = row.get("review_priority")
    priority = int(priority_value) if priority_value is not None else 20
    conflict_badge = (
        '<span class="badge conflict">konflikt model / etykieta</span>'
        if row.get("model_conflict")
        else ""
    )
    blockers = ", ".join(str(value) for value in row.get("review_blockers") or [])
    blockers = blockers or str(row.get("review_reason") or "piece_labels_required")
    legacy_fen = str(row.get("legacy_verified_fen") or "")
    conflict_details = ""
    if row.get("model_conflict") and legacy_fen:
        conflict_details = (
            '<details><summary>Konflikt do rozstrzygnięcia</summary><div>'
            "Poprzednia etykieta: <code>"
            + html.escape(legacy_fen)
            + "</code><br>Nie kopiuj jej bez wzrokowego sprawdzenia.</div></details>"
        )
    context = html.escape(str(row.get("nearby_text") or "Brak kontekstu tekstowego."))
    board_figure = _fen_review_crop_figure(
        "1. Plansza 8×8",
        board_image,
        "Odczytaj figury z tego obrazu. Górny lewy róg edytora to a8, dolny prawy to h1.",
        css_class="board-figure",
        empty="Brak poprawnego cropa planszy.",
    )
    context_figure = _fen_review_crop_figure(
        "2A. Kontekst źródłowy",
        context_image,
        "Potwierdź podpis diagramu i położenie markera względem planszy.",
        css_class="context-figure",
        empty="Kontekst źródłowy niedostępny.",
    )
    marker_figure = _fen_review_crop_figure(
        "2B. Marker",
        marker_image,
        "△ oznacza ruch białych, ▼ oznacza ruch czarnych.",
        css_class="marker-figure",
        empty="Marker nie został wycięty.",
    )
    marker_search_figure = _fen_review_crop_figure(
        "2C. Strefa wyszukiwania",
        marker_search_image,
        "Sprawdź, czy marker jest pełny i należy do tej planszy.",
        css_class="marker-search-figure",
        empty="Strefa wyszukiwania niedostępna.",
    )
    detected_marker = html.escape(str(row.get("detected_marker_symbol") or "brak"))
    marker_status = html.escape(str(row.get("detected_marker_status") or "brak"))
    return f"""<article class="review-card" data-index="{index}" data-fingerprint="{fingerprint}" data-priority="{priority}" data-state="pending">
  <header class="card-head">
    <div><h2>{title}</h2><div class="meta">{diagram_id} · strona {int(row.get('page') or 0)} · pewność {float(row.get('confidence') or 0.0):.3f}</div></div>
    <div class="badges"><span class="badge">P{priority}</span>{conflict_badge}</div>
  </header>
  <div class="media-grid">
    <section class="board-panel" aria-label="Crop planszy">{board_figure}</section>
    <section class="evidence-panel" aria-label="Dowód markera">
      {context_figure}
      <div class="marker-evidence-grid">{marker_figure}{marker_search_figure}</div>
      <p class="machine-marker">Maszyna widzi: <b>{detected_marker}</b> · status: <code>{marker_status}</code>. Sprawdź wzrokowo.</p>
    </section>
  </div>
  <div class="body">
    <section class="machine"><div class="machine-head"><div><b>Sugestia maszyny, nie etykieta</b><code>{candidate}</code></div><button type="button" class="use-candidate">Przywróć sugestię</button></div><div class="meta">Źródło: {html.escape(str(row.get('candidate_source') or ''))} · bloker: {html.escape(blockers)}</div></section>
    {conflict_details}
    <details><summary>Kontekst tekstowy strony</summary><p>{context}</p></details>
    <form class="form" novalidate>
      <section class="label-block">
        <div class="label-intro"><span>1</span><div><b>Sprawdź crop i oznacz figury</b><small>Wybierz figurę, kliknij pole, popraw sugestię modelu.</small></div></div>
        <div class="field compact-field"><label for="crop-{index}">Jakość cropa planszy</label><select id="crop-{index}" name="board_crop_label"><option value="">Wybierz</option><option value="correct">Poprawny, pełne 64 pola</option><option value="cropped">Ucięty, ale wszystkie figury czytelne</option><option value="wrong">Zły diagram / false positive</option><option value="unreadable">Nieczytelny</option></select></div>
        <div class="piece-workspace">
          <div class="palette-panel"><h3>Figura do wstawienia</h3><div class="selected-piece">Wybrano: <strong class="selected-piece-label">Puste pole</strong></div><div class="piece-palette" role="toolbar" aria-label="Paleta figur" aria-busy="true"></div><p class="hint">Kliknij pole, aby wstawić wybraną figurę. Prawy przycisk czyści pole.</p></div>
          <div class="editor-panel"><div class="board-editor" role="grid" aria-label="Etykiety 64 pól planszy" aria-busy="true"><span class="editor-loading">Ładowanie siatki…</span></div><div class="editor-meta"><strong class="changed-count">0 zmian</strong><span>Zielona ramka = pole poprawione względem modelu</span></div><div class="editor-actions"><button type="button" class="use-candidate secondary">Przywróć sugestię</button><button type="button" class="clear-board secondary">Wyczyść planszę</button></div></div>
          <div class="fen-panel">
            <h3>Wynik automatyczny</h3>
            <div class="field"><label for="placement-{index}">Układ figur</label><textarea id="placement-{index}" name="manual_placement" class="fen-output" readonly rows="3"></textarea></div>
            <div class="field"><label for="fen-{index}">Pełny FEN</label><textarea id="fen-{index}" name="manual_fen" class="fen-output" readonly rows="4"></textarea><small>FEN powstaje z siatki i strony ruchu. Nie wpisujesz go ręcznie.</small></div>
            <div class="grid-check" aria-live="polite"></div>
            <label class="confirm-grid"><input type="checkbox" name="piece_labels_verified"> <span>Sprawdziłem wzrokowo wszystkie 64 pola</span></label>
          </div>
        </div>
      </section>
      <section class="label-block">
        <div class="label-intro"><span>2</span><div><b>Potwierdź marker i stronę ruchu</b><small>Marker jest niezależnym dowodem. Nie odczytuj go z cropa planszy.</small></div></div>
        <div class="field-grid marker-fields">
          <div class="field"><label for="marker-crop-{index}">Jakość dowodu markera</label><select id="marker-crop-{index}" name="marker_crop_label"><option value="">Wybierz</option><option value="clear">Marker wyraźny i przypisany poprawnie</option><option value="complete_no_marker">Pełny kontekst, markera faktycznie brak</option><option value="cropped">Marker lub strefa ucięta</option><option value="wrong">Marker należy do innego diagramu</option><option value="unreadable">Nieczytelny / szum</option></select></div>
          <div class="field"><label for="marker-{index}">Co jest widoczne?</label><select id="marker-{index}" name="manual_visible_marker"><option value="">Wybierz</option><option value="outline_triangle">△ pusty trójkąt — białe</option><option value="filled_triangle">▼ pełny trójkąt — czarne</option><option value="none_confirmed">Brak markera — potwierdzone</option><option value="unclear">Nieczytelny symbol</option><option value="multiple">Kilka możliwych markerów</option><option value="unavailable">Nie da się ocenić</option></select></div>
          <div class="field"><label for="side-{index}">Kto ma ruch?</label><select id="side-{index}" name="manual_side_to_move"><option value="">Nie wiadomo</option><option value="w">Białe (w)</option><option value="b">Czarne (b)</option></select></div>
          <div class="field"><label for="side-evidence-{index}">Skąd wiadomo?</label><select id="side-evidence-{index}" name="manual_side_evidence"><option value="">Wybierz dowód</option><option value="marker">Marker △ / ▼</option><option value="caption">Jawny podpis lub tekst strony</option><option value="verified_source">Zweryfikowane źródło pozycji</option><option value="unknown">Brak rozstrzygającego dowodu</option></select></div>
        </div>
      </section>
      <div class="closing-fields">
        <div class="field"><label for="status-{index}">Status etykiety</label><select id="status-{index}" name="label_status"><option value="needs_piece_labels">Figury do sprawdzenia</option><option value="verified">Zweryfikowane 64 pola + marker</option><option value="rejected">False positive / odrzucony</option><option value="unreadable">Plansza rzeczywiście nieczytelna</option></select></div>
        <div class="field"><label for="notes-{index}">Uwagi (opcjonalnie)</label><textarea id="notes-{index}" name="notes" rows="3" placeholder="Np. poprawiono czarnego gońca na f6; górny rząd ucięty..."></textarea></div>
      </div>
      <div class="validation" aria-live="polite" tabindex="-1">Sprawdź siatkę 8×8, następnie marker.</div>
    </form>
  </div>
</article>"""


def _piece_palette_markup() -> str:
    def button(value: str, label: str, glyph: str) -> str:
        value_attr = html.escape(value, quote=True)
        return (
            f'<button type="button" class="piece-choice" data-piece="{value_attr}" '
            f'aria-pressed="false" title="{html.escape(label, quote=True)}">'
            f'<span class="piece-glyph">{html.escape(glyph)}</span><span>{html.escape(label)}</span></button>'
        )

    empty = button(*PIECE_OPTIONS[0])
    white = "".join(button(*option) for option in PIECE_OPTIONS[1:7])
    black = "".join(button(*option) for option in PIECE_OPTIONS[7:])
    return (
        f'<div class="piece-empty">{empty}</div>'
        '<section class="piece-group" aria-label="Białe figury"><h4>Białe</h4>'
        f'<div class="piece-group-options">{white}</div></section>'
        '<section class="piece-group" aria-label="Czarne figury"><h4>Czarne</h4>'
        f'<div class="piece-group-options">{black}</div></section>'
    )


def _piece_grid_markup() -> str:
    squares = []
    for index in range(64):
        file_name = chr(ord("a") + index % 8)
        rank = 8 - index // 8
        square = f"{file_name}{rank}"
        shade = "light" if (index // 8 + index % 8) % 2 == 0 else "dark"
        rank_label = f'<span class="coord rank-coordinate">{rank}</span>' if index % 8 == 0 else ""
        file_label = f'<span class="coord file-coordinate">{file_name}</span>' if rank == 1 else ""
        squares.append(
            f'<button type="button" class="board-square {shade}" role="gridcell" '
            f'data-square-index="{index}" data-square="{square}" data-piece="" '
            f'aria-label="{square}: puste" title="{square}">{rank_label}{file_label}'
            f'<span class="placed-piece" aria-hidden="true"></span></button>'
        )
    return "".join(squares)


def _piece_editor_template_markup() -> str:
    return (
        '<template id="piece-editor-template">'
        f'<div data-template="palette">{_piece_palette_markup()}</div>'
        f'<div data-template="board">{_piece_grid_markup()}</div>'
        "</template>"
    )


def _fen_review_crop_figure(
    title: str,
    image_path: str,
    caption: str,
    *,
    css_class: str,
    empty: str,
) -> str:
    safe_title = html.escape(title)
    safe_caption = html.escape(caption)
    if not image_path:
        return (
            f'<figure class="crop-figure {html.escape(css_class, quote=True)} is-empty">'
            f'<h3>{safe_title}</h3><div class="crop-empty">{html.escape(empty)}</div>'
            f"<figcaption>{safe_caption}</figcaption></figure>"
        )
    safe_path = html.escape(image_path, quote=True)
    return (
        f'<figure class="crop-figure {html.escape(css_class, quote=True)}">'
        f'<h3>{safe_title}</h3><img loading="lazy" src="{safe_path}" alt="{safe_title}">'
        f"<figcaption>{safe_caption}</figcaption></figure>"
    )


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>KindleMaster — oznaczanie figur i markerów</title>
  <style>
    :root{--canvas:#e7e0d2;--paper:#fffdf7;--ink:#172019;--muted:#647067;--line:#d2c8b6;--line-strong:#9f9078;--forest:#1f5b46;--forest-dark:#174335;--brick:#a24630;--amber:#8b5a17;--red:#a8342a;--focus:#ef8a58;--light-square:#eee3c7;--dark-square:#819778;--radius:14px;--shadow:0 18px 46px rgba(53,43,27,.1)}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:radial-gradient(circle at 8% -4%,#fff8e6 0,transparent 34%),linear-gradient(135deg,#e5ddd0,#f6f1e7 54%,#dfe9e1);font-family:"Trebuchet MS","Segoe UI",sans-serif;line-height:1.48}
    button,input,select,textarea{font:inherit;border:1px solid var(--line);border-radius:10px}button{min-height:44px;padding:.66rem .9rem;color:var(--ink);background:#fff;font-weight:800;cursor:pointer}button:hover{border-color:var(--forest)}button.primary{color:#fff;border-color:var(--forest);background:var(--forest)}button.primary:hover{background:var(--forest-dark)}button.secondary{background:#f8f5ed}:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
    .shell{width:min(1560px,calc(100% - 32px));margin:0 auto;padding:30px 0 72px}.hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,440px);gap:28px;align-items:end;margin-bottom:20px}.eyebrow{color:var(--brick);font-size:.76rem;font-weight:900;letter-spacing:.11em;text-transform:uppercase}h1,h2,h3{margin:0;font-family:Georgia,"Times New Roman",serif}h1{max-width:900px;margin-top:7px;font-size:clamp(2.2rem,4.5vw,4.1rem);line-height:.98;letter-spacing:-.035em}.hero p{max-width:850px;margin:15px 0 0;color:var(--muted);font-size:1.02rem}.source{padding:13px 15px;border:1px solid var(--line);border-radius:var(--radius);background:rgba(255,253,247,.78);color:var(--muted);font-family:"Cascadia Mono",Consolas,monospace;font-size:.74rem;overflow-wrap:anywhere}
    .guide{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:16px}.guide-step{min-height:94px;padding:14px 15px;border:1px solid var(--line);border-radius:var(--radius);background:rgba(255,253,247,.82)}.guide-step b{display:block;margin-bottom:4px;font-family:Georgia,serif}.guide-step span{color:var(--muted);font-size:.84rem}
    .control-deck{position:sticky;top:10px;z-index:10;display:grid;gap:10px;margin-bottom:16px;padding:12px;border:1px solid var(--line);border-radius:var(--radius);background:rgba(255,253,247,.95);box-shadow:var(--shadow);backdrop-filter:blur(14px)}.metrics{display:grid;grid-template-columns:repeat(5,minmax(104px,1fr));gap:8px}.metric{min-height:62px;padding:9px 11px;border:1px solid var(--line);border-radius:10px;background:#fff}.metric strong{display:block;font-family:Georgia,serif;font-size:1.35rem;line-height:1.1}.metric span{display:block;margin-top:3px;color:var(--muted);font-size:.74rem}.controls{display:grid;grid-template-columns:minmax(180px,1.5fr) repeat(2,minmax(150px,.7fr)) minmax(170px,.8fr) auto;gap:8px;align-items:center}.controls input,.controls select{width:100%;min-height:44px;padding:0 11px;background:#fff}.toolbar-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end}.toolbar-actions button{white-space:nowrap}.save-state{display:flex;gap:8px;align-items:center;min-height:32px;padding:5px 9px;border-radius:9px;background:#f1eee6;color:var(--muted);font-size:.73rem;font-weight:800}.save-state::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--amber)}.save-state[data-state="saved"]::before{background:var(--forest)}.save-state[data-state="saving"]::before{background:var(--focus);animation:pulse 1s infinite}.save-state[data-state="error"]{color:var(--red)}.save-state[data-state="error"]::before{background:var(--red)}@keyframes pulse{50%{opacity:.35}}
    .review-grid{display:grid;gap:18px}.review-card{overflow:hidden;border:1px solid var(--line);border-radius:var(--radius);background:var(--paper);box-shadow:0 8px 24px rgba(58,48,32,.06)}.review-card[hidden]{display:none}.review-card[data-state="verified"]{border-color:#6c9e83;box-shadow:inset 4px 0 var(--forest),0 8px 24px rgba(58,48,32,.06)}.review-card[data-state="invalid"]{border-color:#d68c84;box-shadow:inset 4px 0 var(--red),0 8px 24px rgba(58,48,32,.06)}.card-head{display:flex;justify-content:space-between;gap:14px;padding:14px 16px 12px;border-bottom:1px solid var(--line)}.card-head h2{font-size:1.08rem;line-height:1.2}.meta{margin-top:4px;color:var(--muted);font-size:.78rem}.badges{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;align-content:flex-start}.badge{padding:4px 8px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--muted);font-size:.7rem;font-weight:900;white-space:nowrap}.badge.conflict{color:var(--red);border-color:#d99a93;background:#fff1ef}
    figure{margin:0}.media-grid{display:grid;grid-template-columns:minmax(0,1.12fr) minmax(360px,.88fr);gap:14px;padding:15px;background:linear-gradient(135deg,#ded7ca,#eee8dc)}.board-panel,.evidence-panel{min-width:0}.evidence-panel{display:grid;gap:11px}.marker-evidence-grid{display:grid;grid-template-columns:minmax(0,.72fr) minmax(0,1.28fr);gap:10px}.crop-figure{display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:7px}.crop-figure h3{font-size:.9rem}.crop-figure img{display:block;width:100%;object-fit:contain;border:1px solid var(--line-strong);border-radius:11px;background:#fff}.board-figure img{height:520px}.context-figure img{height:238px}.marker-figure img,.marker-search-figure img{height:170px}.crop-empty{display:grid;min-height:150px;place-items:center;padding:16px;border:1px dashed var(--line-strong);border-radius:11px;background:rgba(255,255,255,.64);color:var(--muted);text-align:center;font-size:.8rem}figcaption{color:var(--muted);font-size:.74rem}.machine-marker{margin:0;padding:8px 10px;border-left:3px solid var(--amber);background:rgba(255,253,247,.76);color:var(--muted);font-size:.76rem}
    .body{display:grid;gap:12px;padding:14px 15px 16px}.machine{padding:10px 11px;border:1px solid #dec49c;border-radius:10px;background:#fff7e8}.machine-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.machine-head>div{display:grid;gap:5px;min-width:0}.machine b{color:var(--amber);font-size:.76rem;text-transform:uppercase;letter-spacing:.05em}.machine code{display:block}code,.fen-output{color:#293930;font-family:"Cascadia Mono",Consolas,monospace;font-size:.77rem;overflow-wrap:anywhere;word-break:break-word}.machine button{flex:0 0 auto;min-height:38px;padding:.42rem .66rem;font-size:.76rem}details{border:1px solid var(--line);border-radius:10px;background:#fff}summary{padding:9px 11px;font-weight:800;cursor:pointer}details p,details div{margin:0;padding:0 11px 10px;color:var(--muted);font-size:.8rem;white-space:pre-wrap}
    .form{display:grid;gap:13px}.label-block{display:grid;gap:11px;padding-top:12px;border-top:1px solid var(--line)}.label-intro{display:flex;gap:10px;align-items:center}.label-intro>span{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;background:var(--forest);color:#fff;font-family:Georgia,serif;font-weight:800}.label-intro b,.label-intro small{display:block}.label-intro small{margin-top:2px;color:var(--muted);font-size:.74rem}.field{display:grid;gap:5px;min-width:0}.field label{color:#38463d;font-size:.78rem;font-weight:900}.field small,.hint{color:var(--muted);font-size:.73rem}.field input,.field select,.field textarea{width:100%;min-height:44px;padding:8px 10px;background:#fff}.field textarea{resize:vertical}.compact-field{max-width:430px}.field-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.closing-fields{display:grid;grid-template-columns:minmax(240px,.7fr) minmax(360px,1.3fr);gap:10px}
    .piece-workspace{display:grid;grid-template-columns:minmax(220px,250px) minmax(390px,520px) minmax(260px,1fr);gap:14px;align-items:start;padding:14px;border:1px solid var(--line);border-radius:12px;background:#f5f0e5}.palette-panel,.editor-panel,.fen-panel{min-width:0}.palette-panel h3,.fen-panel h3{margin-bottom:9px;font-size:.94rem}.selected-piece{margin-bottom:9px;padding:7px 9px;border-left:3px solid var(--forest);background:#fff;color:var(--muted);font-size:.72rem}.selected-piece strong{color:var(--forest-dark)}.piece-palette{display:grid;gap:8px}.piece-empty .piece-choice{width:100%}.piece-group{padding:8px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.55)}.piece-group h4{margin:0 0 6px;color:var(--muted);font-size:.66rem;letter-spacing:.08em;text-transform:uppercase}.piece-group-options{display:grid;grid-template-columns:1fr 1fr;gap:5px}.piece-choice{display:grid;grid-template-columns:30px 1fr;gap:5px;align-items:center;min-height:44px;padding:5px 7px;text-align:left;font-size:.67rem;line-height:1.15}.piece-choice[aria-pressed="true"]{color:#fff;border-color:var(--forest);background:var(--forest)}.piece-glyph{font-family:Georgia,"Times New Roman",serif;font-size:1.55rem;line-height:1;text-align:center}.hint{margin:8px 0 0}
    .board-editor{display:grid;grid-template-columns:repeat(8,1fr);width:100%;max-width:520px;aspect-ratio:1;border:2px solid #3e4a3f;border-radius:7px;overflow:hidden;box-shadow:0 8px 18px rgba(38,44,35,.12)}.editor-loading{grid-column:1/-1;display:grid;place-items:center;background:#e9e1cf;color:var(--muted);font-size:.78rem}.board-square{position:relative;display:grid;min-width:0;min-height:0;padding:0;place-items:center;border:0;border-radius:0;font-family:Georgia,"Times New Roman",serif}.board-square.light{background:var(--light-square)}.board-square.dark{background:var(--dark-square)}.board-square:hover{box-shadow:inset 0 0 0 3px rgba(31,91,70,.7)}.board-square[data-changed="true"]{box-shadow:inset 0 0 0 4px #18724f}.board-square[data-changed="true"]::after{content:"";position:absolute;right:4px;top:4px;width:7px;height:7px;border-radius:50%;background:#fff;box-shadow:0 0 0 2px #18724f}.board-square:focus-visible{z-index:2;outline:3px solid var(--focus);outline-offset:-3px}.placed-piece{font-size:clamp(1.45rem,3.2vw,2.75rem);line-height:1;text-shadow:0 1px 1px rgba(255,255,255,.65)}.coord{position:absolute;font-family:"Cascadia Mono",Consolas,monospace;font-size:clamp(.48rem,.75vw,.64rem);font-weight:900;opacity:.82}.rank-coordinate{top:2px;left:3px}.file-coordinate{right:3px;bottom:1px}.board-square.dark .coord{color:#fff}.editor-meta{display:flex;justify-content:space-between;gap:10px;margin-top:8px;color:var(--muted);font-size:.7rem}.editor-meta strong{color:var(--forest-dark)}.editor-actions{display:flex;gap:8px;margin-top:9px}.editor-actions button{flex:1;min-height:40px;padding:.45rem .55rem;font-size:.72rem}
    .fen-panel{display:grid;gap:10px}.fen-output{min-height:66px;background:#fffdf8!important;resize:none!important}.grid-check{padding:9px 10px;border-left:3px solid var(--amber);background:#fff8e9;color:var(--muted);font-size:.77rem}.grid-check.ok{border-color:var(--forest);background:#edf7f0;color:var(--forest-dark)}.grid-check.error{border-color:var(--red);background:#fff0ee;color:var(--red)}.confirm-grid{display:flex;gap:9px;align-items:flex-start;padding:11px;border:1px solid var(--line-strong);border-radius:10px;background:#fff;font-size:.82rem;font-weight:900;cursor:pointer}.confirm-grid input{flex:0 0 auto;width:20px;height:20px;margin:0;accent-color:var(--forest)}
    .validation{padding:10px 11px;border-left:4px solid var(--amber);border-radius:4px;background:#fff7e8;color:#6d4b1c;font-size:.8rem}.validation.ok{border-color:var(--forest);background:#edf7f0;color:var(--forest-dark)}.validation.error{border-color:var(--red);background:#fff0ee;color:var(--red)}.empty-state{padding:40px;border:1px dashed var(--line-strong);border-radius:var(--radius);background:var(--paper);text-align:center}.toast{position:fixed;right:20px;bottom:20px;z-index:30;max-width:min(480px,calc(100% - 40px));padding:12px 15px;border-radius:10px;background:#172019;color:#fff;box-shadow:var(--shadow);opacity:0;transform:translateY(10px);pointer-events:none;transition:.18s ease}.toast.show{opacity:1;transform:none}
    @media(max-width:1180px){.controls{grid-template-columns:1fr 1fr}.toolbar-actions{justify-content:flex-start}.piece-workspace{grid-template-columns:minmax(170px,220px) minmax(360px,1fr)}.fen-panel{grid-column:1/-1;grid-template-columns:1fr 1fr}.fen-panel h3,.fen-panel .grid-check,.fen-panel .confirm-grid{grid-column:1/-1}.field-grid{grid-template-columns:1fr 1fr}}
    @media(max-width:840px){.shell{width:min(100% - 20px,1560px);padding-top:18px}.hero,.media-grid{grid-template-columns:1fr}.guide{grid-template-columns:1fr}.control-deck{position:static}.metrics{grid-template-columns:repeat(2,1fr)}.controls{grid-template-columns:1fr}.toolbar-actions{display:grid;grid-template-columns:1fr 1fr}.toolbar-actions .primary{grid-column:1/-1}.save-state{grid-column:1/-1}.board-figure img{height:min(72vw,520px)}.context-figure img{height:min(58vw,300px)}.piece-workspace{grid-template-columns:1fr}.piece-palette{grid-template-columns:1fr 1fr}.piece-empty{grid-column:1/-1}.fen-panel{grid-column:auto;grid-template-columns:1fr}.fen-panel h3,.fen-panel .grid-check,.fen-panel .confirm-grid{grid-column:auto}.closing-fields,.field-grid{grid-template-columns:1fr}}
    @media(max-width:520px){h1{font-size:2.12rem}.card-head,.machine-head{align-items:flex-start;flex-direction:column}.marker-evidence-grid{grid-template-columns:1fr}.marker-figure img,.marker-search-figure img{height:190px}.piece-workspace{padding:8px}.piece-palette{grid-template-columns:1fr}.piece-empty{grid-column:auto}.placed-piece{font-size:clamp(1.2rem,8vw,2.15rem)}.coord{font-size:.43rem}.editor-meta{align-items:flex-start;flex-direction:column}.editor-actions{display:grid;grid-template-columns:1fr}.toolbar-actions{grid-template-columns:1fr}.toolbar-actions .primary{grid-column:auto}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero"><div><div class="eyebrow">KindleMaster · etykiety źródłowe</div><h1>Oznacz figury, nie zapis FEN</h1><p>Model wstępnie wypełnia planszę. Popraw błędne pola, potwierdź 64 pola i niezależnie sprawdź marker. Pełny FEN powstanie automatycznie.</p></div><div class="source"><b>Artefakt:</b> __ARTIFACT_ID__<br><b>Powiązanie:</b> __SOURCE_BINDING__<br><b>SHA:</b> __SOURCE_DIGEST__</div></header>
    <section class="guide" aria-label="Instrukcja"><div class="guide-step"><b>1. Crop planszy</b><span>Jeśli brakuje rzędu albo widzisz dwa diagramy, oznacz zły crop. Nie zgaduj.</span></div><div class="guide-step"><b>2. Siatka 8×8</b><span>Wybierz figurę i klikaj tylko pola różniące się od sugestii. Potem potwierdź 64 pola.</span></div><div class="guide-step"><b>3. Marker</b><span>△ = białe, ▼ = czarne. Strona ruchu trafia do wygenerowanego FEN.</span></div></section>
    <section class="control-deck" aria-label="Sterowanie kolejką"><div class="metrics"><div class="metric"><strong>__ROW_COUNT__</strong><span>diagramów</span></div><div class="metric"><strong id="metric-completed">0</strong><span>zakończonych</span></div><div class="metric"><strong id="metric-verified">0</strong><span>zweryfikowanych</span></div><div class="metric"><strong id="metric-excluded">0</strong><span>wykluczonych</span></div><div class="metric"><strong id="metric-pending">0</strong><span>pozostało</span></div><div class="metric"><strong id="metric-invalid">0</strong><span>wymaga poprawy</span></div></div><div class="controls"><input id="search" type="search" placeholder="Szukaj ID, strony lub FEN" aria-label="Szukaj diagramu"><select id="status-filter" aria-label="Filtr statusu"><option value="">Wszystkie statusy</option><option value="pending">Figury do sprawdzenia</option><option value="verified">Zweryfikowane</option><option value="closed">Wykluczone: odrzucone / nieczytelne</option><option value="invalid">Z błędem</option></select><select id="priority-filter" aria-label="Filtr priorytetu"><option value="">Wszystkie priorytety</option><option value="0">Najpierw: konflikt modelu</option><option value="10">Kandydat modelu</option><option value="20">Pozostałe</option></select><input id="reviewer" autocomplete="name" placeholder="Kto oznacza? np. PM" aria-label="Identyfikator osoby oznaczającej"><div class="toolbar-actions"><button type="button" id="next-pending">Następny</button><button type="button" id="import-jsonl">Wczytaj JSONL</button><button type="button" id="save-server">Zapisz na stronie</button><button type="button" class="primary" id="export-jsonl">Eksportuj JSONL</button><span class="save-state" id="save-state" data-state="loading">Łączenie z zapisem…</span></div><input id="import-file" type="file" accept=".jsonl,.ndjson,application/x-ndjson" hidden></div></section>
    <section class="review-grid">__CARDS__</section>
  </main>
  <div class="toast" id="toast" role="status" aria-live="polite"></div>
  __PIECE_EDITOR_TEMPLATE__
  <script type="application/json" id="seed">__SEED_JSON__</script>
  <script>
    const seed = JSON.parse(document.getElementById('seed').textContent);
    const artifactId = __ARTIFACT_JSON__;
    const sourceDigest = __SOURCE_DIGEST_JSON__;
    const stateKey = 'kindlemaster.fen-manual.piece-grid-v2.' + (__SOURCE_KEY__ || artifactId || 'local');
    const reviewerKey = stateKey + '.reviewer';
    const localModifiedKey = stateKey + '.modifiedAt';
    const serverProgressUrl = artifactId && artifactId !== 'local' ? `/convert/artifact/${encodeURIComponent(artifactId)}/chess_fen_review_progress` : '';
    const pieceGlyphs = {'':'',K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
    const pieceNames = {'':'puste',K:'biały król',Q:'biały hetman',R:'biała wieża',B:'biały goniec',N:'biały skoczek',P:'biały pion',k:'czarny król',q:'czarny hetman',r:'czarna wieża',b:'czarny goniec',n:'czarny skoczek',p:'czarny pion'};
    const cards = [...document.querySelectorAll('.review-card')];
    const terminal = status => ['verified','rejected','unreadable'].includes(status);
    const nowIso = () => new Date().toISOString();
    let state = {};
    let saveTimer = 0;
    let serverSavePending = false;
    let serverSaveInFlight = false;
    let stateRevision = 0;
    try { state = JSON.parse(localStorage.getItem(stateKey) || '{}'); } catch { state = {}; }
    const reviewer = document.getElementById('reviewer');
    const saveState = document.getElementById('save-state');
    reviewer.value = localStorage.getItem(reviewerKey) || '';

    const form = card => card.querySelector('.form');
    const seedRow = card => seed[Number(card.dataset.index)];
    const savedRow = card => state[card.dataset.fingerprint] || {};
    function placementToCells(value) {
      const placement = String(value || '').trim().split(/\s+/)[0];
      const ranks = placement.split('/');
      if (ranks.length !== 8) return null;
      const cells = [];
      for (const rank of ranks) {
        let width = 0;
        for (const token of rank) {
          if (/^[1-8]$/.test(token)) { const count = Number(token); cells.push(...Array(count).fill('')); width += count; }
          else if (/^[prnbqkPRNBQK]$/.test(token)) { cells.push(token); width += 1; }
          else return null;
        }
        if (width !== 8) return null;
      }
      return cells.length === 64 ? cells : null;
    }
    function cellsToPlacement(cells) {
      const ranks = [];
      for (let rank = 0; rank < 8; rank += 1) {
        let empty = 0, output = '';
        for (let file = 0; file < 8; file += 1) {
          const piece = cells[rank * 8 + file] || '';
          if (!piece) { empty += 1; continue; }
          if (empty) { output += String(empty); empty = 0; }
          output += piece;
        }
        if (empty) output += String(empty);
        ranks.push(output || '8');
      }
      return ranks.join('/');
    }
    function candidateCells(card) {
      return placementToCells(seedRow(card).fen_candidate) || Array(64).fill('');
    }
    function sideFromMarker(value) {
      return {outline_triangle:'w',filled_triangle:'b'}[value] || '';
    }
    function manualLabel(row) {
      if (row.label_status === 'rejected') return 'false_positive';
      if (row.label_status === 'unreadable') return 'uncertain';
      if (row.label_status !== 'verified') return 'needs_piece_labels';
      return row.board_crop_label === 'cropped' ? 'cropped_diagram' : 'correct_diagram';
    }
    function rowFor(card) {
      const base = seedRow(card), f = form(card), previous = savedRow(card);
      const status = f.label_status.value;
      const cells = [...(card._squareLabels || candidateCells(card))];
      const placement = cellsToPlacement(cells);
      const side = f.manual_side_to_move.value;
      const manualFen = ['w','b'].includes(side) ? `${placement} ${side} - - 0 1` : '';
      const pieceLabelsVerified = Boolean(f.piece_labels_verified.checked);
      const row = {
        ...base,
        schema:'kindlemaster.fen_manual_review.row.v4',
        review_contract:'source_bound_piece_grid_v2',
        square_labels:cells,
        piece_labels_verified:pieceLabelsVerified,
        fen_human_verified:status === 'verified' && pieceLabelsVerified,
        piece_labels_source:pieceLabelsVerified ? 'human_visual_64_square_grid' : 'model_candidate_draft',
        manual_placement:placement,
        manual_fen:manualFen,
        manual_side_to_move:side,
        manual_side_evidence:f.manual_side_evidence.value,
        manual_visible_marker:f.manual_visible_marker.value,
        board_crop_label:f.board_crop_label.value,
        marker_crop_label:f.marker_crop_label.value,
        label_status:status,
        human_verified:terminal(status),
        verified_by:terminal(status) ? (reviewer.value.trim() || previous.verified_by || '') : '',
        verified_at:terminal(status) ? (previous.verified_at || nowIso()) : '',
        verification_source:terminal(status) ? 'human_visual_piece_grid_and_marker' : '',
        label_provenance:terminal(status) ? 'human_visual_source_bound_piece_grid_review' : '',
        notes:f.notes.value.trim()
      };
      row.manual_label = manualLabel(row);
      return row;
    }
    function editable(row) {
      const fields = ['manual_side_to_move','manual_side_evidence','manual_visible_marker','board_crop_label','marker_crop_label','label_status','verified_by','verified_at','notes','piece_labels_source'];
      const output = Object.fromEntries(fields.map(key => [key,row[key] ?? '']));
      output.square_labels = Array.isArray(row.square_labels) ? [...row.square_labels] : [];
      output.piece_labels_verified = row.piece_labels_verified === true;
      output.fen_human_verified = row.fen_human_verified === true;
      return output;
    }
    function placementCheck(cells) {
      if (!Array.isArray(cells) || cells.length !== 64) return {ok:false,message:'Siatka musi zawierać 64 pola.'};
      if (cells.some(piece => !Object.prototype.hasOwnProperty.call(pieceGlyphs,piece))) return {ok:false,message:'Siatka zawiera nieznaną klasę figury.'};
      const whiteKings = cells.filter(piece => piece === 'K').length;
      const blackKings = cells.filter(piece => piece === 'k').length;
      if (whiteKings !== 1 || blackKings !== 1) return {ok:false,message:'Ustaw dokładnie jednego białego i jednego czarnego króla.'};
      if ([...cells.slice(0,8),...cells.slice(56,64)].some(piece => piece === 'P' || piece === 'p')) return {ok:false,message:'Pion nie może znajdować się w pierwszym ani ósmym rzędzie.'};
      return {ok:true,message:'Układ figur ma poprawną strukturę.'};
    }
    function fenCheck(value) {
      const parts = String(value || '').trim().split(/\s+/);
      if (parts.length !== 6) return {ok:false,message:'FEN zostanie utworzony po wskazaniu strony ruchu.'};
      const cells = placementToCells(parts[0]);
      const placement = placementCheck(cells);
      if (!placement.ok) return placement;
      if (!['w','b'].includes(parts[1])) return {ok:false,message:'Strona ruchu musi być w albo b.'};
      if (parts[2] !== '-' || parts[3] !== '-' || parts[4] !== '0' || parts[5] !== '1') return {ok:false,message:'Techniczne pola FEN mają niepoprawny format.'};
      return {ok:true,side:parts[1],message:'FEN jest gotowy do walidacji backendu.'};
    }
    function validateRow(row) {
      const errors = [];
      if (row.label_status === 'verified') {
        const placement = placementCheck(row.square_labels), fen = fenCheck(row.manual_fen), markerSide = sideFromMarker(row.manual_visible_marker);
        if (!placement.ok) errors.push(placement.message);
        if (!row.piece_labels_verified || !row.fen_human_verified) errors.push('Potwierdź wzrokowe sprawdzenie wszystkich 64 pól.');
        if (!fen.ok) errors.push(fen.message);
        if (!['correct','cropped'].includes(row.board_crop_label)) errors.push('Crop planszy musi być poprawny albo czytelny mimo ucięcia.');
        if (!['clear','complete_no_marker'].includes(row.marker_crop_label)) errors.push('Dowód markera musi być czytelny albo potwierdzać jego brak.');
        if (!row.manual_visible_marker) errors.push('Oznacz symbol na cropie markera.');
        if (row.marker_crop_label === 'complete_no_marker' && row.manual_visible_marker !== 'none_confirmed') errors.push('Pełny kontekst bez markera wymaga etykiety „Brak markera”.');
        if (row.manual_visible_marker === 'none_confirmed' && row.marker_crop_label !== 'complete_no_marker') errors.push('Brak markera wolno potwierdzić tylko z pełnego kontekstu.');
        if (['unclear','multiple','unavailable'].includes(row.manual_visible_marker)) errors.push('Niejednoznacznego markera nie można zweryfikować.');
        if (!['w','b'].includes(row.manual_side_to_move)) errors.push('Wskaż stronę mającą ruch.');
        if (!['marker','caption','verified_source'].includes(row.manual_side_evidence)) errors.push('Wskaż rozstrzygające źródło strony ruchu.');
        if (markerSide && row.manual_side_to_move !== markerSide) errors.push('Marker nie zgadza się ze stroną ruchu.');
        if (row.manual_side_evidence === 'marker' && !markerSide) errors.push('Dowód „marker” wymaga etykiety △ albo ▼.');
        if (fen.ok && row.manual_side_to_move !== fen.side) errors.push('Strona ruchu nie zgadza się z FEN.');
        if (!row.verified_by) errors.push('Podaj identyfikator osoby oznaczającej.');
      } else if (terminal(row.label_status) && !row.verified_by) errors.push('Podaj identyfikator osoby oznaczającej.');
      return {ok:errors.length === 0,errors};
    }
    function renderBoard(card) {
      const cells = card._squareLabels || candidateCells(card);
      const candidate = candidateCells(card);
      let changed = 0;
      for (const square of card.querySelectorAll('.board-square')) {
        const index = Number(square.dataset.squareIndex), piece = cells[index] || '';
        square.dataset.piece = piece;
        square.dataset.changed = String(piece !== (candidate[index] || ''));
        if (square.dataset.changed === 'true') changed += 1;
        square.querySelector('.placed-piece').textContent = pieceGlyphs[piece] || '';
        square.setAttribute('aria-label',`${square.dataset.square}: ${pieceNames[piece] || 'nieznana figura'}`);
      }
      for (const button of card.querySelectorAll('.piece-choice')) button.setAttribute('aria-pressed',String(button.dataset.piece === (card._selectedPiece ?? '')));
      card.querySelector('.selected-piece-label').textContent = pieceNames[card._selectedPiece ?? ''] || 'nieznana figura';
      card.querySelector('.changed-count').textContent = `${changed} ${changed === 1 ? 'zmiana' : changed >= 2 && changed <= 4 ? 'zmiany' : 'zmian'}`;
      const row = rowFor(card), check = placementCheck(row.square_labels), box = card.querySelector('.grid-check');
      form(card).manual_placement.value = row.manual_placement;
      form(card).manual_fen.value = row.manual_fen;
      box.className = 'grid-check ' + (check.ok ? 'ok' : 'error');
      box.textContent = check.message;
    }
    function hydrateEditor(card) {
      if (card.dataset.editorReady === 'true') return;
      const template = document.getElementById('piece-editor-template').content;
      const paletteSource = template.querySelector('[data-template="palette"]');
      const boardSource = template.querySelector('[data-template="board"]');
      const palette = card.querySelector('.piece-palette');
      const board = card.querySelector('.board-editor');
      palette.replaceChildren(...[...paletteSource.children].map(node => node.cloneNode(true)));
      board.replaceChildren(...[...boardSource.children].map(node => node.cloneNode(true)));
      palette.setAttribute('aria-busy','false');
      board.setAttribute('aria-busy','false');
      card.dataset.editorReady = 'true';
      for (const choice of card.querySelectorAll('.piece-choice')) choice.addEventListener('click',()=>{card._selectedPiece=choice.dataset.piece||'';renderBoard(card)});
      for (const square of card.querySelectorAll('.board-square')) {
        square.addEventListener('click',()=>{card._squareLabels[Number(square.dataset.squareIndex)]=card._selectedPiece||'';resetGridVerification(card);save(card)});
        square.addEventListener('contextmenu',event=>{event.preventDefault();card._squareLabels[Number(square.dataset.squareIndex)]='';resetGridVerification(card);save(card)});
      }
      renderBoard(card);
    }
    function normalizeImported(row,card) {
      const output = {...row};
      const hasGrid = Array.isArray(row.square_labels) && row.square_labels.length === 64;
      if (!hasGrid) {
        output.square_labels = candidateCells(card);
        output.piece_labels_verified = false;
        output.fen_human_verified = false;
        output.piece_labels_source = 'model_candidate_draft';
        if (row.label_status === 'rejected' || row.board_crop_label === 'wrong') output.label_status = 'rejected';
        else if (row.board_crop_label === 'unreadable') output.label_status = 'unreadable';
        else output.label_status = 'needs_piece_labels';
      }
      if (!['needs_piece_labels','verified','rejected','unreadable'].includes(output.label_status)) output.label_status = 'needs_piece_labels';
      if (output.label_status === 'verified' && (!output.piece_labels_verified || !output.fen_human_verified)) output.label_status = 'needs_piece_labels';
      return output;
    }
    function apply(card,values) {
      const f = form(card), migrated = normalizeImported(values || {},card);
      for (const key of ['manual_side_to_move','manual_side_evidence','manual_visible_marker','board_crop_label','marker_crop_label','label_status','notes']) if (migrated[key] !== undefined && f.elements[key]) f.elements[key].value = migrated[key];
      card._squareLabels = Array.isArray(migrated.square_labels) && migrated.square_labels.length === 64 ? [...migrated.square_labels] : candidateCells(card);
      card._selectedPiece = '';
      f.piece_labels_verified.checked = migrated.piece_labels_verified === true;
      renderBoard(card);
      updateCard(card);
    }
    function save(card) {
      const row = rowFor(card);
      state[card.dataset.fingerprint] = editable(row);
      localStorage.setItem(stateKey,JSON.stringify(state));
      localStorage.setItem(localModifiedKey,nowIso());
      stateRevision += 1;
      renderBoard(card);
      updateCard(card);
      refresh(false);
      scheduleServerSave();
    }
    function updateCard(card) {
      const row = rowFor(card), result = validateRow(row), box = card.querySelector('.validation');
      let value = 'pending';
      if (row.label_status === 'verified') value = result.ok ? 'verified' : 'invalid';
      else if (terminal(row.label_status)) value = result.ok ? 'closed' : 'invalid';
      card.dataset.state = value;
      box.className = 'validation ' + (result.ok && row.label_status === 'verified' ? 'ok' : result.ok ? '' : 'error');
      box.textContent = result.errors[0] || (row.label_status === 'verified' ? 'Komplet gotowy do eksportu i walidacji backendu.' : 'Sprawdź siatkę 8×8, następnie marker.');
    }
    function refresh(runFilter=true) {
      let verified=0,excluded=0,pending=0,invalid=0;
      for (const card of cards) {
        updateCard(card);
        if (card.dataset.state === 'verified') verified += 1;
        else if (card.dataset.state === 'closed') excluded += 1;
        else if (card.dataset.state === 'invalid') invalid += 1;
        else pending += 1;
      }
      document.getElementById('metric-completed').textContent=verified+excluded;
      document.getElementById('metric-verified').textContent=verified;
      document.getElementById('metric-excluded').textContent=excluded;
      document.getElementById('metric-pending').textContent=pending;
      document.getElementById('metric-invalid').textContent=invalid;
      if (runFilter) filterCards();
    }
    function filterCards() {
      const query=document.getElementById('search').value.trim().toLowerCase(),status=document.getElementById('status-filter').value,priority=document.getElementById('priority-filter').value;
      for (const card of cards) {
        const row=rowFor(card),haystack=[row.diagram_id,row.page,row.caption,row.manual_fen,row.fen_candidate].join(' ').toLowerCase(),statusOk=!status||card.dataset.state===status||(status==='pending'&&card.dataset.state==='invalid');
        card.hidden=!((!query||haystack.includes(query))&&statusOk&&(!priority||String(row.review_priority)===priority));
      }
    }
    function toast(message) { const node=document.getElementById('toast');node.textContent=message;node.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>node.classList.remove('show'),3600); }
    function recordWord(count) { if(count===1)return'rekord';const last=count%10,lastTwo=count%100;return last>=2&&last<=4&&!(lastTwo>=12&&lastTwo<=14)?'rekordy':'rekordów'; }
    function nextPending() { const card=cards.find(item=>!item.hidden&&['pending','invalid'].includes(item.dataset.state))||cards.find(item=>['pending','invalid'].includes(item.dataset.state));if(!card){toast('Wszystkie rekordy mają status końcowy.');return}card.hidden=false;hydrateEditor(card);card.scrollIntoView({behavior:'smooth',block:'start'});card.querySelector('.board-square').focus(); }
    function resetGridVerification(card) { const f=form(card);f.piece_labels_verified.checked=false;if(f.label_status.value==='verified')f.label_status.value='needs_piece_labels'; }
    function loadCandidate(card) { card._squareLabels=candidateCells(card);resetGridVerification(card);save(card);toast('Przywrócono sugestię modelu. Sprawdź wszystkie 64 pola.'); }
    function setSaveState(value,message) { saveState.dataset.state=value;saveState.textContent=message; }
    function scheduleServerSave() {
      serverSavePending = true;
      if (!serverProgressUrl) { setSaveState('local','Zapis lokalny; serwer niedostępny'); return; }
      setSaveState('pending','Zmiany czekają na zapis');
      clearTimeout(saveTimer);
      saveTimer = setTimeout(()=>saveAllToServer(false),1200);
    }
    async function saveAllToServer(showToast=true) {
      if (!serverProgressUrl || serverSaveInFlight) return false;
      clearTimeout(saveTimer);
      serverSaveInFlight = true;
      const savingRevision = stateRevision;
      setSaveState('saving','Zapisywanie na serwerze…');
      try {
        const response = await fetch(serverProgressUrl,{
          method:'PUT',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({source_digest:sourceDigest,rows:cards.map(rowFor)})
        });
        const payload = await response.json().catch(()=>({}));
        if (!response.ok || payload.success !== true) throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
        serverSavePending = savingRevision !== stateRevision;
        localStorage.setItem(localModifiedKey,payload.saved_at || nowIso());
        if (serverSavePending) scheduleServerSave();
        else setSaveState('saved',`Zapisano ${new Date(payload.saved_at || Date.now()).toLocaleTimeString('pl-PL',{hour:'2-digit',minute:'2-digit'})}`);
        if (showToast) toast(`Postęp zapisany: ${payload.summary?.completed || 0} zakończonych, ${payload.summary?.verified || 0} zweryfikowanych, ${payload.summary?.pending || 0} oczekujących.`);
        return true;
      } catch (error) {
        serverSavePending = true;
        setSaveState('error','Błąd zapisu; dane są w tej przeglądarce');
        if (showToast) toast(`Nie udało się zapisać na serwerze: ${error.message}`);
        return false;
      } finally {
        serverSaveInFlight = false;
      }
    }
    async function loadServerProgress() {
      if (!serverProgressUrl) { setSaveState('local','Zapis lokalny; użyj eksportu JSONL'); return; }
      setSaveState('loading','Wczytywanie zapisu z serwera…');
      try {
        const response = await fetch(serverProgressUrl,{headers:{Accept:'application/json'}});
        const payload = await response.json().catch(()=>({}));
        if (!response.ok || payload.success !== true || !Array.isArray(payload.rows)) throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
        const localModifiedAt = Date.parse(localStorage.getItem(localModifiedKey) || '') || 0;
        const serverSavedAt = Date.parse(payload.saved_at || '') || 0;
        const keepLocal = Object.keys(state).length > 0 && localModifiedAt > serverSavedAt;
        if (!keepLocal) {
          state = {};
          for (const row of payload.rows) {
            const card=cards.find(item=>item.dataset.fingerprint===row.diagram_fingerprint);
            if (card) state[card.dataset.fingerprint]=editable(normalizeImported(row,card));
          }
          localStorage.setItem(stateKey,JSON.stringify(state));
          if (payload.saved_at) localStorage.setItem(localModifiedKey,payload.saved_at);
          if (!reviewer.value) {
            const serverReviewer=payload.rows.map(row=>String(row.verified_by||'').trim()).find(Boolean);
            if (serverReviewer) { reviewer.value=serverReviewer;localStorage.setItem(reviewerKey,serverReviewer); }
          }
          for (const card of cards) apply(card,savedRow(card));
          refresh();
        } else {
          serverSavePending = true;
          scheduleServerSave();
        }
        if (!serverSavePending) setSaveState('saved',payload.saved_at ? `Wczytano zapis ${new Date(payload.saved_at).toLocaleString('pl-PL')}` : 'Gotowe do zapisu na serwerze');
      } catch (error) {
        setSaveState('error','Serwer zapisu niedostępny; działa kopia lokalna');
      }
    }

    for (const card of cards) {
      apply(card,savedRow(card));
      const f=form(card);
      for (const button of card.querySelectorAll('.use-candidate')) button.addEventListener('click',()=>loadCandidate(card));
      card.querySelector('.clear-board').addEventListener('click',()=>{card._squareLabels=Array(64).fill('');resetGridVerification(card);save(card);toast('Wyczyszczono siatkę.');});
      f.addEventListener('input',()=>save(card));
      f.addEventListener('change',event=>{
        if(event.target.name==='manual_visible_marker'){
          const side=sideFromMarker(event.target.value);
          if(side){f.manual_side_to_move.value=side;f.manual_side_evidence.value='marker';if(!f.marker_crop_label.value)f.marker_crop_label.value='clear';}
          else if(event.target.value==='none_confirmed'){if(!f.marker_crop_label.value)f.marker_crop_label.value='complete_no_marker';if(f.manual_side_evidence.value==='marker')f.manual_side_evidence.value='';}
        }
        save(card);
      });
    }
    if ('IntersectionObserver' in window) {
      const editorObserver = new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){hydrateEditor(entry.target);editorObserver.unobserve(entry.target);}}, {rootMargin:'800px 0px'});
      for (const card of cards) editorObserver.observe(card);
    } else {
      for (const card of cards.slice(0,3)) hydrateEditor(card);
    }
    reviewer.addEventListener('input',()=>{localStorage.setItem(reviewerKey,reviewer.value.trim());localStorage.setItem(localModifiedKey,nowIso());stateRevision+=1;refresh();scheduleServerSave();});
    for(const id of ['search','status-filter','priority-filter'])document.getElementById(id).addEventListener('input',filterCards);
    document.getElementById('next-pending').addEventListener('click',nextPending);
    document.getElementById('save-server').addEventListener('click',()=>saveAllToServer(true));
    document.addEventListener('keydown',event=>{if(event.altKey&&event.key.toLowerCase()==='n'){event.preventDefault();nextPending();}});
    document.getElementById('import-jsonl').addEventListener('click',()=>document.getElementById('import-file').click());
    document.getElementById('import-file').addEventListener('change',async event=>{
      const file=event.target.files[0];if(!file)return;let imported=0,rejected=0,migrated=0;
      for(const line of(await file.text()).split(/\r?\n/)){
        if(!line.trim())continue;
        try{
          const row=JSON.parse(line),card=cards.find(item=>item.dataset.fingerprint===row.diagram_fingerprint)||cards.find(item=>seedRow(item).diagram_id===row.diagram_id);
          if(!card||(row.crop_sha256&&row.crop_sha256!==seedRow(card).crop_sha256)){rejected+=1;continue;}
          const normalized=normalizeImported(row,card);if(!Array.isArray(row.square_labels)||row.square_labels.length!==64)migrated+=1;
          state[card.dataset.fingerprint]=editable(normalized);imported+=1;
        }catch{rejected+=1;}
      }
      localStorage.setItem(stateKey,JSON.stringify(state));localStorage.setItem(localModifiedKey,nowIso());stateRevision+=1;for(const card of cards)apply(card,savedRow(card));refresh();
      scheduleServerSave();toast(`Wczytano ${imported}; stare rekordy do ponownego sprawdzenia siatki: ${migrated}; pominięto ${rejected}.`);event.target.value='';
    });
    document.getElementById('export-jsonl').addEventListener('click',()=>{
      const rows=cards.map(rowFor),invalid=rows.map((row,index)=>({index,result:validateRow(row)})).filter(item=>!item.result.ok&&terminal(rows[item.index].label_status));
      if(invalid.length){const card=cards[invalid[0].index];card.hidden=false;card.scrollIntoView({behavior:'smooth',block:'center'});toast(`Popraw ${invalid.length} ${recordWord(invalid.length)} ze statusem końcowym przed eksportem.`);return;}
      const jsonl=rows.map(row=>JSON.stringify(row)).join('\n')+'\n',url=URL.createObjectURL(new Blob([jsonl],{type:'application/x-ndjson'})),link=document.createElement('a');link.href=url;link.download='fen_piece_grid_'+(artifactId||'review')+'.filled.jsonl';link.click();URL.revokeObjectURL(url);toast(`Wyeksportowano ${rows.length} rekordów, w tym ${rows.filter(row=>terminal(row.label_status)).length} zakończonych.`);
    });
    refresh();
    loadServerProgress();
    window.addEventListener('beforeunload',event=>{if(serverSavePending){event.preventDefault();event.returnValue='';}});
  </script>
</body>
</html>
"""
