"""`split_frontmatter` — the parser every measured number depends on.

If frontmatter parsing is wrong, the description is wrong, and the always-on
number is wrong. These tests pin the behaviour and, where the deliberate YAML
subset disagrees with PyYAML, they pin the disagreement so a new one is caught.
"""

import unittest

import _sift  # noqa: F401  (puts src/ on the path)
from audit import split_frontmatter

try:
    import yaml
except ImportError:  # PyYAML is not a dependency of sift; the comparison is optional
    yaml = None


def fm(text):
    return split_frontmatter(text)[0]


class TestBasicShapes(unittest.TestCase):
    def test_plain_key_value_pairs_parse(self):
        """The ordinary `key: value` frontmatter every SKILL.md uses."""
        d, body, ok = split_frontmatter("---\nname: alpha\ndescription: Does a thing.\n---\nBODY\n")
        self.assertTrue(ok)
        self.assertEqual(d, {"name": "alpha", "description": "Does a thing."})
        self.assertEqual(body, "BODY\n")

    def test_no_frontmatter_returns_ok_false_and_the_whole_text_as_body(self):
        """A file with no frontmatter is reported, not silently treated as empty."""
        d, body, ok = split_frontmatter("# Just a heading\n")
        self.assertFalse(ok)
        self.assertEqual(d, {})
        self.assertEqual(body, "# Just a heading\n")

    def test_unterminated_frontmatter_is_not_parsed(self):
        """An opening `---` with no closing `---` is not frontmatter; nothing is invented."""
        text = "---\nname: alpha\ndescription: never closed\n"
        d, body, ok = split_frontmatter(text)
        self.assertFalse(ok)
        self.assertEqual(d, {})
        self.assertEqual(body, text)

    def test_crlf_line_endings_parse_identically_to_lf(self):
        """Windows-authored SKILL.md files measure the same as Unix ones."""
        lf = "---\nname: alpha\ndescription: Windows safe.\n---\nBODY\n"
        crlf = lf.replace("\n", "\r\n")
        self.assertEqual(fm(crlf), fm(lf))
        self.assertNotIn("\r", fm(crlf)["description"])

    def test_empty_description_is_an_empty_string_not_a_missing_key(self):
        """`description:` with no value yields '', so check_description can flag it."""
        d = fm("---\nname: alpha\ndescription:\n---\n")
        self.assertEqual(d["description"], "")

    def test_unicode_survives_intact(self):
        """Non-ASCII descriptions are not mangled, so their char count stays honest."""
        desc = "使用時 — émoji ✅ résumé naïve"
        self.assertEqual(fm(f"---\ndescription: {desc}\n---\n")["description"], desc)

    def test_quoted_values_are_unquoted(self):
        """Quotes are stripped so a quoted description is not measured two chars long."""
        self.assertEqual(fm('---\ndescription: "Use when: it has a colon"\n---\n')["description"],
                         "Use when: it has a colon")
        self.assertEqual(fm("---\ndescription: 'single quoted'\n---\n")["description"],
                         "single quoted")

    def test_continuation_lines_join_with_a_space(self):
        """An indented wrapped value is one description, not a truncated one."""
        self.assertEqual(fm("---\ndescription: starts here\n  and continues\n---\n")["description"],
                         "starts here and continues")

    def test_literal_and_folded_blocks_capture_every_line(self):
        """`|` and `>` blocks keep all their content, so long descriptions are fully costed."""
        for marker in ("|", ">", "|-", ">-", "|+", ">+"):
            with self.subTest(marker=marker):
                d = fm(f"---\ndescription: {marker}\n  line one\n  line two\n---\n")
                self.assertIn("line one", d["description"])
                self.assertIn("line two", d["description"])

    def test_a_whole_line_yaml_comment_is_not_absorbed_into_the_previous_value(self):
        """A `# comment` line does not inflate the preceding key's measured size."""
        d = fm("---\nname: alpha\n# this comment is not part of the name\ndescription: b\n---\n")
        self.assertEqual(d["name"], "alpha")

    def test_a_hash_inside_a_description_is_kept(self):
        """`C#` and `issue #12` are content, not comments."""
        d = fm("---\ndescription: Use when the user writes C# or mentions issue #12\n---\n")
        self.assertIn("C#", d["description"])
        self.assertIn("#12", d["description"])

    def test_keys_with_hyphens_parse(self):
        """`allowed-tools` and friends are real frontmatter keys in the wild."""
        self.assertEqual(fm("---\nallowed-tools: Bash, Read\n---\n")["allowed-tools"], "Bash, Read")


# --------------------------------------------------------------------------
# Comparison against PyYAML. Divergences are findings, not failures: they are
# listed here so that a NEW divergence fails the suite while the known,
# accepted ones do not.
# --------------------------------------------------------------------------

CASES = {
    "plain":            "name: a\ndescription: Does a thing.",
    "folded":           "description: >\n  first line\n  second line",
    "folded_strip":     "description: >-\n  one\n  two",
    "literal":          "description: |\n  line one\n  line two\n",
    "literal_indented": "description: |\n  outer\n    inner",
    "double_quoted":    'description: "Use when: colons inside"',
    "single_quoted":    "description: 'it''s quoted'",
    "empty_desc":       "name: a\ndescription:",
    "unicode":          "description: 使用時 — émoji ✅ résumé",
    "continuation":     "description: starts here\n  and continues",
    "blank_line":       "name: a\n\ndescription: b",
    "comment_line":     "# a comment\nname: a",
    "comment_after":    "name: a\n# a trailing comment",
    "inline_comment":   "description: value # trailing comment",
    "list_value":       "name: a\ntags:\n  - x\n  - y",
    "nested_map":       "meta:\n  a: 1\n  b: 2",
    "trailing_ws":      "description: value   ",
    "hyphen_key":       "allowed-tools: Bash, Read",
}

# Documented, accepted disagreements with PyYAML, each with why it is tolerable
# for a *measurement* tool (the char count, not the exact string, is what we bill).
KNOWN_DIVERGENCES = {
    "folded":           "folded `>` is joined with newlines, not spaces; PyYAML also keeps a trailing newline",
    "folded_strip":     "folded `>-` is joined with newlines, not spaces",
    "literal":          "PyYAML keeps the block's trailing newline; we strip it (1 char)",
    "literal_indented": "we strip per-line indentation inside a block; PyYAML preserves it (undercounts deep blocks)",
    "single_quoted":    "we do not un-double '' inside single quotes (1 char per escaped quote)",
    "empty_desc":       "we return '' where PyYAML returns None — deliberate: '' is what check_description wants",
    "inline_comment":   "we keep inline `# ...` as content, since `C#`/`#12` are legitimate in descriptions",
    "list_value":       "sequences become a flattened string; we only consume scalar keys",
    "nested_map":       "nested mappings become a flattened string; we only consume top-level scalars",
}


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestAgainstPyYAML(unittest.TestCase):
    def test_the_set_of_divergences_from_pyyaml_is_exactly_the_documented_one(self):
        """Our YAML subset disagrees with PyYAML only in the ways we have written down."""
        found = {}
        for name, raw in CASES.items():
            ours = fm(f"---\n{raw}\n---\nBODY\n")
            try:
                theirs = yaml.safe_load(raw)
            except yaml.YAMLError:
                theirs = "<yaml-error>"
            if ours != theirs:
                found[name] = (ours, theirs)
        unexpected = sorted(set(found) - set(KNOWN_DIVERGENCES))
        self.assertEqual(unexpected, [], f"new divergence from PyYAML: "
                                         f"{ {k: found[k] for k in unexpected} }")
        gone = sorted(set(KNOWN_DIVERGENCES) - set(found))
        self.assertEqual(gone, [], f"documented divergence no longer reproduces (update the list): {gone}")

    def test_we_parse_a_document_pyyaml_rejects(self):
        """An unquoted URL breaks PyYAML but not us — the subset parser is more forgiving here."""
        raw = "description: see https://example.com/a: b"
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load(raw)
        self.assertIn("https://example.com", fm(f"---\n{raw}\n---\n")["description"])

    def test_divergences_never_move_the_measured_size_by_more_than_a_few_chars(self):
        """Where we disagree with PyYAML on a description, the token cost is still right."""
        from audit import estimate_tokens
        for name in ("folded", "folded_strip", "literal", "single_quoted"):
            with self.subTest(name=name):
                raw = CASES[name]
                ours = fm(f"---\n{raw}\n---\n")["description"]
                theirs = yaml.safe_load(raw)["description"]
                self.assertEqual(estimate_tokens(ours), estimate_tokens(theirs))


if __name__ == "__main__":
    unittest.main()
