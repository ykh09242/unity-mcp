"""Lexical evidence for two live Editor reads, using terms from the caller's query."""

import re


_ARABIC_MARKS = str.maketrans(
    "", "", "\u0640\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670"
)


def lookup_text(query: str) -> str:
    """Fold optional Arabic marks and the decomposed Turkish dotted i for lookup."""
    return query.translate(_ARABIC_MARKS).replace("i\u0307", "i")


def _rules(*rows: tuple[str, str, str]) -> tuple[tuple[re.Pattern, ...], ...]:
    return tuple(tuple(re.compile(pattern) for pattern in row) for row in rows)


# Domain, requested value, and read/rank cue must agree within one language.
_CONSOLE = _rules(
    (
        r"콘솔|console",
        r"오류|에러",
        r"지금|현재|어떤|뭐|메시지|읽어|읽고|확인|보여|알려|목록|그대로",
    ),
    (
        r"コンソール|console",
        r"エラー",
        r"今|現在|どんな|どの|メッセージ|読[んみむ]|確認|見せ|一覧|表示",
    ),
    (
        r"\bconsola\b|console",
        r"\berror(?:es)?\b",
        r"\b(?:actual\w*|ahora|qu[ée]|mensajes?|lee|lea|leer|enumera\w*|muestra\w*|mu[ée]str\w*)\b",
    ),
    (
        r"(?:وحدة|وحده)\s+(?:ال)?تحكم|(?:ال)?كونسول|console",
        r"أخطاء|اخطاء|خطأ|خطا",
        r"الآن|الان|الحالي|الرسائل|رسائل|اقرأ|اقرا|اعرض|أظهر|تظهر|المعروض",
    ),
    (
        r"\bkonsol\w*|console",
        r"\bhata\w*",
        r"\b(?:şu an(?:da|ki)?|şimdi|güncel|hangi|aynen|(?:oku|mesaj|ileti|aktar|göster|listele|görü|sırala|paylaş|bildir)\w*)\b",
    ),
)
_CAMERA = _rules(
    (r"카메라|camera", r"깊이|뎁스|depth", r"가장|제일|최[대고]|높은|큰\s*순"),
    (r"カメラ|camera", r"深度|深さ|デプス|depth", r"最大|最高|最も|一番|いちばん|降順"),
    (
        r"\bc[áa]maras?\b|camera",
        r"\b(?:profundidad|depth)\b",
        r"\b(?:mayor|m[áa]xim[oa]|m[áa]s (?:alt[oa]|grande)|descendente)\b",
    ),
    (r"كامير[اة]|كاميرا|camera", r"عمق|depth", r"أعلى|اعلى|الأعلى|أكبر|اكبر|الأكبر|الأقصى|تنازلي"),
    (
        r"\bkamera\w*|camera",
        r"\bderinli[ğk]\w*|\bdepth\b",
        r"\b(?:en (?:yüksek|büyük|fazla)|maksimum|azalan)\b",
    ),
)
_CONSOLE_CHANGE = re.compile(
    r"지워|지우세요|비워|고쳐|(?:삭제|수정|해결|초기화)(?:해|하(?!지)|할|시켜)|(?:消去|削除|クリア|修正)して|消して|直して|"
    r"(?<!no )\b(?:borra|borre|limpia|limpie|elimina|corrige|arregla)\w*\b|"
    r"(?<!\w)(?:امسح|احذف|أصلح|اصلح)\b|\b(?:sil|silin|temizle|temizleyin|düzelt|düzeltin)\b"
    r"|\b(?:siler|silebilir|temizler|temizleyebilir|düzeltir|düzeltebilir)\s+mi(?:sin(?:iz)?)?\b"
    r"|\bdosya(?:ya|sına|lara|larına)\s+(?:yaz|aktar|kaydet)\w*"
)
_OTHER_CONSOLE = re.compile(
    r"브라우저|터미널|ブラウザ|ターミナル|\b(?:browser|devtools|navegador|terminal|shell|tarayıcı)\w*"
    r"|متصفح|الطرفية"
)
_CAMERA_CHANGE = re.compile(
    r"설정해|설정하(?!지)|바꿔|변경해|맞춰|올려|낮춰|(?:設定|変更)して|にして|上げて|下げて|"
    r"(?<!no )\b(?:pon|pongan?|fij[ae]|cambi[ae]|ajust[ae]|establece|asign[ae]|configur[ae])(?:n|d|r|lo|la)?\b|"
    r"(?<!\w)(?:اضبط|غيّر|غير|عدّل|عدل|ارفع)\b|\b(?:ayarla|ayarlayın|değiştir|değiştirin|yükselt)\b"
)
_OTHER_DEPTH = re.compile(
    r"텍스처|버퍼|색\s*심도|被写界深度|テクスチャ|バッファ|"
    r"\b(?:texture|textura|buffer|búfer|bit depth|profundidad de (?:bits?|campo|color))\b|"
    r"نسيج|مخزن العمق|عمق (?:المجال|اللون)|\b(?:doku|tampon)\w*|\balan derinli"
)


def _matched(query: str, rules: tuple[tuple[re.Pattern, ...], ...]) -> set[str]:
    for row in rules:
        matches = [pattern.search(query) for pattern in row]
        if all(matches):
            return {match[0] for match in matches if match}
    return set()


def localized_read_terms(query: str) -> tuple[set[str], set[str]]:
    """Return console-error and camera-depth evidence, in that order."""
    text = lookup_text(query)
    console = (
        set()
        if _CONSOLE_CHANGE.search(text) or _OTHER_CONSOLE.search(text)
        else _matched(text, _CONSOLE)
    )
    camera = (
        set()
        if _CAMERA_CHANGE.search(text) or _OTHER_DEPTH.search(text)
        else _matched(text, _CAMERA)
    )
    return console, camera
