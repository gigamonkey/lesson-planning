// CodeMirror 6 editor for the course outline's plan.md. Bundled by esbuild into
// static/editor.bundle.js (committed, so the Python runtime stays node-free); see
// package.json's `build` script. Exposed to the page as `window.LessonEditor`.
import { EditorState, Compartment } from "@codemirror/state";
import {
  EditorView, keymap, lineNumbers, highlightActiveLine,
  highlightActiveLineGutter, drawSelection, rectangularSelection, crosshairCursor,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { markdown } from "@codemirror/lang-markdown";
import {
  syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching,
} from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { vim } from "@replit/codemirror-vim";
import { emacs } from "@replit/codemirror-emacs";

// Token styling for the markdown buffer. This replaces CM's defaultHighlightStyle
// (which underlines headings AND links) -- headings/links stay distinct without
// underlines. Tweak here to restyle syntax; use `editorTheme` below for chrome.
const highlightStyle = HighlightStyle.define([
  { tag: t.heading, fontWeight: "600" },
  { tag: t.strong, fontWeight: "700" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through" },
  { tag: [t.link, t.url], color: "#2b6cb0" },
  { tag: t.monospace, color: "#b7791f" },
  { tag: [t.meta, t.processingInstruction], color: "#999" },
  { tag: t.contentSeparator, color: "#999" },
]);

// Editor chrome (background, cursor, selection, font). Extend this to restyle the
// editor itself rather than the syntax tokens.
const editorTheme = EditorView.theme({
  "&": { backgroundColor: "#fff" },
  ".cm-content": { caretColor: "#222" },
});

// The keymap scheme (default / vim / emacs) lives in a compartment so the
// selector can swap it live, with no editor rebuild.
const keymapCompartment = new Compartment();
const KEYMAP_KEY = "lesson-editor-keymap";

function keymapExtension(name) {
  if (name === "vim") return vim();
  if (name === "emacs") return emacs();
  return []; // "default": only the base keymaps installed below
}

// Mount the editor on `parent`, seeded with `doc`, mirroring the live document
// into the hidden `textarea` so a normal form submit carries the edited markdown.
export function mount({ parent, textarea, doc }) {
  const saved = localStorage.getItem(KEYMAP_KEY) || "default";

  const base = [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    history(),
    drawSelection(),
    rectangularSelection(),
    crosshairCursor(),
    indentOnInput(),
    bracketMatching(),
    highlightSelectionMatches(),
    syntaxHighlighting(highlightStyle, { fallback: true }),
    editorTheme,
    markdown(),
    EditorView.lineWrapping,
    keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
    EditorView.updateListener.of((u) => {
      if (u.docChanged && textarea) textarea.value = u.state.doc.toString();
    }),
  ];

  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: doc || "",
      // The vim/emacs keymap goes first so its bindings take precedence over the base.
      extensions: [keymapCompartment.of(keymapExtension(saved)), base],
    }),
  });

  // Seed the textarea with the initial value too, so a save with no edits still works.
  if (textarea) textarea.value = view.state.doc.toString();

  const select = document.getElementById("keymap-select");
  if (select) {
    select.value = saved;
    select.addEventListener("change", () => {
      localStorage.setItem(KEYMAP_KEY, select.value);
      view.dispatch({ effects: keymapCompartment.reconfigure(keymapExtension(select.value)) });
      view.focus();
    });
  }

  view.focus();
  return view;
}
