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
    r"게임\s*(?:내|속|화면|일시\s*정지)|ゲーム(?:内|画面|のポーズ)|\b(?:del|en el) (?:video)?juego\b|(?:في|داخل) اللعبة\b"
    r"|\boyun(?:daki|\s+içi\w*)\b"
)
_EDITOR_MENU = re.compile(
    r"(?:에디터|편집기)(?:의)?\s*메뉴|エディター?のメニュー|\bmen[úu] (?:del|de este) editor\b"
    r"|(?:قائمة|قوائم) (?:هذا )?المحرر\b|\b(?:editör|düzenleyici)\w*\s+menü|\beditor menu\b"
)

# A window location can be requested without calling it a menu. When even the
# Editor/window is implicit, require both a way of opening it and a path-only cue.
_WINDOW_LOCATIONS = tuple(
    tuple(re.compile(pattern) for pattern in row)
    for row in (
        (
            r"에디터|편집기|유니티|unity|창(?:을|은|이|의|에|으|만|\s|$)",
            r"어디|어느\s*(?:곳|위치|경로|항목)|어떤\s*경로",
            r"여는|여나|엽(?:니까|니다)|열[려수어죠까]|열\s*수|찾[아을는]|접근|들어가|어디에?\s*있",
            r"(?:경로|위치|장소)\s*만",
        ),
        (
            r"エディター?|ウィンドウ|ウインドウ|パネル|unity",
            r"どこ|どちら|どの(?:場所|項目|階層|経路)",
            r"開[くきけい]|見つ[かけ]|アクセス|たど[るれ]|どこに(?:ある|あり)",
            r"(?:場所|位置|パス|経路|手順)(?:だけ|のみ)",
        ),
        (
            r"\b(?:editor|ventanas?|panel(?:es)?|unity)\b",
            r"\b(?:d[oó]nde|(?:qu[ée]|cu[áa]l) (?:es la )?(?:ruta|ubicaci[oó]n|parte|secci[oó]n|apartado))\b",
            r"\b(?:abr(?:ir|[eo])|acced\w*|enc(?:uentr|ontr)\w*|lleg\w*|est[áa])\b",
            r"\b(?:s[oó]lo|solamente|[úu]nicamente) (?:la |su )?(?:ruta|ubicaci[oó]n)\b",
        ),
        (
            r"المحرر|نافذة|النافذة|لوحة|يونيتي|unity",
            r"(?:أين|اين|أي\s+(?:قسم|مكان|مسار))",
            r"(?:أفتح|افتح|فتح|أجد|اجد|أصل|الوصول|العثور|توجد|يوجد)",
            r"(?:المسار|مساره[ا]?|المكان|الموقع)\s+(?:فقط|وحده)",
        ),
        (
            r"\b(?:editör|düzenleyici|pencere|panel|unity)\w*",
            r"\b(?:nere(?:de\w*|den|ye)|hangi (?:bölüm|yol|konum|sekme)\w*)\b",
            r"\b(?:aç[ıia]\w*|ulaş\w*|bul[ua]\w*|eriş\w*)\b",
            r"\b(?:yalnızca|sadece) (?:\w+ )?(?:yol|konum|yer)\w*\b",
        ),
    )
)
_OTHER_LOCATION = re.compile(
    r"(?<!프로)파일|폴더|프리팹|에셋|게임\s*오브젝트|웹\s*(?:주소|사이트)|"
    r"ファイル|フォルダ|ウェブ|プレハブ|アセット|ゲームオブジェクト|"
    r"(?<![a-z])(?:url|https?|prefabs?|gameobjects?)(?![a-z])|"
    r"\b(?:carpetas?|archivos?|sitio web|dosya\w*|klasör\w*)\b|(?:ملف|مجلد|الرابط)"
)
_MOVE_WINDOW = re.compile(
    r"옮[겨길기]|이동시|재배치|移動|移[すし]|ドラッグ|\b(?:mover|mueve|reubica|arrastra)\w*"
    r"|(?:انقل|أنقل|نقل)\b|\b(?:taşı|sürükle|yerleştir)\w*"
)
_WINDOW_NOUN = re.compile(
    r"창(?:을|은|이|의|에|\s|$)|ウ[ィイ]ンドウ|パネル|\b(?:ventana|panel|pencere)|نافذة|لوحة"
)


def localized_menu_path_terms(query: str) -> set[str]:
    """Return matched declaration or window-location cues from a normalized query."""
    lookup = _ASSET_REFERENCE.sub(" ", query)
    lookup = lookup.translate(_ARABIC_MARKS).replace("i\u0307", "i")
    if _LITERAL_PATH.search(lookup) or (
        _GAME_UI.search(lookup) and not _EDITOR_MENU.search(lookup)
    ):
        return set()
    for pair in _PAIRS:
        if match := pair.search(lookup):
            return set(match.groups())
    if not (
        _OTHER_LOCATION.search(lookup)
        or _ASSET_REFERENCE.search(query)
        or _MOVE_WINDOW.search(lookup)
    ):
        for domain, location, opening, path_only in _WINDOW_LOCATIONS:
            where, path = location.search(lookup), path_only.search(lookup)
            if not (where or path):
                continue
            context, access = domain.search(lookup), opening.search(lookup)
            if (context and (access or path or _WINDOW_NOUN.search(lookup))) or (path and access):
                return {match[0] for match in (where, path, context, access) if match}
    return set()
