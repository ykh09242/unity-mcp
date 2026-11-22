"""Local lexical cues for finding an unknown Editor menu path."""

import re


# Pair a menu expression with a location or declaration cue in either order.
# Connectors admit particles and inflections without treating a file path elsewhere
# in the sentence as a request for a menu. A window or command name is never required.
_ARABIC_MENU = r"(?<!\w)(?:[وف]?(?:لل|[بكل]?ال|[بكل])?)?(?:قائمة|قوائم)\b"
_SPANISH_MENU = r"\bmen[úu](?:s|es)?\b"
_NAME_GAP = r"(?:\s+[\w-]+){0,4}\s+"
_CUES = (
    (
        "메뉴",
        r"(?:\s*(?:에서|에는|으로|의|에|가|는|로|된|어느|전체)){0,3}\s*",
        r"경로|위치|어디|어느|어떤|(?:등록|선언)(?=돼|됐|되|된|처|\s*(?:위치|경로))",
    ),
    (
        "メニュー",
        r"(?:\s*(?:は|の|に|で|が|を|どの|完全な|内の|された)){0,4}\s*",
        r"フルパス|パス|場所|位置|階層|どこ|どの|(?:登録|宣言)(?=され|済|先|場所|位置)",
    ),
    (
        _SPANISH_MENU,
        r"(?:\s+(?:complet[ao]s?|está|de|del|en|el|la|los|las|este)){0,4}\s+",
        r"\b(?:rutas?|ubicaci[oó]n|jerarqu[ií]a|d[oó]nde|qué|cuál|registrad[oa]s?|declarad[oa]s?)\b",
    ),
    # A name can intervene in "registered <command name> in the menu".
    (rf"\ben (?:el|este) {_SPANISH_MENU}", _NAME_GAP, r"\b(?:registrad[oa]s?|declarad[oa]s?)\b"),
    (
        _ARABIC_MENU
        + r"(?=\s*(?:$|[؟،.?!]|التي\s+(?:يعلن|يسجل)|ل(?:ل?نافذة|أمر)|(?:هذا\s+)?المحرر\b))",
        r"(?:\s+(?:الكامل|كامل|في|ضمن|هو|أي)){0,3}\s+",
        r"\b(?:المسار|مسار|موقع|أين|أي)\b",
    ),
    # The qualified domain distinguishes an Editor menu from an ordinary list.
    (
        _ARABIC_MENU + r"\s+(?:هذا\s+)?(?:المحرر|الأدوات|النوافذ|unity|يونيتي)\b",
        _NAME_GAP,
        r"\b(?:أين|المسار|مسار|سجلت|مسجل[ةه]?|توجد|يوجد|تقع|يقع)\b",
    ),
    (
        r"\bmenü\w*\b",
        r"(?:\s+(?:tam|hangi|içindeki|olan)){0,3}\s+",
        r"\b(?:yol(?:u\w*|lar\w*)?|konum\w*|nerede|hangi|kay[ıi]tl[ıi]|tan[ıi]ml[ıi])\b",
    ),
)
_PAIRS = tuple(
    re.compile(f"({first}){connector}({second})")
    for menu, connector, lookup in _CUES
    for first, second in ((menu, lookup), (lookup, menu))
)
_ARABIC_MARKS = str.maketrans(
    "", "", "\u0640\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670"
)
_ASSET_REFERENCE = re.compile(
    r"\b(?:assets|packages)/[\w ./-]*?(?:\.\w+(?![\w./-])"
    r"|(?=\s+(?:폴더|フォルダ|folder\b|directory\b|carpeta\b|مجلد|klasör)))"
)
_LITERAL_PATH = re.compile(r"\b[^\W\d_][\w.-]*[ \t]*[/›»>][ \t]*[\w.-]+")
_GAME_UI = re.compile(
    r"게임\s*(?:내|속)|ゲーム(?:内|画面)|\b(?:del|en el) juego\b|(?:في|داخل) اللعبة\b"
    r"|\boyun(?:daki|\s+içi\w*)\b"
)
_EDITOR_MENU = re.compile(
    r"(?:에디터|편집기)(?:의)?\s*메뉴|エディター?のメニュー|\bmen[úu] (?:del|de este) editor\b"
    r"|(?:قائمة|قوائم) (?:هذا )?المحرر\b|\b(?:editör|düzenleyici)\w*\s+menü|\beditor menu\b"
)


def localized_menu_path_terms(query: str) -> set[str]:
    """Return the two matched cues in a normalized query, preserving the caller's input."""
    lookup = _ASSET_REFERENCE.sub(" ", query)
    lookup = lookup.translate(_ARABIC_MARKS).replace("i\u0307", "i")
    if _LITERAL_PATH.search(lookup) or (
        _GAME_UI.search(lookup) and not _EDITOR_MENU.search(lookup)
    ):
        return set()
    for pair in _PAIRS:
        if match := pair.search(lookup):
            return set(match.groups())
    return set()
