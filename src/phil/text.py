"""Text as Phil shows it: agent, command and rule text is cleaned before it reaches a terminal."""

import unicodedata

# Characters that hide, reorder or break up text (N1): controls including tab (Cc), format and
# bidi controls (Cf), line and paragraph separators (Zl, Zp), private-use (Co), unassigned (Cn)
# and lone surrogates (Cs). No rule may hold one, and Phil shows each as `?`.
HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cn", "Cs"})


def printable(text: object) -> str:
    """`text` for display: each hidden character (controls such as ESC and tab, bidi and other
    format characters, line separators...) becomes `?`, so an agent's command can't recolour the
    terminal, move its cursor, reorder its text or fake a new line. Only what's shown changes;
    saved rules and approved commands keep their text."""
    return "".join("?" if unicodedata.category(char) in HIDDEN_CATEGORIES else char for char in str(text))
