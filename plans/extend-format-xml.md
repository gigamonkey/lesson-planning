# Extend format-xml.py to cover all format-ptx.py capabilities

## Goal

Make `format-xml.py` capable of doing everything `format-ptx.py` does, without
hardcoding anything specific to PreTeXt or any other XML format. After this
work, `format-ptx.py` could be replaced by `format-xml.py` plus a PreTeXt
config file.

## Gaps to close

Capabilities in `format-ptx.py` that `format-xml.py` currently lacks:

1. **`one_line` element category** — elements like `<cline>` that preserve
   whitespace but render on a single line (no newline before close tag).
   `render_with_whitespace` in ptx checks `is_oneline()`; xml doesn't.

2. **Compound code elements** — ptx's `render_program` handles a parent element
   (`<program>`) whose children (`<preamble>`, `<code>`, `<postamble>`) are all
   code blocks that should share a common dedentation. xml's `render_code` only
   handles leaf code elements.

3. **Conditional element classification** — ptx's `is_pre_in_datafile` treats
   `<pre>` as verbatim only when it's inside `<datafile>` and lacks a `source`
   attribute. xml has no way to classify elements conditionally based on parent
   or attributes.

4. **External code formatters** — ptx's `maybe_formatted` pipes code through
   `google-java-format`. xml has no formatter integration.

5. **Dedentation parameter on verbatim rendering** — ptx's
   `render_verbatim_text` accepts an explicit dedentation amount (used by
   compound code elements). xml's `render_code` always fully dedents.

## Plan

### 1. Add `one_line` to the config

Add a new category `"one_line"` (list of tag names). These elements preserve
whitespace like `preserve_whitespace` but render without newlines around the
closing tag. In `render_with_whitespace`, check `is_oneline(elem, cfg)` to
decide whether to add newlines, matching ptx's logic.

Config addition:
```json
{ "one_line": ["cline"] }
```

### 2. Add compound code elements

Add a config key `"compound_code"` that maps a parent tag to a list of child
tags that should be treated as related code blocks with shared dedentation.

Config addition:
```json
{
  "compound_code": {
    "program": {
      "code_children": ["preamble", "code", "postamble"]
    }
  }
}
```

When serializing an element whose tag is in `compound_code`:
- If it has no children, render it as a simple code element.
- Otherwise, find children matching `code_children` and compute their common
  dedentation, then render each with `render_verbatim_text` using that shared
  amount. Render any other children normally.

This replaces ptx's `render_program` with a generic version driven by config.

### 3. Add conditional classification rules

Add an optional `"rules"` list to the config. Each rule overrides an element's
category based on context (parent tag, attributes). Rules are checked in order;
first match wins. If no rule matches, the element's tag-based category applies.

Config addition:
```json
{
  "rules": [
    {
      "tag": "pre",
      "parent": "datafile",
      "without_attr": "source",
      "treat_as": "code"
    }
  ]
}
```

Implementation: at the top of `serialize_element`, check if any rule matches the
current element. If so, override which renderer is used. Keep rules simple —
support `tag`, `parent`, `has_attr`, `without_attr`, and `treat_as`.

### 4. Add formatter plugins

Add a config key `"formatters"` mapping element categories or tag names to an
external command. The command receives code on stdin and returns formatted code
on stdout.

Config addition:
```json
{
  "formatters": {
    "code": ["java", "-jar", "google-java-format.jar", "-a", "-"]
  }
}
```

Add a CLI flag `-f` / `--format` to enable formatting (off by default, matching
ptx behavior). When enabled, `render_verbatim_text` pipes text through the
configured command before rendering. On non-zero exit, fall back to unformatted
text and warn on stderr.

This is strictly more general than ptx's approach since different element types
could use different formatters.

### 5. Restore dedentation parameter on `render_verbatim_text`

Rename `render_code` back to `render_verbatim_text` and add the optional
`dedentation` parameter (defaulting to `None` for full dedent). This is needed
by compound code elements (item 2) and makes the function match ptx's version.

## Resulting config schema

```json
{
  "indent": 2,
  "width": 80,
  "inline": ["term", "url", "c", "em", "xref", "m"],
  "code": ["program"],
  "preserve_whitespace": ["cline", "pre"],
  "one_line": ["cline"],
  "compact": ["cell", "idx", "premise"],
  "compound_code": {
    "program": {
      "code_children": ["preamble", "code", "postamble"]
    }
  },
  "rules": [
    {
      "tag": "pre",
      "parent": "datafile",
      "without_attr": "source",
      "treat_as": "code"
    }
  ],
  "formatters": {
    "code": ["java", "-jar", "google-java-format.jar", "-a", "-"]
  }
}
```

With this config, `format-xml.py -f -c pretext.json file.ptx` would produce
identical output to `format-ptx.py -f file.ptx`.

## Implementation order

1. `one_line` — smallest change, self-contained.
2. Dedentation parameter on `render_verbatim_text` — needed by step 3.
3. Compound code elements — uses dedentation parameter.
4. Conditional rules — independent of 1–3.
5. Formatter plugins — independent of 1–4.

Each step can be committed and tested independently.

## Verification

- Write a `pretext.json` config covering all ptx tag sets and rules.
- Run both formatters on the same PreTeXt file and diff the output.
- Run `format-xml.py` on `ced-mcqs.xml` with `ced-mcqs-format.json` to confirm
  no regressions.
