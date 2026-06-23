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
  syntaxHighlighting, defaultHighlightStyle, indentOnInput, bracketMatching,
} from "@codemirror/language";
import { vim } from "@replit/codemirror-vim";
import { emacs } from "@replit/codemirror-emacs";

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
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
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
