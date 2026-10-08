"""Phil's mascot for the welcome banner (spec 2026-10-08 §3.3). A placeholder until the final
mascot is chosen: swap it by editing MASCOT only. Each row is (style, text) fragments; rows may
differ in width — the banner pads them to the widest."""

MASCOT: tuple[tuple[tuple[str, str], ...], ...] = (
    (("phil.brand", "╭─────╮"),),
    (("phil.brand", "│ "), ("phil.warn", "◠ ◠"), ("phil.brand", " │")),
    (("phil.brand", "│  "), ("phil.warn", "◡"), ("phil.brand", "  │")),
    (("phil.brand", "╰──┬──╯"),),
)
